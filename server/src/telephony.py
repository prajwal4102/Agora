"""Real phone calls through a Twilio SIP trunk.

With CALL_MODE=phone the agent dials the employee's or supervisor's mobile
instead of ringing the web phone page. Agora places the call through the
trunk: the number is imported into Agora once, then each call starts an agent
that waits for the person to pick up.

Everything here is optional. With CALL_MODE=app, or any setting missing, the
web phone page is used and nothing in this module runs.
"""
import asyncio
import logging
import os
import random
import time
from typing import Any, Dict, Optional

logger = logging.getLogger("uvicorn.error")

POLL_SECONDS = 3

# Auth headers per placed call, so its status can be read later.
_call_auth: Dict[str, Any] = {}
MAX_CALL_SECONDS = 10 * 60


def settings() -> Dict[str, Optional[str]]:
    return {
        "mode": (os.getenv("CALL_MODE") or "app").strip().lower(),
        "from_number": (os.getenv("TWILIO_FROM_NUMBER") or "").strip() or None,
        "sip_address": (os.getenv("TWILIO_SIP_ADDRESS") or "").strip() or None,
        "sip_username": (os.getenv("TWILIO_SIP_USERNAME") or "").strip() or None,
        "sip_password": (os.getenv("TWILIO_SIP_PASSWORD") or "").strip() or None,
        "transport": (os.getenv("TWILIO_SIP_TRANSPORT") or "udp").strip().lower(),
        "employee": (os.getenv("EMPLOYEE_PHONE") or "").strip() or None,
        "supervisor": (os.getenv("SUPERVISOR_PHONE") or "").strip() or None,
    }


def missing_settings() -> list:
    cfg = settings()
    needed = {
        "TWILIO_FROM_NUMBER": cfg["from_number"],
        "TWILIO_SIP_ADDRESS": cfg["sip_address"],
        "EMPLOYEE_PHONE": cfg["employee"],
        "SUPERVISOR_PHONE": cfg["supervisor"],
    }
    return [name for name, value in needed.items() if not value]


def number_for(role: str) -> Optional[str]:
    """The mobile to dial for this role, or None when real calls are off."""
    cfg = settings()
    if cfg["mode"] != "phone" or missing_settings():
        return None
    return cfg.get(role)


def _mask(number: Optional[str]) -> str:
    return f"…{number[-4:]}" if number and len(number) > 4 else "(none)"


def _sip_address(cfg: Dict[str, Optional[str]]) -> str:
    host = (cfg["sip_address"] or "").replace("sip:", "")
    if cfg["sip_username"] and cfg["sip_password"]:
        return f"{cfg['sip_username']}:{cfg['sip_password']}@{host}"
    return host


async def ensure_number_imported(client: Any) -> str:
    """Import the Twilio number into Agora if it is not there yet. Returns a
    short status for logs and the status endpoint."""
    from agora_agent.phone_numbers.types.add_phone_numbers_request_inbound_config import (
        AddPhoneNumbersRequestInboundConfig,
    )
    from agora_agent.phone_numbers.types.add_phone_numbers_request_outbound_config import (
        AddPhoneNumbersRequestOutboundConfig,
    )

    cfg = settings()
    existing = await client.phone_numbers.list()
    for item in existing or []:
        if getattr(item, "phone_number", None) == cfg["from_number"]:
            return "already imported"
    await client.phone_numbers.add(
        provider="byo",
        phone_number=cfg["from_number"],
        label="Shelf agent",
        inbound=False,
        outbound=True,
        inbound_config=AddPhoneNumbersRequestInboundConfig(allowed_addresses=[]),
        outbound_config=AddPhoneNumbersRequestOutboundConfig(
            address=_sip_address(cfg), transport=cfg["transport"]
        ),
    )
    return "imported"


async def dial(agent: Any, role: str, greeting: str, to_number: str) -> Dict[str, Any]:
    """Call a mobile and put an agent, briefed for this role, on the line."""
    from agora_agent.agentkit.presets import resolve_session_presets
    from agora_agent.agentkit.token import generate_convo_ai_token
    from agora_agent.telephony.types.call_telephony_request_properties import CallTelephonyRequestProperties
    from agora_agent.telephony.types.call_telephony_request_sip import CallTelephonyRequestSip

    cfg = settings()
    await ensure_number_imported(agent.client)

    channel = f"pstn-{int(time.time())}-{random.randint(1000, 9999)}"
    agent_uid = random.randint(10_000_000, 99_999_999)
    sip_uid = random.randint(1000, 9_999_999)

    session = agent.build_pipeline_agent(role, greeting, for_phone=True).create_async_session(
        channel=channel,
        agent_uid=str(agent_uid),
        remote_uids=[str(sip_uid)],
        enable_string_uid=False,
        idle_timeout=30,
        expires_in=3600,
    )
    # Reuse the SDK's own assembly of the agent settings, then hand them to the
    # telephony endpoint instead of the normal "join a channel" one.
    skip, allow_missing = session._vendor_validation_categories(None)  # noqa: SLF001
    properties = session._build_start_properties(  # noqa: SLF001
        {"app_id": agent.app_id, "app_certificate": agent.app_certificate, "expires_in": 3600},
        skip_vendor_validation_categories=skip,
        allow_missing_vendor_categories=allow_missing,
    )
    preset, properties = resolve_session_presets(None, properties)

    request_options = dict(session._request_options() or {})  # noqa: SLF001
    if preset:
        request_options["additional_body_parameters"] = {"preset": preset}

    sip_token = generate_convo_ai_token(
        app_id=agent.app_id,
        app_certificate=agent.app_certificate,
        channel_name=channel,
        uid=sip_uid,
        token_expire=3600,
    )
    logger.info("Dialling the %s at %s from %s", role, _mask(to_number), _mask(cfg["from_number"]))
    response = await agent.client.telephony.call(
        agent.app_id,
        name=channel,
        sip=CallTelephonyRequestSip(
            to_number=to_number,
            from_number=cfg["from_number"],
            rtc_uid=str(sip_uid),
            rtc_token=sip_token,
        ),
        properties=CallTelephonyRequestProperties(**properties),
        request_options=request_options or None,
    )
    agent_id = getattr(response, "agent_id", None)
    auth = session._request_options()  # noqa: SLF001
    if agent_id:
        _call_auth[agent_id] = auth
    return {"agent_id": agent_id, "channel": channel, "request_options": auth}


async def call_info(agent: Any, agent_id: str) -> Any:
    """Agora's current view of a placed call."""
    return await agent.client.telephony.get(
        agent.app_id, agent_id, request_options=_call_auth.get(agent_id)
    )


async def follow_call(agent: Any, agent_id: str, request_options: Any, on_state) -> None:
    """Watch a placed call and report when it is answered and when it ends."""
    seen = None
    deadline = time.time() + MAX_CALL_SECONDS
    while time.time() < deadline:
        await asyncio.sleep(POLL_SECONDS)
        try:
            info = await agent.client.telephony.get(agent.app_id, agent_id, request_options=request_options)
        except Exception:
            logger.warning("Could not read call status agent_id=%s", agent_id, exc_info=True)
            continue
        state = getattr(info, "state", None)
        if state != seen and state in ("answered", "hangup"):
            seen = state
            on_state(state, getattr(info, "reason", None))
        if state == "hangup":
            return
