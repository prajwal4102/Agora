"""
Agent — MCP Recipe

High-level API for managing Agora Conversational AI Agents with OpenAI Pipeline
or Realtime MCP tool calling. Agora cloud orchestrates the MCP server: the model
emits a tool call, Agora invokes the public MCP_ENDPOINT, returns the result,
and the model speaks it.

Pipeline mode is managed by default and optionally accepts BYO OpenAI credentials.
OPENAI_REALTIME_API_KEY is required only for Realtime mode.
MCP_ENDPOINT must be PUBLIC — Agora cloud (not this server) calls it.
"""
import logging
import os
from typing import Any, Dict, Literal, Optional

from agora_agent import Area, AsyncAgora
from agora_agent.agentkit import Agent as AgoraAgent
from agora_agent.agentkit.vendors import (
    DeepgramSTT,
    MurfTTS,
    OpenAI,
    OpenAIRealtime,
)
from mcp_config import build_mcp_servers
from prompts import greeting_for, prompt_for

logger = logging.getLogger("uvicorn.error")

DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1/chat/completions"
DEFAULT_MURF_BASE_URL = "wss://global.api.murf.ai/v1/speech/stream-input"
AgentMode = Literal["pipeline", "realtime"]


class Agent:
    """
    High-level wrapper for an Agora agent with Pipeline or Realtime MCP tools.

    When the user asks what time it is, the selected model emits a tool call,
    Agora invokes the mcp/ server at MCP_ENDPOINT, and the result is returned
    to the model so it can speak the answer.

    IMPORTANT: MCP_ENDPOINT must be publicly accessible for the Agora
    Conversational AI Engine (cloud) to reach the mcp/ server. For local
    development, use a tunnel (ngrok) — e.g. ngrok http 8000 — and paste
    the public URL here.
    """

    def __init__(self):
        self.app_id = os.getenv("AGORA_APP_ID")
        self.app_certificate = os.getenv("AGORA_APP_CERTIFICATE")

        # Pipeline is managed unless BYO credentials are supplied.
        self.openai_api_key = os.getenv("OPENAI_API_KEY")
        self.openai_base_url = os.getenv("OPENAI_BASE_URL")
        self.openai_model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        self.openai_realtime_api_key = os.getenv("OPENAI_REALTIME_API_KEY")
        self.openai_realtime_model = os.getenv(
            "OPENAI_REALTIME_MODEL", "gpt-realtime"
        )
        # MCP_ENDPOINT must be PUBLIC — Agora cloud calls the mcp/ server directly.
        self.mcp_endpoint = os.getenv("MCP_ENDPOINT")
        if not self.mcp_endpoint:
            raise ValueError(
                "MCP_ENDPOINT is required (public URL of your mcp/ server, "
                "e.g. https://<tunnel>/mcp)"
            )

        if not self.app_id or not self.app_certificate:
            raise ValueError("AGORA_APP_ID and AGORA_APP_CERTIFICATE are required")

        self.client = AsyncAgora(
            area=Area.US,
            app_id=self.app_id,
            app_certificate=self.app_certificate,
        )

        # Voice: Murf Falcon is the agent's voice. STT language "multi" lets
        # Deepgram follow Hindi-English code switching; "en" is English only.
        self.murf_api_key = os.getenv("MURF_API_KEY")
        self.murf_voice_id = os.getenv("MURF_VOICE_ID", "Anisha")
        self.murf_locale = os.getenv("MURF_LOCALE", "en-IN")
        self.murf_base_url = os.getenv("MURF_BASE_URL", DEFAULT_MURF_BASE_URL)
        self.stt_language = os.getenv("STT_LANGUAGE", "en")
        # The employee is spoken to in Hinglish: "multi" lets Deepgram follow
        # Hindi-English switching, and the same Murf voice speaks hi-IN.
        self.role_voice = {
            "supervisor": {"stt": self.stt_language, "locale": self.murf_locale},
            "employee": {
                "stt": os.getenv("EMPLOYEE_STT_LANGUAGE", "multi"),
                "locale": os.getenv("EMPLOYEE_MURF_LOCALE", "hi-IN"),
            },
        }
        if not self.murf_api_key:
            raise ValueError("MURF_API_KEY is required (Murf Falcon is the agent voice)")

        # Agents saved in Agora Agent Studio, by role. When one is set, calls for
        # that role start from the Studio agent (its instructions, voice and
        # tools live in Agora's console) and only the opening line is sent from
        # here. Left empty, the agent is built from this file as before.
        self.studio_agents = {
            "supervisor": (os.getenv("STUDIO_PROCUREMENT_AGENT_ID") or "").strip() or None,
            "employee": (os.getenv("STUDIO_FLOOR_AGENT_ID") or "").strip() or None,
        }
        self._studio_auth: Dict[str, Any] = {}

        # Track active sessions by agent_id
        self._sessions: Dict[str, Any] = {}

    async def _start_from_studio(
        self, pipeline_id: str, channel_name: str, agent_uid: int, user_uid: int, role: str, opening: str
    ) -> Dict[str, Any]:
        """Start a call from an agent saved in Agent Studio. Agora uses the saved
        agent as the base and applies what is sent here on top, so the only
        thing sent is what changes per call: the opening line."""
        from agora_agent.agentkit.token import generate_convo_ai_token

        token = generate_convo_ai_token(
            app_id=self.app_id,
            app_certificate=self.app_certificate,
            channel_name=channel_name,
            uid=agent_uid,
            token_expire=3600,
        )
        properties: Dict[str, Any] = {
            "channel": channel_name,
            "token": token,
            "agent_rtc_uid": str(agent_uid),
            "remote_rtc_uids": [str(user_uid)],
            "enable_string_uid": False,
            "idle_timeout": 30,
            "advanced_features": {"enable_rtm": True, "enable_tools": True},
            "parameters": {
                "audio_scenario": "chorus",
                "data_channel": "rtm",
                "enable_error_message": True,
                "enable_metrics": True,
            },
            "llm": {"greeting_message": opening},
        }
        auth = {"additional_headers": {"Authorization": f"agora token={token}"}}
        logger.info("Starting Studio agent role=%s pipeline_id=%s channel=%s", role, pipeline_id, channel_name)
        response = await self.client.agents.start(
            self.app_id,
            name=f"{role}-{channel_name}",
            properties=properties,
            pipeline_id=pipeline_id,
            request_options=auth,
        )
        agent_id = getattr(response, "agent_id", None) or ""
        self._studio_auth[agent_id] = auth
        return {
            "agent_id": agent_id,
            "channel_name": channel_name,
            "status": "started",
            "agent_mode": "pipeline",
            "source": "studio",
        }

    def _build_tts(self, locale: Optional[str] = None) -> MurfTTS:
        """Murf Falcon streaming TTS. An empty MURF_LOCALE lets Murf detect the
        language from the text, which is what mixed Hindi-English needs."""
        options: Dict[str, Any] = {
            "key": self.murf_api_key,
            "base_url": self.murf_base_url,
            "voice_id": self.murf_voice_id,
            "model": "FALCON",
            "sample_rate": 24000,
            "rate": 0,
            "pitch": 0,
        }
        locale = self.murf_locale if locale is None else locale
        if locale:
            options["locale"] = locale
        return MurfTTS(**options)

    def build_pipeline_agent(self, role: str, greeting: Optional[str] = None, for_phone: bool = False):
        """An agent (STT -> LLM with shop tools -> Murf) briefed for one role.
        `for_phone` drops the web-only extras: a phone has no data channel for
        transcripts, and phone audio is not the low-latency web profile."""
        opening = greeting_for(role, greeting)
        voice = self.role_voice.get(role, self.role_voice["supervisor"])
        parameters: Dict[str, Any] = {"enable_error_message": True, "enable_metrics": True}
        if not for_phone:
            parameters.update({"audio_scenario": "chorus", "data_channel": "rtm"})
        llm_options: Dict[str, Any] = {
            "model": self.openai_model,
            "system_messages": [{"role": "system", "content": prompt_for(role)}],
            "mcp_servers": build_mcp_servers(self.mcp_endpoint),
            "greeting_message": opening,
        }
        if self.openai_api_key:
            llm_options["api_key"] = self.openai_api_key
            llm_options["base_url"] = self.openai_base_url or DEFAULT_OPENAI_BASE_URL
        return (
            AgoraAgent(
                client=self.client,
                greeting=opening,
                failure_message="Please wait a moment.",
                max_history=50,
                advanced_features={"enable_rtm": not for_phone},
                parameters=parameters,
                turn_detection={
                    "config": {
                        "speech_threshold": 0.5,
                        "start_of_speech": {
                            "mode": "vad",
                            "vad_config": {"interrupt_duration_ms": 160, "prefix_padding_ms": 300},
                        },
                        "end_of_speech": {"mode": "vad", "vad_config": {"silence_duration_ms": 480}},
                    },
                },
            )
            .with_stt(DeepgramSTT(model="nova-3", language=voice["stt"]))
            .with_llm(OpenAI(**llm_options))
            .with_tts(self._build_tts(voice["locale"]))
            .with_tools()
        )

    async def start(
        self,
        channel_name: str,
        agent_uid: int,
        user_uid: int,
        output_audio_codec: Optional[str] = None,
        agent_mode: AgentMode = "pipeline",
        role: str = "supervisor",
        greeting: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Start a Pipeline or Realtime agent with MCP tool calling.

        `role` picks who the agent is talking to (and so its instructions);
        `greeting` overrides the opening line, e.g. for a call the agent places
        about a specific low product."""
        instructions = prompt_for(role)
        opening = greeting_for(role, greeting)
        voice = self.role_voice.get(role, self.role_voice["supervisor"])
        if not channel_name or not str(channel_name).strip():
            raise ValueError("channel_name is required and cannot be empty")
        if agent_uid <= 0:
            raise ValueError("agent_uid is required and cannot be empty")
        if user_uid <= 0:
            raise ValueError("user_uid is required and cannot be empty")
        if agent_mode not in ("pipeline", "realtime"):
            raise ValueError("agent_mode must be 'pipeline' or 'realtime'")
        if agent_mode == "realtime" and not self.openai_realtime_api_key:
            raise ValueError("OPENAI_REALTIME_API_KEY is required for realtime mode")

        studio_agent = self.studio_agents.get(role)
        if studio_agent and agent_mode == "pipeline":
            return await self._start_from_studio(
                studio_agent, channel_name, agent_uid, user_uid, role, opening
            )

        mcp_servers = build_mcp_servers(self.mcp_endpoint)

        parameters = {
            "audio_scenario": "chorus",  # web client — ultra-low-latency chorus profile
            "data_channel": "rtm",
            "enable_error_message": True,
            "enable_metrics": True,
        }
        if isinstance(output_audio_codec, str) and output_audio_codec.strip():
            parameters["output_audio_codec"] = output_audio_codec.strip()

        agent_options = {
            "client": self.client,
            "greeting": opening,
            "failure_message": "Please wait a moment.",
            "max_history": 50,
            "advanced_features": {"enable_rtm": True},
            "parameters": parameters,
        }
        if agent_mode == "pipeline":
            agent_options["turn_detection"] = {
                "config": {
                    "speech_threshold": 0.5,
                    "start_of_speech": {
                        "mode": "vad",
                        "vad_config": {
                            "interrupt_duration_ms": 160,
                            "prefix_padding_ms": 300,
                        },
                    },
                    "end_of_speech": {
                        "mode": "vad",
                        "vad_config": {
                            "silence_duration_ms": 480,
                        },
                    },
                },
            }
        agora_agent = AgoraAgent(**agent_options)

        if agent_mode == "pipeline":
            llm_options = {
                "model": self.openai_model,
                "system_messages": [{"role": "system", "content": instructions}],
                "mcp_servers": mcp_servers,
                "greeting_message": opening,
            }
            if self.openai_api_key:
                llm_options["api_key"] = self.openai_api_key
                llm_options["base_url"] = (
                    self.openai_base_url or DEFAULT_OPENAI_BASE_URL
                )
            llm = OpenAI(**llm_options)
            stt = DeepgramSTT(model="nova-3", language=voice["stt"])
            tts = self._build_tts(voice["locale"])
            agora_agent = (
                agora_agent.with_stt(stt).with_llm(llm).with_tts(tts).with_tools()
            )
        else:
            mllm = OpenAIRealtime(
                api_key=self.openai_realtime_api_key,
                model=self.openai_realtime_model,
                instructions=instructions,
                greeting_message=opening,
                failure_message="Please wait a moment.",
                turn_detection={"mode": "server_vad"},
                mcp_servers=mcp_servers,
            )
            agora_agent = agora_agent.with_mllm(mllm).with_tools()

        session = agora_agent.create_async_session(
            channel=channel_name,
            agent_uid=str(agent_uid),
            remote_uids=[str(user_uid)],
            enable_string_uid=False,
            idle_timeout=30,
            expires_in=3600,
        )

        logger.info(
            "Starting MCP agent role=%s channel=%s agent_uid=%s user_uid=%s mode=%s mcp_endpoint=%s",
            role,
            channel_name,
            agent_uid,
            user_uid,
            agent_mode,
            self.mcp_endpoint,
        )

        try:
            agent_id = await session.start()
        except Exception:
            logger.exception(
                "Failed to start MCP agent channel=%s agent_uid=%s user_uid=%s",
                channel_name,
                agent_uid,
                user_uid,
            )
            raise

        # Save session for later stop
        self._sessions[agent_id] = session

        logger.info(
            "Started MCP agent agent_id=%s channel=%s",
            agent_id,
            channel_name,
        )

        return {
            "agent_id": agent_id,
            "channel_name": channel_name,
            "status": "started",
            "agent_mode": agent_mode,
        }

    async def stop(self, agent_id: str) -> None:
        """Stop a running agent. Falls back to the stateless client path."""
        if not agent_id or not str(agent_id).strip():
            raise ValueError("agent_id is required and cannot be empty")

        studio_auth = self._studio_auth.pop(agent_id, None)
        if studio_auth is not None:
            await self.client.agents.stop(self.app_id, agent_id, request_options=studio_auth)
            logger.info("Stopped Studio agent agent_id=%s", agent_id)
            return

        session = self._sessions.pop(agent_id, None)
        if session:
            try:
                await session.stop()
                logger.info("Stopped agent from active session agent_id=%s", agent_id)
                return
            except Exception:
                logger.warning(
                    "Failed to stop agent from active session; falling back agent_id=%s",
                    agent_id,
                    exc_info=True,
                )

        logger.info("Stopping agent through client.stop_agent agent_id=%s", agent_id)
        await self.client.stop_agent(agent_id)
