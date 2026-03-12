"""Azure Voice Live API client.

Connects to the Azure Voice Live API endpoint (/voice-live/realtime) which is
a separate service from the Azure OpenAI Realtime API (/openai/realtime).

Key differences from the Azure OpenAI Realtime approach:
  - Endpoint: Azure AI Services (Cognitive Services), NOT Azure OpenAI
  - URL path: /voice-live/realtime (not /openai/realtime)
  - Auth scope: https://ai.azure.com/.default (not cognitiveservices)
  - Voice config: Azure Speech voices with type "azure-standard"
  - Extra capabilities: noise suppression, echo cancellation

Interruption Handling (Barge-in)
--------------------------------
With Twilio PSTN as the audio transport, the standard browser-based interrupt
pattern (stop playing audio deltas) is insufficient because:

  1. **Twilio buffers outbound audio** — several seconds of audio may already
     be queued in Twilio's playout pipeline.  Simply stopping new deltas
     doesn't silence what's already buffered.

  2. **Azure finishes generating before Twilio finishes playing** — Azure
     produces audio faster than real-time, so ``response.done`` (and
     ``_response_in_progress = False``) can fire while Twilio still has
     seconds of queued audio to play.

  3. **Late deltas arrive after cancel** — even after sending
     ``response.cancel``, a few residual ``response.audio.delta`` messages
     leak through before Azure acknowledges with ``response.done``.

The solution implemented here:
  - On every ``speech_started`` event, **always** send a Twilio ``clear``
    message (via ``on_interrupt_callback``) to flush the outbound audio
    buffer, regardless of whether ``_response_in_progress`` is True.
  - If Azure is still generating (``_response_in_progress`` is True),
    additionally send ``response.cancel`` and set ``_mute_audio = True``
    to drop any stale deltas until ``response.done`` resets the flag.
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
        # ── Interruption / barge-in state ──────────────────────────────
        # _speech_active: True while the user is speaking (between
        #   speech_started and speech_stopped).
        # _response_in_progress: True while Azure is actively streaming
        #   response.audio.delta events.  Set True on first delta, reset
        #   False on response.done.
        # _mute_audio: When True, all incoming audio deltas and transcript
        #   events are silently dropped.  This prevents stale data from a
        #   cancelled response from reaching Twilio after a clear.  Reset
        #   to False on response.done so the next fresh response flows.
        self._speech_active = False
        self._response_in_progress = False
        self._mute_audio = False

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
                    "model": "gpt-4o-transcribe",
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
                    # Drop audio from a cancelled response — after an
                    # interrupt we mute until response.done clears the flag.
                    if self._mute_audio:
                        continue
                    audio_b64 = data.get("delta", "")
                    if audio_b64 and self._on_audio:
                        audio_bytes = base64.b64decode(audio_b64)
                        await self._on_audio(audio_bytes)

                elif msg_type == "response.audio_transcript.delta":
                    if self._mute_audio:
                        continue
                    text = data.get("delta", "")
                    if text and self._on_transcript:
                        await self._on_transcript("assistant", text, partial=True)

                elif msg_type == "response.audio_transcript.done":
                    # Skip transcript from a cancelled/muted response
                    if self._mute_audio:
                        continue
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
                    # ── BARGE-IN / INTERRUPTION HANDLING ─────────────────
                    # This is the critical path for making interruptions
                    # work over Twilio PSTN.  See the module docstring for
                    # the full rationale.
                    #
                    # Step 1: ALWAYS flush Twilio's outbound audio buffer.
                    #   Azure generates audio faster than real-time, so by
                    #   the time the user speaks, response.done may have
                    #   already fired (_response_in_progress == False) but
                    #   Twilio still has seconds of queued audio playing.
                    #   Sending Twilio's "clear" event discards that queue.
                    #
                    # Step 2: If Azure is still generating audio, send
                    #   response.cancel and mute the pipeline so any late-
                    #   arriving deltas don't re-fill the Twilio buffer
                    #   we just flushed.
                    logger.info(f"[{self.call_sid}] User started speaking (response_in_progress={self._response_in_progress})")
                    self._speech_active = True

                    # Step 1 — flush Twilio buffer unconditionally
                    if self._on_interrupt:
                        await self._on_interrupt()

                    # Step 2 — cancel Azure response if still generating
                    if self._response_in_progress:
                        self._mute_audio = True
                        self._response_in_progress = False
                        try:
                            await self.ws.send(json.dumps({"type": "response.cancel"}))
                            logger.info(f"[{self.call_sid}] Sent response.cancel to Azure")
                        except Exception:
                            logger.exception(f"[{self.call_sid}] Failed to send response.cancel")

                    logger.info(f"[{self.call_sid}] Interrupted — flushed Twilio buffer")

                elif msg_type == "input_audio_buffer.speech_stopped":
                    logger.info(f"[{self.call_sid}] User stopped speaking")
                    self._speech_active = False

                elif msg_type == "input_audio_buffer.committed":
                    logger.debug(f"[{self.call_sid}] Audio buffer committed")

                elif msg_type == "response.done":
                    self._response_in_progress = False
                    # Unmute audio — the cancelled response is finished,
                    # so the next response.audio.delta is from a fresh response.
                    self._mute_audio = False
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
            if self._response_in_progress:
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
