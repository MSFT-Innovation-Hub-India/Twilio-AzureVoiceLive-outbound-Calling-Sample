import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    # Twilio
    TWILIO_ACCOUNT_SID: str = os.getenv("TWILIO_ACCOUNT_SID", "")
    TWILIO_AUTH_TOKEN: str = os.getenv("TWILIO_AUTH_TOKEN", "")
    TWILIO_PHONE_NUMBER: str = os.getenv("TWILIO_PHONE_NUMBER", "")

    # Azure Voice Live API
    AZURE_OPENAI_ENDPOINT: str = os.getenv("AZURE_OPENAI_ENDPOINT", "")
    AZURE_OPENAI_DEPLOYMENT: str = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o-realtime-preview")
    AZURE_OPENAI_API_VERSION: str = os.getenv("AZURE_OPENAI_API_VERSION", "2025-04-01-preview")

    # Server
    HOST: str = os.getenv("HOST", "0.0.0.0")
    PORT: int = int(os.getenv("PORT", "8000"))
    PUBLIC_URL: str = os.getenv("PUBLIC_URL", "http://localhost:8000")

    # Agent
    SYSTEM_PROMPT: str = os.getenv(
        "SYSTEM_PROMPT",
        "You are a helpful voice assistant. Be concise and conversational. You are speaking with someone on a phone call.",
    )
    VOICE: str = os.getenv("VOICE", "alloy")

    @property
    def azure_realtime_url(self) -> str:
        base = self.AZURE_OPENAI_ENDPOINT.rstrip("/")
        return (
            f"{base.replace('https://', 'wss://').replace('http://', 'ws://')}"
            f"/openai/realtime"
            f"?api-version={self.AZURE_OPENAI_API_VERSION}"
            f"&deployment={self.AZURE_OPENAI_DEPLOYMENT}"
        )


settings = Settings()
