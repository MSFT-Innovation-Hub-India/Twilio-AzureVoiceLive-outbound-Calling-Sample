"""FastAPI server — Twilio ↔ Azure Voice Live media bridge.

Endpoints:
    GET  /api/scenarios     – List available interview scenarios
    GET  /api/scenarios/{id} – Get full scenario details
    POST /api/call          – Trigger outbound call via Twilio
    GET  /api/calls         – List active calls
    GET  /api/results       – List completed interview results
    GET  /api/results/{id}  – Get interview result by call_id
    POST /twilio/status     – Twilio status callback
    POST /twilio/twiml      – TwiML response for call flow
    WS   /ws/media/{sid}    – WebSocket for Twilio media stream
    WS   /ws/events/{sid}   – WebSocket for frontend live transcript
"""

import asyncio
import json
import logging
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, field_validator
from typing import Optional

from config import settings
from twilio_client import twilio_client
from media_bridge import MediaBridge, active_sessions, BACKEND_GPT_REALTIME, BACKEND_VOICE_LIVE
from scenario_manager import list_scenarios, load_scenario
import cosmosdb_client
import cosmosdb_network

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
    scenario_id: Optional[str] = None
    candidate_name: Optional[str] = None

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

@app.get("/api/scenarios")
async def get_scenarios():
    """List all available interview/call scenarios."""
    return {"scenarios": list_scenarios()}


@app.get("/api/scenarios/{scenario_id}")
async def get_scenario(scenario_id: str):
    """Get full details of a specific scenario."""
    scenario = load_scenario(scenario_id)
    if not scenario:
        raise HTTPException(status_code=404, detail=f"Scenario '{scenario_id}' not found")
    # Return summary (don't expose full system_prompt to frontend)
    return {
        "id": scenario["id"],
        "name": scenario["name"],
        "description": scenario.get("description", ""),
        "version": scenario.get("version", "1.0"),
        "has_tools": bool(scenario.get("tools")),
    }


@app.post("/api/call")
async def initiate_call(req: CallRequest):
    """Place an outbound call via Twilio and prepare the media bridge."""
    call_id = str(uuid.uuid4())[:8]

    # Load scenario if specified
    scenario = None
    if req.scenario_id:
        scenario = load_scenario(req.scenario_id)
        if not scenario:
            raise HTTPException(status_code=404, detail=f"Scenario '{req.scenario_id}' not found")

    twiml_url = f"{settings.PUBLIC_URL}/twilio/twiml?call_id={call_id}"
    status_callback = f"{settings.PUBLIC_URL}/twilio/status"

    # Store metadata before placing the call
    call_metadata[call_id] = {
        "call_id": call_id,
        "phone_number": req.phone_number,
        "candidate_name": req.candidate_name,
        "backend": req.backend,
        "scenario_id": req.scenario_id,
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
    bridge = MediaBridge(call_id, backend=req.backend, scenario=scenario, candidate_name=req.candidate_name)
    active_sessions[call_id] = bridge

    return {
        "call_id": call_id,
        "twilio_sid": result.get("call_sid"),
        "status": result.get("status"),
        "backend": req.backend,
        "scenario_id": req.scenario_id,
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
                "candidate_name": meta.get("candidate_name"),
                "scenario_id": meta.get("scenario_id"),
            }
            for cid, meta in call_metadata.items()
        ]
    }


@app.get("/api/results")
async def get_results():
    """List all saved interview results."""
    try:
        items = await cosmosdb_client.list_results()
        results = [
            {
                "call_id": item.get("call_id", item.get("id")),
                "candidate_name": item.get("candidate_name", "Unknown"),
                "call_outcome": item.get("call_outcome", "unknown"),
                "overall_recommendation": item.get("overall_recommendation", "unknown"),
                "scenario_id": item.get("scenario_id"),
                "timestamp": item.get("timestamp"),
            }
            for item in items
        ]
        return {"results": results}
    except Exception:
        logger.exception("Failed to list results from Cosmos DB, falling back to local")
        return await _list_results_local()


async def _list_results_local():
    """Fallback: list results from local disk."""
    results_dir = Path(__file__).parent / "results"
    if not results_dir.exists():
        return {"results": []}
    results = []
    for path in sorted(results_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            results.append({
                "call_id": data.get("call_id", path.stem),
                "candidate_name": data.get("candidate_name", "Unknown"),
                "call_outcome": data.get("call_outcome", "unknown"),
                "overall_recommendation": data.get("overall_recommendation", "unknown"),
                "scenario_id": data.get("scenario_id"),
                "timestamp": data.get("timestamp"),
            })
        except (json.JSONDecodeError, KeyError):
            continue
    return {"results": results}


@app.get("/api/results/{call_id}")
async def get_result(call_id: str):
    """Get full interview result by call_id."""
    try:
        item = await cosmosdb_client.get_result(call_id)
        if item:
            return item
    except Exception:
        logger.exception(f"Failed to get result from Cosmos DB for {call_id}")

    # Fallback to local file
    result_file = Path(__file__).parent / "results" / f"{call_id}.json"
    if not result_file.exists():
        raise HTTPException(status_code=404, detail=f"Result for call '{call_id}' not found")
    return json.loads(result_file.read_text(encoding="utf-8"))


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


# ─── Cosmos DB Network Access ─────────────────────────────────────

@app.get("/api/cosmosdb/network-status")
async def cosmosdb_network_status():
    """Check whether Cosmos DB public network access is enabled."""
    try:
        status = await asyncio.to_thread(cosmosdb_network.check_public_network_access)
        return status
    except Exception as exc:
        logger.exception("Failed to check Cosmos DB network status")
        raise HTTPException(status_code=502, detail=str(exc))


@app.post("/api/cosmosdb/enable-network")
async def cosmosdb_enable_network():
    """Enable public network access on the Cosmos DB account (may take ~1 min)."""
    try:
        result = await asyncio.to_thread(cosmosdb_network.enable_public_network_access)
        return result
    except Exception as exc:
        logger.exception("Failed to enable Cosmos DB public network access")
        raise HTTPException(status_code=502, detail=str(exc))


# ─── Health Check ─────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "active_calls": len(active_sessions),
    }


@app.on_event("shutdown")
async def shutdown():
    await cosmosdb_client.close()


# ─── Runner ──────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host=settings.HOST,
        port=settings.PORT,
        log_level="info",
    )
