"""Twilio API client for placing outbound calls."""

import logging

import httpx

from config import settings

logger = logging.getLogger(__name__)


class TwilioClient:
    """Client for Twilio REST API."""

    def __init__(self):
        self.account_sid = settings.TWILIO_ACCOUNT_SID
        self.auth_token = settings.TWILIO_AUTH_TOKEN
        self.phone_number = settings.TWILIO_PHONE_NUMBER
        self.base_url = f"https://api.twilio.com/2010-04-01/Accounts/{self.account_sid}"

    async def place_call(self, to_number: str, twiml_url: str, status_callback_url: str) -> dict:
        """Place an outbound call via Twilio.

        Args:
            to_number: The phone number to call (E.164 format).
            twiml_url: URL Twilio will fetch for TwiML instructions.
            status_callback_url: URL Twilio will POST status updates to.

        Returns:
            dict with call SID and status.
        """
        url = f"{self.base_url}/Calls.json"

        form_data = {
            "From": self.phone_number,
            "To": to_number,
            "Url": twiml_url,
            "StatusCallback": status_callback_url,
            "StatusCallbackEvent": "initiated ringing answered completed",
        }

        logger.info(f"Placing outbound call to {to_number} via Twilio")

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                url,
                data=form_data,
                auth=(self.account_sid, self.auth_token),
            )

        if resp.status_code not in (200, 201):
            logger.error(f"Twilio API error: {resp.status_code} - {resp.text}")
            return {"error": resp.text, "status_code": resp.status_code}

        data = resp.json()
        call_sid = data.get("sid", "unknown")
        logger.info(f"Call placed successfully. Call SID: {call_sid}")

        return {
            "call_sid": call_sid,
            "status": data.get("status", "queued"),
            "to": to_number,
        }


twilio_client = TwilioClient()
