"""Azure OpenAI Realtime API (GPT-Realtime) client.

Manages a WebSocket connection to Azure OpenAI's Realtime API endpoint
(/openai/realtime) for bidirectional speech-to-speech streaming.

This is distinct from the Azure Voice Live API (/voice-live/realtime)
which lives on Azure AI Services endpoints — see azure_voicelive_client.py.
"""

import asyncio
import base64
import json
import logging

import websockets
from azure.identity.aio import DefaultAzureCredential

from config import settings

# Azure Cognitive Services scope for token auth
_AZURE_COGNITIVESERVICES_SCOPE = "https://cognitiveservices.azure.com/.default"

logger = logging.getLogger(__name__)


class AzureVoiceLiveSession:
    """Manages a single session with Azure Voice Live (GPT-Realtime) API."""

    def __init__(self, call_sid: str, on_audio_callback=None, on_transcript_callback=None):
        self.call_sid = call_sid
        self.ws = None
        self._on_audio = on_audio_callback
        self._on_transcript = on_transcript_callback
        self._receive_task: asyncio.Task | None = None
        self._closed = False

    async def connect(self):
        """Establish WebSocket connection to Azure Voice Live API using DefaultAzureCredential."""
        url = settings.azure_realtime_url

        # Acquire a token via managed identity / Azure CLI / env credentials
        self._credential = DefaultAzureCredential()
        token = await self._credential.get_token(_AZURE_COGNITIVESERVICES_SCOPE)
        headers = {"Authorization": f"Bearer {token.token}"}

        logger.info(f"[{self.call_sid}] Connecting to Azure Voice Live: {url}")

        self.ws = await websockets.connect(
            url,
            additional_headers=headers,
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
                "voice": settings.VOICE,
                "input_audio_format": "pcm16",
                "output_audio_format": "pcm16",
                "input_audio_transcription": {
                    "model": "whisper-1",
                },
                "turn_detection": {
                    "type": "server_vad",
                    "threshold": 0.5,
                    "prefix_padding_ms": 300,
                    "silence_duration_ms": 500,
                },
            },
        }

        await self.ws.send(json.dumps(session_config))
        logger.info(f"[{self.call_sid}] Session configured")

    async def send_audio(self, audio_bytes: bytes):
        """Send audio data to Azure Voice Live API.

        Args:
            audio_bytes: Raw PCM16 audio data (16kHz, mono, 16-bit signed LE).
        """
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
            logger.warning(f"[{self.call_sid}] Azure WS closed while sending audio")
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
                    # AI-generated audio chunk
                    audio_b64 = data.get("delta", "")
                    if audio_b64 and self._on_audio:
                        audio_bytes = base64.b64decode(audio_b64)
                        await self._on_audio(audio_bytes)

                elif msg_type == "response.audio_transcript.delta":
                    # Partial transcript of AI speech
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
                    logger.info(f"[{self.call_sid}] Azure session created")

                elif msg_type == "session.updated":
                    logger.info(f"[{self.call_sid}] Azure session updated")

                elif msg_type == "error":
                    error = data.get("error", {})
                    logger.error(f"[{self.call_sid}] Azure error: {error}")

                elif msg_type == "input_audio_buffer.speech_started":
                    logger.debug(f"[{self.call_sid}] User started speaking")

                elif msg_type == "input_audio_buffer.speech_stopped":
                    logger.debug(f"[{self.call_sid}] User stopped speaking")

        except websockets.exceptions.ConnectionClosed as e:
            logger.info(f"[{self.call_sid}] Azure WS closed: {e}")
        except Exception:
            logger.exception(f"[{self.call_sid}] Error in Azure receive loop")
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
        logger.info(f"[{self.call_sid}] Azure session closed")
