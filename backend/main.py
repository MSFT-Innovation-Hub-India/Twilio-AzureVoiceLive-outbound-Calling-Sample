"""FastAPI server — Twilio ↔ Azure Voice Live media bridge.

Endpoints:
    POST /api/call          – Trigger outbound call via Twilio
    GET  /api/calls         – List active calls
    POST /twilio/status     – Twilio status callback
    POST /twilio/twiml      – TwiML response for call flow
    WS   /ws/media/{sid}    – WebSocket for Twilio media stream
    WS   /ws/events/{sid}   – WebSocket for frontend live transcript
"""

import asyncio
import json
import logging
import uuid

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, field_validator

from config import settings
from twilio_client import twilio_client
from media_bridge import MediaBridge, active_sessions, BACKEND_GPT_REALTIME, BACKEND_VOICE_LIVE

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(title="Twilio ↔ Azure Voice Live Bridge")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Track frontend event subs: call_sid -> list[WebSocket]
event_subscribers: dict[str, list[WebSocket]] = {}

# Call metadata store
call_metadata: dict[str, dict] = {}


# ─── Models ───────────────────────────────────────────────────────

class CallRequest(BaseModel):
    phone_number: str
    backend: str = BACKEND_GPT_REALTIME

    @field_validator("phone_number")
    @classmethod
    def validate_phone(cls, v: str) -> str:
        import re
        cleaned = re.sub(r"[\s\-\(\)]", "", v)
        if not re.match(r"^\+?\d{10,15}$", cleaned):
            raise ValueError("Invalid phone number format")
        return cleaned

    @field_validator("backend")
    @classmethod
    def validate_backend(cls, v: str) -> str:
        allowed = {BACKEND_GPT_REALTIME, BACKEND_VOICE_LIVE}
        if v not in allowed:
            raise ValueError(f"backend must be one of {allowed}")
        return v


# ─── REST Endpoints ───────────────────────────────────────────────

@app.post("/api/call")
async def initiate_call(req: CallRequest):
    """Place an outbound call via Twilio and prepare the media bridge."""
    call_id = str(uuid.uuid4())[:8]

    twiml_url = f"{settings.PUBLIC_URL}/twilio/twiml?call_id={call_id}"
    status_callback = f"{settings.PUBLIC_URL}/twilio/status"

    # Store metadata before placing the call
    call_metadata[call_id] = {
        "call_id": call_id,
        "phone_number": req.phone_number,
        "backend": req.backend,
        "status": "initiating",
        "twilio_sid": None,
    }

    result = await twilio_client.place_call(
        to_number=req.phone_number,
        twiml_url=twiml_url,
        status_callback_url=status_callback,
    )

    if "error" in result:
        call_metadata[call_id]["status"] = "failed"
        raise HTTPException(status_code=502, detail=f"Twilio error: {result['error']}")

    call_metadata[call_id].update({
        "status": result.get("status", "queued"),
        "twilio_sid": result.get("call_sid"),
    })

    # Pre-create the media bridge so it's ready when Twilio connects
    bridge = MediaBridge(call_id, backend=req.backend)
    active_sessions[call_id] = bridge

    return {
        "call_id": call_id,
        "twilio_sid": result.get("call_sid"),
        "status": result.get("status"),
        "backend": req.backend,
        "message": f"Call initiated to {req.phone_number}",
    }


@app.get("/api/calls")
async def list_calls():
    """List active calls."""
    return {
        "calls": [
            {
                "call_id": cid,
                "status": meta.get("status"),
                "phone_number": meta.get("phone_number"),
            }
            for cid, meta in call_metadata.items()
        ]
    }


# ─── Twilio Callbacks ────────────────────────────────────────────

@app.post("/twilio/status")
async def twilio_status_callback(request: Request):
    """Receive call status updates from Twilio."""
    form = await request.form()
    data = dict(form)
    logger.info(f"Twilio status callback: {data}")

    call_sid = data.get("CallSid", "")
    status = data.get("CallStatus", "")

    # Update metadata
    for cid, meta in call_metadata.items():
        if meta.get("twilio_sid") == call_sid:
            meta["status"] = status
            # Notify frontend subscribers
            await _broadcast_event(cid, {"type": "status", "status": status})
            break

    return {"status": "ok"}


@app.post("/twilio/twiml", response_class=Response)
async def twiml_response(request: Request):
    """Return TwiML instructing Twilio to stream media to our WebSocket.

    When the outbound call is answered, Twilio fetches this URL
    to know what to do with the call. We instruct it to:
    1. Stream the audio to our WebSocket endpoint.
    2. Keep the call connected while streaming.
    """
    # Get call_id from query param (we pass it when creating the call)
    call_id = request.query_params.get("call_id", "")

    if not call_id:
        # Fallback: use the first active session
        if active_sessions:
            call_id = next(iter(active_sessions))
        else:
            call_id = "unknown"

    ws_url = f"{settings.PUBLIC_URL.replace('https', 'wss').replace('http', 'ws')}/ws/media/{call_id}"

    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say>Please wait while we connect you to our AI assistant.</Say>
    <Connect>
        <Stream url="{ws_url}">
            <Parameter name="call_id" value="{call_id}" />
        </Stream>
    </Connect>
</Response>"""

    return Response(content=twiml, media_type="application/xml")


# ─── WebSocket: Exotel Media Stream ──────────────────────────────

@app.websocket("/ws/media/{call_id}")
async def twilio_media_websocket(websocket: WebSocket, call_id: str):
    """WebSocket endpoint that receives Twilio's media stream.

    Twilio connects here after the call is answered and streams
    bidirectional audio (mulaw 8kHz).
    """
    await websocket.accept()
    logger.info(f"[{call_id}] Twilio media WebSocket connected")

    # Get or create bridge
    bridge = active_sessions.get(call_id)
    if not bridge:
        bridge = MediaBridge(call_id)
        active_sessions[call_id] = bridge

    # Update call status
    if call_id in call_metadata:
        call_metadata[call_id]["status"] = "connected"
    await _broadcast_event(call_id, {"type": "status", "status": "connected"})

    try:
        await bridge.handle_twilio_stream(websocket)
    finally:
        if call_id in call_metadata:
            call_metadata[call_id]["status"] = "completed"
        await _broadcast_event(call_id, {"type": "status", "status": "completed"})
        logger.info(f"[{call_id}] Twilio media WebSocket closed")


# ─── WebSocket: Frontend Events ──────────────────────────────────

@app.websocket("/ws/events/{call_id}")
async def frontend_events_websocket(websocket: WebSocket, call_id: str):
    """WebSocket for streaming live transcripts to the React frontend."""
    await websocket.accept()
    logger.info(f"[{call_id}] Frontend event subscriber connected")

    if call_id not in event_subscribers:
        event_subscribers[call_id] = []
    event_subscribers[call_id].append(websocket)

    try:
        # Keep alive — wait for disconnect
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        if call_id in event_subscribers:
            event_subscribers[call_id].remove(websocket)
            if not event_subscribers[call_id]:
                del event_subscribers[call_id]


async def _broadcast_event(call_id: str, event: dict):
    """Broadcast event to all frontend subscribers for a call."""
    subs = event_subscribers.get(call_id, [])
    for ws in subs:
        try:
            await ws.send_text(json.dumps(event))
        except Exception:
            pass


# ─── Health Check ─────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "active_calls": len(active_sessions),
    }


# ─── Runner ──────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host=settings.HOST,
        port=settings.PORT,
        log_level="info",
    )
