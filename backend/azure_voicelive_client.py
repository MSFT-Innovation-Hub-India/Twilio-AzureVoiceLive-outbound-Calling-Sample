"""Azure Voice Live API client.

Connects to the Azure Voice Live API endpoint (/voice-live/realtime) which is
a separate service from the Azure OpenAI Realtime API (/openai/realtime).

Key differences from the Azure OpenAI Realtime approach:
  - Endpoint: Azure AI Services (Cognitive Services), NOT Azure OpenAI
  - URL path: /voice-live/realtime (not /openai/realtime)
  - Auth scope: https://ai.azure.com/.default (not cognitiveservices)
  - Voice config: Azure Speech voices with type "azure-standard"
  - Extra capabilities: noise suppression, echo cancellation
"""

import asyncio
import base64
import json
import logging

import websockets
from azure.identity.aio import DefaultAzureCredential

from config import settings

# Azure AI scope for Voice Live API token auth
_AZURE_AI_SCOPE = "https://ai.azure.com/.default"

logger = logging.getLogger(__name__)


class AzureVoiceLiveSession:
    """Manages a single session with the Azure Voice Live API."""

    def __init__(self, call_sid: str, on_audio_callback=None, on_transcript_callback=None):
        self.call_sid = call_sid
        self.ws = None
        self._on_audio = on_audio_callback
        self._on_transcript = on_transcript_callback
        self._receive_task: asyncio.Task | None = None
        self._closed = False

    async def connect(self):
        """Establish WebSocket connection to Azure Voice Live API."""
        self._credential = DefaultAzureCredential()
        token = await self._credential.get_token(_AZURE_AI_SCOPE)
        access_token = token.token

        # Build the Voice Live API WebSocket URL
        base = settings.AZURE_VOICE_LIVE_ENDPOINT.rstrip("/")
        ws_base = base.replace("https://", "wss://").replace("http://", "ws://")
        url = (
            f"{ws_base}/voice-live/realtime"
            f"?api-version={settings.AZURE_VOICE_LIVE_API_VERSION}"
            f"&model={settings.VOICE_LIVE_MODEL}"
            f"&agent-access-token={access_token}"
        )

        logger.info(f"[{self.call_sid}] Connecting to Azure Voice Live API: {ws_base}/voice-live/realtime")

        self.ws = await websockets.connect(
            url,
            additional_headers={
                "Authorization": f"Bearer {access_token}",
            },
            max_size=None,
            open_timeout=30,
        )

        logger.info(f"[{self.call_sid}] Connected to Azure Voice Live API")

        # Configure the session
        await self._configure_session()

        # Start receiving messages
        self._receive_task = asyncio.create_task(self._receive_loop())

    async def _configure_session(self):
        """Send session configuration to Azure Voice Live API."""
        session_config = {
            "type": "session.update",
            "session": {
                "modalities": ["text", "audio"],
                "instructions": settings.SYSTEM_PROMPT,
                "input_audio_format": "pcm16",
                "output_audio_format": "pcm16",
                "input_audio_sampling_rate": 24000,
                "input_audio_transcription": {
                    "model": "whisper-1",
                },
                "turn_detection": {
                    "type": "server_vad",
                    "threshold": 0.5,
                    "prefix_padding_ms": 300,
                    "silence_duration_ms": 500,
                },
                "input_audio_noise_reduction": {
                    "type": "azure_deep_noise_suppression",
                },
                "input_audio_echo_cancellation": {
                    "type": "server_echo_cancellation",
                },
                "voice": {
                    "name": settings.AZURE_TTS_VOICE_NAME,
                    "type": "azure-standard",
                    "temperature": 0.8,
                },
            },
        }

        await self.ws.send(json.dumps(session_config))
        logger.info(f"[{self.call_sid}] Voice Live session configured")

    async def send_audio(self, audio_bytes: bytes):
        """Send PCM16 audio data to Azure Voice Live API."""
        if self._closed or not self.ws:
            return

        audio_b64 = base64.b64encode(audio_bytes).decode("utf-8")
        msg = {
            "type": "input_audio_buffer.append",
            "audio": audio_b64,
        }

        try:
            await self.ws.send(json.dumps(msg))
        except websockets.exceptions.ConnectionClosed:
            logger.warning(f"[{self.call_sid}] Voice Live WS closed while sending audio")
            self._closed = True

    async def _receive_loop(self):
        """Receive and process messages from Azure Voice Live API."""
        try:
            async for message in self.ws:
                if self._closed:
                    break

                data = json.loads(message)
                msg_type = data.get("type", "")

                if msg_type == "response.audio.delta":
                    audio_b64 = data.get("delta", "")
                    if audio_b64 and self._on_audio:
                        audio_bytes = base64.b64decode(audio_b64)
                        await self._on_audio(audio_bytes)

                elif msg_type == "response.audio_transcript.delta":
                    text = data.get("delta", "")
                    if text and self._on_transcript:
                        await self._on_transcript("assistant", text, partial=True)

                elif msg_type == "response.audio_transcript.done":
                    text = data.get("transcript", "")
                    if text and self._on_transcript:
                        await self._on_transcript("assistant", text, partial=False)

                elif msg_type == "conversation.item.input_audio_transcription.completed":
                    text = data.get("transcript", "")
                    if text and self._on_transcript:
                        await self._on_transcript("user", text, partial=False)

                elif msg_type == "session.created":
                    logger.info(f"[{self.call_sid}] Voice Live session created")

                elif msg_type == "session.updated":
                    logger.info(f"[{self.call_sid}] Voice Live session updated")

                elif msg_type == "error":
                    error = data.get("error", {})
                    logger.error(f"[{self.call_sid}] Voice Live error: {error}")

                elif msg_type == "input_audio_buffer.speech_started":
                    logger.debug(f"[{self.call_sid}] User started speaking")

                elif msg_type == "input_audio_buffer.speech_stopped":
                    logger.debug(f"[{self.call_sid}] User stopped speaking")

                elif msg_type == "input_audio_buffer.committed":
                    logger.debug(f"[{self.call_sid}] Audio buffer committed")

        except websockets.exceptions.ConnectionClosed as e:
            logger.info(f"[{self.call_sid}] Voice Live WS closed: {e}")
        except Exception:
            logger.exception(f"[{self.call_sid}] Error in Voice Live receive loop")
        finally:
            self._closed = True

    async def close(self):
        """Close the Azure Voice Live session."""
        self._closed = True
        if self._receive_task and not self._receive_task.done():
            self._receive_task.cancel()
            try:
                await self._receive_task
            except asyncio.CancelledError:
                pass
        if self.ws:
            await self.ws.close()
        if hasattr(self, "_credential"):
            await self._credential.close()
        logger.info(f"[{self.call_sid}] Voice Live session closed")
