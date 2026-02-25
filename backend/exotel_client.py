"""Exotel API client for placing outbound calls.

NOTE: This file is NOT used in the current implementation. The application uses
Twilio (see twilio_client.py) for outbound calling. This file is retained only
as a reference for Exotel-based integration.
"""

import base64
import logging

import httpx

from config import settings

logger = logging.getLogger(__name__)


class ExotelClient:
    """Client for Exotel REST API."""

    def __init__(self):
        self.sid = settings.EXOTEL_SID
        self.api_key = settings.EXOTEL_API_KEY
        self.api_token = settings.EXOTEL_API_TOKEN
        self.caller_id = settings.EXOTEL_CALLER_ID
        self.subdomain = settings.EXOTEL_SUBDOMAIN
        self.base_url = f"https://{self.subdomain}/v1/Accounts/{self.sid}"

    def _auth_header(self) -> dict[str, str]:
        credentials = base64.b64encode(
            f"{self.api_key}:{self.api_token}".encode()
        ).decode()
        return {"Authorization": f"Basic {credentials}"}

    async def place_call(self, to_number: str, status_callback_url: str, stream_url: str) -> dict:
        """Place an outbound call via Exotel.

        Exotel will call `to_number` and once connected, it will connect the
        media stream to our WebSocket endpoint via the Exotel ExoML <Stream> verb.

        Args:
            to_number: The phone number to call (E.164 format).
            status_callback_url: URL Exotel will POST status updates to.
            stream_url: WebSocket URL where Exotel will stream audio.

        Returns:
            dict with call SID and status.
        """
        url = f"{self.base_url}/Calls/connect.json"

        # ExoML App URL that tells Exotel what to do when call connects.
        # We use a dynamic TwiML/ExoML response from our server.
        applet_url = f"{settings.PUBLIC_URL}/exotel/exoml"

        form_data = {
            "From": self.caller_id,
            "To": to_number,
            "CallerId": self.caller_id,
            "Url": applet_url,
            "StatusCallback": status_callback_url,
        }

        logger.info(f"Placing outbound call to {to_number} via Exotel")

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                url,
                data=form_data,
                headers=self._auth_header(),
            )

        if resp.status_code not in (200, 201):
            logger.error(f"Exotel API error: {resp.status_code} - {resp.text}")
            return {"error": resp.text, "status_code": resp.status_code}

        data = resp.json()
        call_data = data.get("Call", data)
        call_sid = call_data.get("Sid", "unknown")
        logger.info(f"Call placed successfully. Call SID: {call_sid}")

        return {
            "call_sid": call_sid,
            "status": call_data.get("Status", "queued"),
            "to": to_number,
        }


exotel_client = ExotelClient()
