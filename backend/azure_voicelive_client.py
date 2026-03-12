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

    def __init__(self, call_sid: str, on_audio_callback=None, on_transcript_callback=None, scenario: dict | None = None, on_function_call=None, candidate_name: str | None = None, on_interrupt_callback=None):
        self.call_sid = call_sid
        self.ws = None
        self._on_audio = on_audio_callback
        self._on_transcript = on_transcript_callback
        self._on_function_call = on_function_call
        self._on_interrupt = on_interrupt_callback
        self._scenario = scenario
        self._candidate_name = candidate_name
        self._receive_task: asyncio.Task | None = None
        self._closed = False
        # Interruption handling
        self._speech_active = False
        self._response_in_progress = False
        self._pending_interrupt_task: asyncio.Task | None = None
        self._interrupt_debounce_ms = 350

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
        # Determine system prompt — scenario overrides default
        instructions = settings.SYSTEM_PROMPT
        if self._scenario:
            instructions = self._scenario.get("system_prompt", instructions)

        # Inject candidate name into instructions so the agent knows who it's speaking to
        if self._candidate_name:
            instructions += f"\n\nThe candidate you are speaking with is named {self._candidate_name}. Use their name when greeting them."

        # Voice config — scenario can override
        voice_config = {
            "name": settings.AZURE_TTS_VOICE_NAME,
            "type": "azure-standard",
            "temperature": 0.8,
        }
        if self._scenario and "voice" in self._scenario:
            vl = self._scenario["voice"].get("voice_live", {})
            if vl:
                voice_config = vl

        # Turn detection — scenario can override
        turn_detection = {
            "type": "server_vad",
            "threshold": 0.6,
            "prefix_padding_ms": 200,
            "silence_duration_ms": 500,
        }
        if self._scenario and "turn_detection" in self._scenario:
            turn_detection = self._scenario["turn_detection"]

        session_config = {
            "type": "session.update",
            "session": {
                "modalities": ["text", "audio"],
                "instructions": instructions,
                "input_audio_format": "pcm16",
                "output_audio_format": "pcm16",
                "input_audio_sampling_rate": 24000,
                "input_audio_transcription": {
                    "model": "whisper-1",
                },
                "turn_detection": turn_detection,
                "input_audio_noise_reduction": {
                    "type": "azure_deep_noise_suppression",
                },
                "input_audio_echo_cancellation": {
                    "type": "server_echo_cancellation",
                },
                "voice": voice_config,
            },
        }

        # Add tools if scenario defines them
        if self._scenario and self._scenario.get("tools"):
            session_config["session"]["tools"] = self._scenario["tools"]
            session_config["session"]["tool_choice"] = "auto"

        await self.ws.send(json.dumps(session_config))
        logger.info(f"[{self.call_sid}] Voice Live session configured (scenario: {self._scenario.get('id', 'none') if self._scenario else 'default'})")

        # Trigger the agent to speak first
        await self.ws.send(json.dumps({"type": "response.create"}))
        logger.info(f"[{self.call_sid}] Triggered initial agent greeting")

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
                    self._response_in_progress = True
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
                    self._speech_active = True
                    self._cancel_pending_interrupt()
                    self._pending_interrupt_task = asyncio.create_task(
                        self._debounced_interrupt()
                    )

                elif msg_type == "input_audio_buffer.speech_stopped":
                    logger.debug(f"[{self.call_sid}] User stopped speaking")
                    self._speech_active = False
                    self._cancel_pending_interrupt()

                elif msg_type == "input_audio_buffer.committed":
                    logger.debug(f"[{self.call_sid}] Audio buffer committed")

                elif msg_type == "response.done":
                    self._response_in_progress = False
                    await self._handle_response_done(data)

        except websockets.exceptions.ConnectionClosed as e:
            logger.info(f"[{self.call_sid}] Voice Live WS closed: {e}")
        except Exception:
            logger.exception(f"[{self.call_sid}] Error in Voice Live receive loop")
        finally:
            self._closed = True

    async def _handle_response_done(self, data: dict):
        """Handle response.done — check for function calls from the model."""
        try:
            response = data.get("response", {})
            if response.get("status") != "completed":
                return

            for output_item in response.get("output", []):
                if output_item.get("type") == "function_call":
                    fn_name = output_item.get("name", "")
                    call_id = output_item.get("call_id", "")
                    try:
                        arguments = json.loads(output_item.get("arguments", "{}"))
                    except json.JSONDecodeError:
                        arguments = {}

                    logger.info(f"[{self.call_sid}] Function call: {fn_name}")

                    # Delegate to the callback if provided
                    result = {}
                    if self._on_function_call:
                        result = await self._on_function_call(fn_name, arguments)

                    # Send function output back to the model
                    await self.ws.send(json.dumps({
                        "type": "conversation.item.create",
                        "item": {
                            "type": "function_call_output",
                            "call_id": call_id,
                            "output": json.dumps(result),
                        },
                    }))
                    # Trigger model to respond after function output
                    await self.ws.send(json.dumps({
                        "type": "response.create",
                        "response": {"modalities": ["text", "audio"]},
                    }))
        except Exception:
            logger.exception(f"[{self.call_sid}] Error handling function call")

    def _cancel_pending_interrupt(self):
        """Cancel any pending debounced interrupt task."""
        if self._pending_interrupt_task:
            self._pending_interrupt_task.cancel()
            self._pending_interrupt_task = None

    async def _debounced_interrupt(self):
        """Wait briefly before triggering an interrupt to filter out coughs/noise."""
        try:
            await asyncio.sleep(self._interrupt_debounce_ms / 1000)
            if self._speech_active and self._response_in_progress:
                await self._interrupt_response()
        except asyncio.CancelledError:
            pass
        finally:
            self._pending_interrupt_task = None

    async def _interrupt_response(self):
        """Cancel the current AI response so the user can barge in."""
        if self._closed or not self.ws:
            return
        try:
            await self.ws.send(json.dumps({"type": "response.cancel"}))
            await self.ws.send(json.dumps({"type": "input_audio_buffer.clear"}))
            self._response_in_progress = False
            # Flush Twilio's outbound audio buffer so buffered speech stops immediately
            if self._on_interrupt:
                await self._on_interrupt()
            logger.info(f"[{self.call_sid}] Interrupted — cancelled response for barge-in")
        except Exception:
            logger.exception(f"[{self.call_sid}] Error during interrupt")

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
