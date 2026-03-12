"""Media bridge between Twilio audio stream and Azure AI backend.

Twilio streams telephony audio (8kHz, mulaw) to our WebSocket.
The AI backend (GPT-Realtime or Voice Live API) expects 24kHz PCM16 mono.
This bridge handles format conversion and bidirectional streaming.
"""

import asyncio
import audioop
import base64
import json
import logging
import struct
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import WebSocket, WebSocketDisconnect

from azure_gpt_realtime_client import AzureVoiceLiveSession as GptRealtimeSession
from azure_voicelive_client import AzureVoiceLiveSession as VoiceLiveSession
import cosmosdb_client

logger = logging.getLogger(__name__)

# Active call sessions: call_sid -> MediaBridge
active_sessions: dict[str, "MediaBridge"] = {}


def mulaw_to_pcm16(mulaw_bytes: bytes, sample_rate_in: int = 8000, sample_rate_out: int = 24000) -> bytes:
    """Convert mulaw 8kHz audio to PCM16 24kHz for Azure Voice Live.

    Args:
        mulaw_bytes: Raw mulaw encoded audio bytes.
        sample_rate_in: Input sample rate (Twilio default: 8000).
        sample_rate_out: Output sample rate (Azure Voice Live: 24000).

    Returns:
        PCM16 signed 16-bit little-endian bytes at target sample rate.
    """
    # Decode mulaw to PCM16
    pcm_bytes = audioop.ulaw2lin(mulaw_bytes, 2)

    # Resample from 8kHz to 24kHz
    if sample_rate_in != sample_rate_out:
        pcm_bytes, _ = audioop.ratecv(
            pcm_bytes, 2, 1, sample_rate_in, sample_rate_out, None
        )

    return pcm_bytes


def pcm16_to_mulaw(pcm_bytes: bytes, sample_rate_in: int = 24000, sample_rate_out: int = 8000) -> bytes:
    """Convert PCM16 24kHz audio back to mulaw 8kHz for Twilio.

    Args:
        pcm_bytes: PCM16 signed 16-bit LE audio bytes.
        sample_rate_in: Input sample rate from Azure (24000).
        sample_rate_out: Output sample rate for Twilio (8000).

    Returns:
        Mulaw encoded bytes at 8kHz.
    """
    # Resample from 24kHz to 8kHz
    if sample_rate_in != sample_rate_out:
        pcm_bytes, _ = audioop.ratecv(
            pcm_bytes, 2, 1, sample_rate_in, sample_rate_out, None
        )

    # Encode PCM to mulaw
    mulaw_bytes = audioop.lin2ulaw(pcm_bytes, 2)
    return mulaw_bytes


# Backend type constants
BACKEND_GPT_REALTIME = "gpt-realtime"
BACKEND_VOICE_LIVE = "voice-live"


# Directory for saving interview results
RESULTS_DIR = Path(__file__).parent / "results"
RESULTS_DIR.mkdir(exist_ok=True)


class MediaBridge:
    """Bridges Twilio telephony audio with an Azure AI backend."""

    def __init__(self, call_sid: str, backend: str = BACKEND_GPT_REALTIME, scenario: dict | None = None, candidate_name: str | None = None, on_event_callback=None):
        self.call_sid = call_sid
        self.backend = backend
        self.scenario = scenario
        self.candidate_name = candidate_name
        self._on_event = on_event_callback
        self.twilio_ws: WebSocket | None = None
        self.azure_session = None
        self.stream_sid: str | None = None
        self._closed = False
        self.transcripts: list[dict] = []
        self.interview_results: dict | None = None

    async def handle_twilio_stream(self, websocket: WebSocket):
        """Handle incoming WebSocket connection from Twilio media stream.

        Twilio sends JSON messages with base64-encoded mulaw audio.
        """
        self.twilio_ws = websocket

        # Create the appropriate Azure session based on backend choice
        SessionClass = VoiceLiveSession if self.backend == BACKEND_VOICE_LIVE else GptRealtimeSession
        self.azure_session = SessionClass(
            call_sid=self.call_sid,
            on_audio_callback=self._send_audio_to_twilio,
            on_transcript_callback=self._handle_transcript,
            scenario=self.scenario,
            on_function_call=self._handle_function_call,
            candidate_name=self.candidate_name,
            on_interrupt_callback=self._flush_twilio_audio,
        )
        logger.info(f"[{self.call_sid}] Using backend: {self.backend}, scenario: {self.scenario.get('id') if self.scenario else 'default'}")

        try:
            await self.azure_session.connect()
            logger.info(f"[{self.call_sid}] Media bridge established")

            # Process Twilio stream messages
            while not self._closed:
                try:
                    raw = await websocket.receive_text()
                    message = json.loads(raw)
                    await self._process_twilio_message(message)
                except WebSocketDisconnect:
                    logger.info(f"[{self.call_sid}] Twilio WebSocket disconnected")
                    break
                except json.JSONDecodeError:
                    logger.warning(f"[{self.call_sid}] Invalid JSON from Twilio")
                    continue

        except Exception:
            logger.exception(f"[{self.call_sid}] Error in media bridge")
        finally:
            await self.close()

    async def _process_twilio_message(self, message: dict):
        """Process a message from Twilio's media stream.

        Twilio sends messages in the following format:
        - connected: stream connection established
        - start: stream metadata
        - media: audio payload
        - stop: stream ended
        """
        event = message.get("event", "")

        if event == "connected":
            logger.info(f"[{self.call_sid}] Twilio stream connected")

        elif event == "start":
            self.stream_sid = message.get("streamSid", message.get("stream_sid", ""))
            start_data = message.get("start", {})
            logger.info(
                f"[{self.call_sid}] Twilio stream started. "
                f"StreamSid: {self.stream_sid}, "
                f"MediaFormat: {start_data}"
            )

        elif event == "media":
            media = message.get("media", {})
            payload = media.get("payload", "")
            if payload:
                # Decode base64 mulaw audio from Twilio
                mulaw_audio = base64.b64decode(payload)

                # Convert mulaw 8kHz → PCM16 24kHz
                pcm_audio = mulaw_to_pcm16(mulaw_audio)

                # Send to Azure Voice Live
                if self.azure_session:
                    await self.azure_session.send_audio(pcm_audio)

        elif event == "stop":
            logger.info(f"[{self.call_sid}] Twilio stream stopped")
            await self.close()

    async def _send_audio_to_twilio(self, pcm_audio: bytes):
        """Send AI-generated audio back to Twilio.

        Converts PCM16 24kHz → mulaw 8kHz and sends via WebSocket.
        """
        if self._closed or not self.twilio_ws:
            return

        try:
            # Convert PCM16 24kHz → mulaw 8kHz
            mulaw_audio = pcm16_to_mulaw(pcm_audio)

            # Encode and send to Twilio
            audio_b64 = base64.b64encode(mulaw_audio).decode("utf-8")

            msg = {
                "event": "media",
                "streamSid": self.stream_sid,
                "media": {
                    "payload": audio_b64,
                },
            }

            await self.twilio_ws.send_text(json.dumps(msg))

        except Exception:
            logger.exception(f"[{self.call_sid}] Error sending audio to Twilio")

    async def _handle_transcript(self, role: str, text: str, partial: bool = False):
        """Handle transcript updates for logging/UI."""
        if not partial:
            self.transcripts.append({"role": role, "text": text})
            logger.info(f"[{self.call_sid}] [{role}]: {text}")
            # Broadcast to frontend event subscribers
            if self._on_event:
                await self._on_event(self.call_sid, {
                    "type": "transcript",
                    "role": role,
                    "text": text,
                })

    async def _handle_function_call(self, fn_name: str, arguments: dict) -> dict:
        """Handle function calls from the AI model (e.g., saving interview results)."""
        logger.info(f"[{self.call_sid}] Function call received: {fn_name}")

        if fn_name == "save_interview_results":
            # Attach metadata — 'id' is mandatory for Cosmos DB
            arguments["id"] = str(uuid.uuid4())
            arguments["call_id"] = self.call_sid
            arguments["candidate_name"] = self.candidate_name or ""
            arguments["scenario_id"] = self.scenario.get("id") if self.scenario else None
            arguments["timestamp"] = datetime.now(timezone.utc).isoformat()
            arguments["transcripts"] = self.transcripts.copy()

            self.interview_results = arguments

            # Persist to disk
            result_file = RESULTS_DIR / f"{self.call_sid}.json"
            result_file.write_text(json.dumps(arguments, indent=2, ensure_ascii=False), encoding="utf-8")
            logger.info(f"[{self.call_sid}] Interview results saved to {result_file}")

            # Persist to Cosmos DB (fire-and-forget so bridge close doesn't cancel it)
            asyncio.ensure_future(self._save_to_cosmosdb(arguments))

            return {"status": "saved", "call_id": self.call_sid}

        logger.warning(f"[{self.call_sid}] Unknown function: {fn_name}")
        return {"error": f"Unknown function: {fn_name}"}

    async def _flush_twilio_audio(self):
        """Send a clear message to Twilio to flush any buffered outbound audio."""
        if self._closed or not self.twilio_ws or not self.stream_sid:
            return
        try:
            msg = {"event": "clear", "streamSid": self.stream_sid}
            await self.twilio_ws.send_text(json.dumps(msg))
            logger.info(f"[{self.call_sid}] Flushed Twilio audio buffer for barge-in")
        except Exception:
            logger.exception(f"[{self.call_sid}] Error flushing Twilio audio buffer")

    async def _save_to_cosmosdb(self, arguments: dict):
        """Persist results to Cosmos DB independently of bridge lifecycle."""
        try:
            await cosmosdb_client.save_result(arguments)
        except Exception:
            logger.exception(f"[{self.call_sid}] Failed to persist results to Cosmos DB")

    async def close(self):
        """Clean up the media bridge."""
        if self._closed:
            return
        self._closed = True

        if self.azure_session:
            await self.azure_session.close()

        # Remove from active sessions
        active_sessions.pop(self.call_sid, None)
        logger.info(f"[{self.call_sid}] Media bridge closed")
