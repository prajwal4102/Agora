# -*- coding: utf-8 -*-
"""
Agora Agent & Token Service — MCP Recipe

HTTP APIs:
- GET  /get_config     -> Generate connection config
- POST /startAgent     -> Start agent with MCP tool calling
- POST /stopAgent      -> Stop agent
- GET  /state          -> Shop snapshot for the dashboard
- GET  /events         -> Agent event trail for the dashboard
- GET  /calls/pending  -> Call ringing for a role
- POST /calls/{id}/decline|end
- POST /shelf          -> New shelf count; may trigger a call
- POST /admin/reset    -> Reset demo data
"""
import logging
import os
import random
import time
from typing import Any, Dict, Literal, Optional
from dotenv import load_dotenv

# Load environment variables from .env.local or .env
_base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(_base_dir, '.env.local'), override=True)
load_dotenv(os.path.join(_base_dir, '.env'), override=True)

from fastapi import APIRouter, FastAPI, HTTPException, Query, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from agora_agent.agentkit.token import generate_convo_ai_token
from agent import Agent

logger = logging.getLogger("uvicorn.error")

# --- MCP mount (single-process): serve the FastMCP server in this same uvicorn ---
from contextlib import asynccontextmanager
import mcp_server  # exposes `mcp = FastMCP(...)`
import asyncio
import threading

import dispatch
import shop_db
import telephony
from vision import shelf_vision

# FastMCP serves the streamable-HTTP app at its default /mcp path; mounting the app
# at root lands the public endpoint at exactly /mcp (no trailing-slash redirect).
_mcp_asgi = mcp_server.mcp.streamable_http_app()


@asynccontextmanager
async def _lifespan(_app):
    # Starlette mount() does not auto-run a sub-app lifespan, so run the FastMCP
    # session manager here for the life of the server process.
    global _loop
    _loop = asyncio.get_running_loop()
    dispatch.on_call_placed = _on_call_placed
    # Load the shelf detector in the background so the first camera frame is fast.
    threading.Thread(target=shelf_vision.warm_up, daemon=True).start()
    callbacks = asyncio.create_task(_callback_loop())
    async with mcp_server.mcp.session_manager.run():
        yield
    callbacks.cancel()


async def _callback_loop() -> None:
    """Every few seconds, ring back anyone whose promised callback is due."""
    while True:
        await asyncio.sleep(3)
        try:
            dispatch.fire_due_callbacks()
        except Exception:
            logger.exception("Callback check failed")


_loop: Optional[asyncio.AbstractEventLoop] = None


async def _dial_real_phone(call: Dict[str, Any], to_number: str) -> None:
    """Dial the mobile for a queued call and mirror its progress into the queue."""
    call_id = call["call_id"]
    try:
        placed = await telephony.dial(agent, call["role"], call["greeting"], to_number)
    except Exception as exc:
        logger.exception("Phone call failed call_id=%s", call_id)
        shop_db.log_event(
            "call", "Phone call could not be placed. Ringing the web phone instead.",
            {"call_id": call_id, "error": str(exc)[:300]},
        )
        return  # the call stays 'ringing', so the web phone page still rings
    shop_db.log_event("call", f"Dialling the {call['role']}'s mobile", {"call_id": call_id},
                      agent="dispatcher", to=dispatch.VOICE_AGENT[call["role"]])

    def on_state(state: str, reason: Any) -> None:
        dispatch.set_call_status(call_id, "answered" if state == "answered" else "ended")

    if placed.get("agent_id"):
        await telephony.follow_call(agent, placed["agent_id"], placed["request_options"], on_state)


def _on_call_placed(call: Dict[str, Any]) -> None:
    """A call was queued. If real phone calls are on, dial the mobile too. Tools
    can run off the event loop, so hand the work over thread-safely."""
    to_number = telephony.number_for(call["role"])
    if not to_number or agent is None or _loop is None:
        return
    _loop.call_soon_threadsafe(lambda: asyncio.ensure_future(_dial_real_phone(call, to_number)))


def _log_route_error(route: str, exc: Exception, **context) -> None:
    """Log route failures with safe request context and a traceback."""
    safe_context = {key: value for key, value in context.items() if value is not None}
    logger.exception(
        "Request failed route=%s context=%s error_type=%s error=%s",
        route,
        safe_context,
        type(exc).__name__,
        exc,
    )


def _to_http_error(exc: Exception) -> HTTPException:
    """Convert SDK exceptions to HTTP errors"""
    if isinstance(exc, ValueError):
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, RuntimeError):
        return HTTPException(status_code=500, detail=str(exc))
    return HTTPException(status_code=500, detail=f"Internal error: {exc}")

try:
    agent = Agent()
except ValueError as e:
    logger.exception(
        "Failed to initialize Agent. Check environment variables: %s", e,
    )
    agent = None


# FastAPI application
app = FastAPI(
    title="Agora MCP Recipe Service",
    version="1.0.0",
    description="Agora Conversational AI with MCP tool calling",
    lifespan=_lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

router = APIRouter()


# Request models
class StartAgentRequest(BaseModel):
    """Request body for POST /startAgent"""
    channelName: str
    rtcUid: int
    userUid: int
    parameters: Optional[Dict[str, Any]] = None
    agentMode: Literal["pipeline", "realtime"] = "pipeline"
    role: Literal["supervisor", "employee"] = "supervisor"
    callId: Optional[int] = None  # set when answering a call the agent placed


class ShelfUpdateRequest(BaseModel):
    """Request body for POST /shelf — a new shelf count from the camera or slider"""
    product: str
    quantity: Optional[int] = None  # an absolute count (camera)
    delta: Optional[int] = None     # or a step from the current count (dashboard buttons)
    source: str = "manual"


class StopAgentRequest(BaseModel):
    """Request body for POST /stopAgent"""
    agentId: str


# API endpoints
def _generate_channel_name() -> str:
    return f"mcp-{int(time.time())}-{random.randint(1000, 9999)}"


@router.get("/get_config")
async def get_config(
    channel: Optional[str] = Query(default=None),
    uid: Optional[int] = Query(default=None),
):
    """Generate connection configuration"""
    if agent is None:
        raise HTTPException(
            status_code=500,
            detail="Service not properly configured. Please check environment variables.",
        )

    try:
        user_uid = random.randint(1000, 9999999) if uid is None or uid <= 0 else uid
        agent_uid = str(random.randint(10000000, 99999999))
        channel_name = channel or _generate_channel_name()

        app_id = os.getenv("AGORA_APP_ID")
        app_certificate = os.getenv("AGORA_APP_CERTIFICATE")

        token = generate_convo_ai_token(
            app_id=app_id,
            app_certificate=app_certificate,
            channel_name=channel_name,
            uid=user_uid,
            token_expire=3600,
        )

        config_data = {
            "app_id": app_id,
            "token": token,
            "uid": str(user_uid),
            "channel_name": channel_name,
            "agent_uid": agent_uid,
        }

        return {
            "code": 0,
            "data": config_data,
            "msg": "success",
        }
    except Exception as e:
        _log_route_error("/get_config", e, channel=channel, uid=uid)
        raise _to_http_error(e)


@router.post("/startAgent")
async def start_agent(request: StartAgentRequest):
    """Start agent with MCP tool calling in a channel"""
    if agent is None:
        raise HTTPException(
            status_code=500,
            detail="Service not properly configured. Please check environment variables.",
        )

    try:
        output_audio_codec = None
        if request.parameters:
            output_audio_codec = request.parameters.get("output_audio_codec")

        # Answering a call the agent placed: open with the reason it rang.
        role, greeting = request.role, None
        if request.callId is not None:
            call = dispatch.get_call(request.callId)
            if call is None:
                raise ValueError("That call no longer exists")
            role, greeting = call["role"], call["greeting"]
            dispatch.set_call_status(request.callId, "answered")
        else:
            dispatch.current_role = role
            shop_db.log_event("call", f"The {role} called in", agent=role, to=dispatch.VOICE_AGENT[role])

        result = await agent.start(
            channel_name=request.channelName,
            agent_uid=request.rtcUid,
            user_uid=request.userUid,
            output_audio_codec=output_audio_codec,
            agent_mode=request.agentMode,
            role=role,
            greeting=greeting,
        )
        return {"code": 0, "msg": "success", "data": {**result, "role": role}}
    except Exception as e:
        _log_route_error(
            "/startAgent",
            e,
            channelName=request.channelName,
            rtcUid=request.rtcUid,
            userUid=request.userUid,
        )
        raise _to_http_error(e)


@router.post("/stopAgent")
async def stop_agent(request: StopAgentRequest):
    """Stop agent by ID"""
    if agent is None:
        raise HTTPException(
            status_code=500,
            detail="Service not properly configured. Please check environment variables.",
        )

    try:
        await agent.stop(request.agentId)
        return {"code": 0, "msg": "success"}
    except Exception as e:
        _log_route_error("/stopAgent", e, agentId=request.agentId)
        raise _to_http_error(e)


# --- Dashboard data: shop snapshot, the agent's event trail, demo reset ---
@router.get("/state")
async def shop_state():
    """Shelf, back-room, budget and recent orders."""
    return {"code": 0, "data": dispatch.snapshot()}


@router.get("/events")
async def shop_events(after: int = Query(default=0, ge=0)):
    """Events newer than `after` (tool calls, triggers, calls placed)."""
    return {"code": 0, "data": shop_db.list_events(after_id=after)}


# --- Calls the agent places: phones poll for theirs, then answer or decline ---
@router.get("/calls/pending")
async def pending_call(role: Literal["supervisor", "employee"] = Query(...)):
    """The call ringing for this role right now, or null."""
    return {"code": 0, "data": dispatch.pending_call(role)}


@router.post("/calls/{call_id}/{action}")
async def update_call(call_id: int, action: Literal["decline", "end"]):
    status = {"decline": "declined", "end": "ended"}[action]
    call = dispatch.set_call_status(call_id, status)
    if call is None:
        raise HTTPException(status_code=404, detail="No such call")
    return {"code": 0, "data": call}


@router.post("/shelf")
async def update_shelf(request: ShelfUpdateRequest):
    """A new shelf count. If the product is now low, the agent decides who to call."""
    try:
        if request.delta is not None:
            data = dispatch.adjust_shelf(request.product, request.delta, request.source)
        elif request.quantity is not None:
            data = dispatch.update_shelf(request.product, request.quantity, request.source)
        else:
            raise HTTPException(status_code=400, detail="Send quantity or delta")
        return {"code": 0, "data": data}
    except shop_db.ShopError as e:
        raise HTTPException(status_code=400, detail=e.message)


@router.post("/vision/frame")
async def vision_frame(request: Request):
    """One webcam frame (JPEG bytes). Returns what the camera sees, and feeds
    any count that has held steady into the shelf."""
    image = await request.body()
    if not image:
        raise HTTPException(status_code=400, detail="Send a JPEG image as the request body")
    try:
        seen = await run_in_threadpool(shelf_vision.detect, image)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        _log_route_error("/vision/frame", exc)
        raise HTTPException(status_code=500, detail=f"Detector failed: {type(exc).__name__}: {str(exc)[:300]}")
    actions = {}
    for product, count in seen["changed"].items():
        actions[product] = dispatch.update_shelf(product, count, source="camera").get("action")
    return {"code": 0, "data": {**seen, "actions": actions}}


@router.get("/vision/last-frame")
async def vision_last_frame():
    """The most recent frame the camera sent, for checking what the detector sees."""
    if shelf_vision.last_frame is None:
        raise HTTPException(status_code=404, detail="No frame received yet. Start the camera.")
    return Response(content=shelf_vision.last_frame, media_type="image/jpeg")


@router.post("/vision/reset")
async def vision_reset():
    """Forget what the camera has seen (call when the shelf is rearranged)."""
    shelf_vision.reset()
    return {"code": 0, "msg": "reset"}


@router.get("/phone/status")
async def phone_status():
    """Whether real phone calls are switched on and fully configured."""
    cfg = telephony.settings()
    return {"code": 0, "data": {
        "mode": cfg["mode"],
        "missing": telephony.missing_settings(),
        "active": cfg["mode"] == "phone" and not telephony.missing_settings(),
    }}


@router.post("/phone/test")
async def phone_test(role: Literal["supervisor", "employee"] = Query(default="supervisor")):
    """Place one real test call to this role's mobile, whatever CALL_MODE is."""
    if agent is None:
        raise HTTPException(status_code=500, detail="Service not properly configured.")
    missing = telephony.missing_settings()
    if missing:
        raise HTTPException(status_code=400, detail=f"Fill in {', '.join(missing)} in server/.env.local")
    to_number = telephony.settings()[role]
    try:
        placed = await telephony.dial(
            agent, role,
            "Hello, this is Asha from FreshMart stock control, calling to test the phone line. Can you hear me?"
            if role == "supervisor" else
            "नमस्ते, मैं FreshMart stock control से Asha बोल रही हूँ। यह एक test call है। क्या आप मुझे सुन पा रहे हैं?",
            to_number,
        )
    except Exception as exc:
        _log_route_error("/phone/test", exc, role=role)
        raise HTTPException(status_code=502, detail=f"{type(exc).__name__}: {str(exc)[:500]}")
    return {"code": 0, "data": {"agent_id": placed.get("agent_id"), "channel": placed.get("channel")}}


@router.get("/phone/call/{agent_id}")
async def phone_call_status(agent_id: str):
    """Agora's view of a placed phone call: ringing, answered, hung up, and why."""
    try:
        info = await telephony.call_info(agent, agent_id)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"{type(exc).__name__}: {str(exc)[:500]}")
    data = info.model_dump() if hasattr(info, "model_dump") else dict(info)
    for key in ("to_number", "from_number"):
        if data.get(key):
            data[key] = "…" + str(data[key])[-4:]
    return {"code": 0, "data": data}


@router.post("/admin/reset")
async def reset_demo():
    """Put the shop back to its seed state before a demo run."""
    shop_db.init_db(reset=True)
    dispatch.reset()
    shelf_vision.reset()
    shop_db.log_event("system", "Demo data reset")
    return {"code": 0, "msg": "reset"}


app.include_router(router)

# Mount the FastMCP streamable-HTTP server in-process (serves its own /mcp path).
# Agora cloud reaches it at <public-url>/mcp — same process, same port as the
# token endpoints. Mounted last, so the server's own routes match first.
app.mount("/", _mcp_asgi)


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port)
