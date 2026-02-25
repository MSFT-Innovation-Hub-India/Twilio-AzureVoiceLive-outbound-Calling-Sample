# Twilio ↔ Azure Voice Live — Outbound Voice Agent

A sample application that places **outbound PSTN calls** via **Twilio** and
connects the callee to an AI voice agent powered by **Azure Voice Live API
(GPT-Realtime)**. The entire conversation — user speech in, AI speech out —
happens in real time over the phone.

---

## Architecture Diagram

```
┌────────────────┐          ┌────────────────────────┐          ┌─────────────────┐
│                │  HTTP    │                        │  REST    │                 │
│  React + Vite  │────────▶│   FastAPI Backend       │────────▶│  Twilio REST    │
│  (port 3000)   │  POST    │   (port 8000)          │  POST    │  API            │
│                │ /api/call│                        │ Calls    │                 │
└───────┬────────┘          └────┬─────────┬─────────┘          └────────┬────────┘
        │                        │         │                             │
        │ WebSocket              │         │                             │  PSTN
        │ /ws/events/{id}        │         │                             │  Call
        │ (live transcripts)     │         │                             ▼
        │                        │         │                   ┌───────────────┐
        │                        │         │                   │  Callee's     │
        ▼                        │         │                   │  Phone        │
   ┌─────────┐                   │         │                   └───────┬───────┘
   │ Browser │                   │         │                           │
   │   UI    │                   │         │   TwiML <Stream>          │
   └─────────┘                   │         │   (audio/x-mulaw 8kHz)    │
                                 │         │                           │
                                 │    ┌────▼───────────────────────────▼────┐
                                 │    │                                     │
                                 │    │        Media Bridge                 │
                                 │    │        (WebSocket handler)          │
                                 │    │                                     │
                                 │    │  ┌────────────┐    ┌─────────────┐  │
                                 │    │  │ mulaw→PCM  │    │ PCM→mulaw   │  │
                                 │    │  │ 8kHz→24kHz │    │ 24kHz→8kHz  │  │
                                 │    │  └─────┬──────┘    └──────▲──────┘  │
                                 │    │        │                  │         │
                                 │    └────────┼──────────────────┼─────────┘
                                 │             │                  │
                                 │             │ PCM16 24kHz      │ PCM16 24kHz
                                 │             │ (base64 JSON)    │ (base64 JSON)
                                 │             ▼                  │
                                 │    ┌────────────────────────────────────┐
                                 │    │                                    │
                                 │    │  Azure Voice Live API              │
                                 │    │  (GPT-Realtime / gpt-4o-realtime)  │
                                 │    │                                    │
                                 │    │  • Server VAD                      │
                                 │    │  • Whisper transcription           │
                                 │    │  • Speech-to-speech generation     │
                                 │    │                                    │
                                 │    └────────────────────────────────────┘
                                 │
                          DefaultAzureCredential
                          (token-based auth)
```

---

## Call Flow — End to End

```
  User (Browser)        FastAPI Backend         Twilio             Phone          Azure Voice Live
       │                      │                    │                  │                   │
       │  POST /api/call      │                    │                  │                   │
  1.   │─────────────────────▶│                    │                  │                   │
       │                      │  POST Calls.json   │                  │                   │
  2.   │                      │───────────────────▶│                  │                   │
       │                      │   201 Created       │                  │                   │
  3.   │  { call_id, sid }    │◀───────────────────│                  │                   │
       │◀─────────────────────│                    │                  │                   │
       │                      │                    │                  │                   │
       │  WS /ws/events/{id}  │                    │                  │                   │
  4.   │═════════════════════▶│                    │                  │                   │
       │  (subscribe to live  │                    │  PSTN Ring       │                   │
       │   transcripts)       │                    │─────────────────▶│                   │
       │                      │                    │                  │                   │
       │                      │                    │  Callee answers  │                   │
  5.   │                      │  POST /twilio/twiml│◀─────────────────│                   │
       │                      │◀───────────────────│                  │                   │
       │                      │  <Response>        │                  │                   │
  6.   │                      │   <Say>...</Say>   │                  │                   │
       │                      │   <Connect>        │                  │                   │
       │                      │    <Stream url=    │                  │                   │
       │                      │     /ws/media/{id}>│                  │                   │
       │                      │───────────────────▶│  "Please wait…"  │                   │
       │                      │                    │─────────────────▶│                   │
       │                      │                    │                  │                   │
       │                      │  WS /ws/media/{id} │                  │                   │
  7.   │                      │◀═══════════════════│                  │                   │
       │                      │  (Twilio opens     │                  │                   │
       │                      │   media stream)    │                  │                   │
       │                      │                    │                  │                   │
       │                      │  DefaultAzureCredential               │                   │
  8.   │                      │──────────────────────────────────────────────────────────▶│
       │                      │                    │                  │   WSS connected   │
       │                      │                    │                  │   session.update   │
       │                      │                    │                  │                   │
       │                      │                    │                  │                   │
       │                      │  ┌──── Audio Loop (bidirectional) ───────────────┐       │
       │                      │  │                 │                  │           │       │
       │                      │  │  Twilio sends   │  Caller speaks  │           │       │
  9.   │                      │◀═╪═ media payload ═│◀════════════════│           │       │
       │                      │  │  (mulaw 8kHz    │                  │           │       │
       │                      │  │   base64)       │                  │           │       │
       │                      │  │                 │                  │           │       │
       │                      │  │  mulaw→PCM16    │                  │           │       │
 10.   │                      │  │  8kHz → 24kHz   │                  │           │       │
       │                      │  │  ──────────────────────────────────────────────┼──────▶│
       │                      │  │                 │                  │  input_   │       │
       │                      │  │                 │                  │  audio_   │       │
       │                      │  │                 │                  │  buffer.  │       │
       │                      │  │                 │                  │  append   │       │
       │                      │  │                 │                  │           │       │
       │                      │  │                 │                  │  response.│       │
 11.   │                      │◀─┼─────────────────────────────────────────────────┼──────│
       │                      │  │  PCM16→mulaw    │                  │  audio.   │       │
 12.   │                      │  │  24kHz → 8kHz   │                  │  delta    │       │
       │                      │══╪═ media payload ═╪═════════════════╪══════════▶│       │
       │                      │  │  (mulaw 8kHz    │  AI speaks      │           │       │
       │                      │  │   to Twilio)    │─────────────────▶  (phone)  │       │
       │                      │  │                 │                  │           │       │
       │  transcript event    │  │                 │                  │           │       │
 13.   │◀═════════════════════│  │  (Whisper       │                  │           │       │
       │  { role, text }      │  │   transcripts)  │                  │           │       │
       │                      │  └─────────────────────────────────────────────────┘       │
       │                      │                    │                  │                   │
       │                      │                    │  Call ends       │                   │
 14.   │                      │  POST /twilio/status                  │                   │
       │                      │◀───────────────────│  completed       │                   │
       │  status: completed   │                    │                  │                   │
       │◀═════════════════════│                    │                  │                   │
       │                      │                    │                  │                   │
```

### Step-by-step Explanation

| Step | Action |
|------|--------|
| **1** | User enters a phone number in the React UI and clicks **Place Call**. Frontend `POST`s to `/api/call`. |
| **2** | Backend generates a unique `call_id`, then calls **Twilio REST API** (`POST /Calls.json`) with the `From` number, `To` number, a `Url` pointing to our TwiML endpoint, and a `StatusCallback` URL. |
| **3** | Twilio responds `201 Created` with a `CallSid`. Backend returns `call_id` + `CallSid` to the frontend. |
| **4** | Frontend opens a WebSocket to `/ws/events/{call_id}` to receive live transcript and status updates. |
| **5** | Twilio dials the callee. When the phone is answered, Twilio fetches `POST /twilio/twiml?call_id={call_id}`. |
| **6** | Backend returns TwiML XML: `<Say>` plays a greeting, then `<Connect><Stream>` instructs Twilio to open a WebSocket media stream to `/ws/media/{call_id}`. |
| **7** | Twilio opens a WebSocket to the backend's media endpoint. The backend receives `connected` and `start` events with stream metadata (encoding: `audio/x-mulaw`, sample rate: 8000 Hz, 1 channel). |
| **8** | Backend acquires an Azure AD token via `DefaultAzureCredential` and opens a WSS connection to **Azure Voice Live API** (`wss://<endpoint>/openai/realtime?deployment=gpt-realtime`). It sends a `session.update` message configuring server VAD, Whisper transcription, and the voice model. |
| **9** | Caller speaks → Twilio captures audio → sends `media` events (base64-encoded mulaw, 8 kHz) to the backend over WebSocket. |
| **10** | **Media Bridge** decodes mulaw → PCM16, resamples 8 kHz → 24 kHz, then base64-encodes and sends `input_audio_buffer.append` to Azure. |
| **11** | Azure GPT-Realtime detects end of speech (server VAD), generates a response, and streams back `response.audio.delta` events (PCM16 24 kHz). |
| **12** | Media Bridge converts PCM16 24 kHz → mulaw 8 kHz, wraps it in a Twilio `media` message, and sends it back to Twilio over the same WebSocket. Twilio plays it to the callee's phone. |
| **13** | Azure also emits transcript events (`response.audio_transcript.done` for AI, `conversation.item.input_audio_transcription.completed` for user). Backend forwards these to the React UI via the `/ws/events/{call_id}` WebSocket. |
| **14** | When the call ends, Twilio sends a `POST /twilio/status` callback with `CallStatus=completed`. Backend updates state and notifies the frontend. |

---

## Audio Pipeline

```
Caller's phone                                                    Azure Voice Live
──────────────                                                    ────────────────
   voice                                                            GPT-Realtime
     │                                                                   │
     ▼                                                                   ▼
┌──────────┐    ┌──────────┐     ┌──────────┐    ┌──────────┐     ┌───────────────┐
│  mulaw   │───▶│ mulaw →  │───▶│ resample │───▶│ PCM16    │───▶│ input_audio_  │
│  8kHz    │    │ PCM16    │     │ 8k → 24k │    │ 24kHz    │     │ buffer.append │
│ (Twilio) │    │          │     │          │    │          │     │               │
└──────────┘    └──────────┘     └──────────┘    └──────────┘     └───────────────┘

┌──────────────┐    ┌──────────┐    ┌──────────┐      ┌──────────┐    ┌──────────┐
│ response.    │───▶│ PCM16    │───▶│ resample │───▶│ PCM16 →  │───▶│  mulaw   │
│ audio.delta  │    │ 24kHz    │    │ 24k → 8k │      │ mulaw    │    │  8kHz    │
│              │    │          │    │          │      │          │    │ (Twilio) │
└──────────────┘    └──────────┘    └──────────┘      └──────────┘    └──────────┘
```

---

## Project Structure

```
├── backend/
│   ├── main.py              # FastAPI server — REST + WebSocket endpoints
│   ├── config.py            # Settings loaded from .env
│   ├── twilio_client.py     # Twilio REST API client (outbound calls)
│   ├── media_bridge.py      # Bridges Twilio audio ↔ Azure Voice Live
│   ├── azure_voice_live.py  # Azure Voice Live (GPT-Realtime) WebSocket client
│   ├── requirements.txt     # Python dependencies
│   ├── .env.example         # Environment variable template
│   └── .env                 # Your local config (git-ignored)
├── frontend/
│   ├── src/
│   │   └── App.jsx          # React UI — call control + live transcript
│   ├── vite.config.js       # Vite dev server config (proxy to backend)
│   └── package.json         # Frontend dependencies
└── README.md
```

### Key Components

| File | Purpose |
|------|---------|
| **main.py** | FastAPI app with endpoints: `POST /api/call` (initiate call), `POST /twilio/twiml` (return TwiML XML), `POST /twilio/status` (status callbacks), `WS /ws/media/{id}` (Twilio audio stream), `WS /ws/events/{id}` (frontend transcript stream). |
| **twilio_client.py** | Async HTTP client using `httpx` with Basic auth. Calls `POST /2010-04-01/Accounts/{sid}/Calls.json` to place outbound calls. |
| **media_bridge.py** | Bidirectional audio bridge. Converts mulaw 8 kHz ↔ PCM16 24 kHz using `audioop`. Manages the lifecycle of both the Twilio and Azure WebSocket streams. |
| **azure_voice_live.py** | Opens a WSS connection to Azure OpenAI Realtime API. Authenticates via `DefaultAzureCredential`. Configures server VAD + Whisper transcription. Streams audio in/out and emits transcript events. |
| **config.py** | Loads `.env` and exposes typed settings. Builds the Azure WSS URL from endpoint, deployment, and API version. |
| **App.jsx** | React UI with phone number input, call/hangup controls, status badge, and a chat-style live transcript view. |

---

## Prerequisites

- **Python 3.11+**
- **Node.js 18+**
- **Twilio account** with a phone number capable of outbound calling
- **Azure OpenAI** resource with a `gpt-4o-realtime-preview` (or `gpt-realtime`) model deployed
- **Azure CLI** logged in (`az login`) — used by `DefaultAzureCredential` for local development
- **ngrok** (or similar tunneling tool) to expose `localhost:8000` to the internet for Twilio webhooks

---

## Quickstart — Run & Test (Step by Step)

Follow these steps in order to go from zero to a working outbound voice agent call.

### Step 1: Clone and install dependencies

```bash
# Backend
cd backend
python -m venv .venv

# Windows
.venv\Scripts\activate
# macOS/Linux
source .venv/bin/activate

pip install -r requirements.txt

# Frontend
cd ../frontend
npm install
```

### Step 2: Set up Twilio

1. Create a Twilio account at [twilio.com](https://www.twilio.com/) (free trial works)
2. From the **Console Dashboard**, copy your **Account SID** and **Auth Token**
3. Buy a phone number under **Phone Numbers → Buy a Number** (choose one with Voice capability)
4. **Trial account only:** Add the phone number you want to call as a **Verified Caller ID** (Phone Numbers → Verified Caller IDs → Add New). Twilio trial accounts can only call verified numbers.

### Step 3: Set up Azure OpenAI

1. Create an **Azure OpenAI** resource in the Azure portal
2. Deploy a `gpt-4o-realtime-preview` model (name it e.g. `gpt-realtime`)
3. Assign the **Cognitive Services OpenAI User** role to your Azure identity on the resource:
   ```bash
   az role assignment create \
     --assignee <your-email-or-object-id> \
     --role "Cognitive Services OpenAI User" \
     --scope /subscriptions/<sub-id>/resourceGroups/<rg>/providers/Microsoft.CognitiveServices/accounts/<resource-name>
   ```
4. Log in to Azure CLI (used by `DefaultAzureCredential` for local dev):
   ```bash
   az login
   ```

### Step 4: Start ngrok tunnel

```bash
ngrok http 8000
```

Note the **Forwarding** URL (e.g. `https://4bcc-167-220-238-22.ngrok-free.app`). You'll need this in the next step.

> **Tip:** Keep this terminal open. If you restart ngrok, the URL changes and you must update `.env`.

### Step 5: Configure environment variables

```bash
cd backend
cp .env.example .env
```

Edit `.env` with your actual values:

```ini
TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TWILIO_AUTH_TOKEN=your_auth_token_here
TWILIO_PHONE_NUMBER=+12131234567

AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com
AZURE_OPENAI_DEPLOYMENT=gpt-realtime
AZURE_OPENAI_API_VERSION=2025-04-01-preview

PUBLIC_URL=https://4bcc-167-220-238-22.ngrok-free.app

SYSTEM_PROMPT=You are a helpful voice assistant. Be concise and conversational.
VOICE=alloy
```

### Step 6: Log in to Azure

The Azure Voice Live API and models are accessed using **managed identity** via `DefaultAzureCredential`. For local development, this means you must be logged in to the Azure CLI under the correct **Entra ID tenant** that has access to your Azure OpenAI resource:

```bash
# Log in to the correct tenant
az login --tenant <your-tenant-id>

# Verify you're in the right context
az account show
```

> **Important:** If your Azure OpenAI resource lives in a different tenant than your default, you **must** specify `--tenant`. Otherwise `DefaultAzureCredential` will acquire a token for the wrong tenant and API calls will fail with a 401.

### Step 7: Start the backend

```bash
cd backend
python main.py
```

You should see:
```
INFO:     Uvicorn running on http://0.0.0.0:8000 (Press CTRL+C to quit)
```

### Step 8: Start the frontend

```bash
cd frontend
npm run dev
```

Opens the React dev server at `http://localhost:3000`.

### Step 9: Place a test call

1. Open **http://localhost:3000** in your browser
2. Enter the phone number to call in E.164 format (e.g. `+919916138854`)
3. Click **📞 Place Call**
4. Your phone will ring — answer it
5. You'll hear _"Please wait while we connect you to our AI assistant."_
6. After a brief pause, the AI agent connects and you can have a conversation
7. The live transcript appears in the browser UI in real time

### Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| Call doesn't ring | Twilio credentials wrong or number not verified | Check `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`. On trial, verify the callee's number. |
| Phone rings but hangs up immediately | ngrok URL mismatch or backend not running | Ensure `PUBLIC_URL` in `.env` matches your current ngrok URL. Confirm backend is running on port 8000. |
| "Please wait…" then silence | Azure auth failure | Run `az login`. Check you have "Cognitive Services OpenAI User" role. Look at backend logs for errors. |
| `ModuleNotFoundError: aiohttp` | Missing async dependency | Run `pip install aiohttp` in the backend venv. |
| `ValueError: too many values to unpack` | audioop API mismatch | Ensure `audioop.ulaw2lin()` call does not unpack a tuple (it returns bytes directly). |
| ngrok URL changed | Free tier rotates URLs on restart | Update `PUBLIC_URL` in `.env` and restart the backend. |

---

## Configuration

| Variable | Description |
|----------|-------------|
| `TWILIO_ACCOUNT_SID` | Your Twilio Account SID (starts with `AC`) |
| `TWILIO_AUTH_TOKEN` | Twilio Auth Token |
| `TWILIO_PHONE_NUMBER` | Your Twilio phone number (E.164 format) |
| `AZURE_OPENAI_ENDPOINT` | Azure OpenAI endpoint URL |
| `AZURE_OPENAI_DEPLOYMENT` | Deployment name (default: `gpt-4o-realtime-preview`) |
| `AZURE_OPENAI_API_VERSION` | API version (default: `2025-04-01-preview`) |
| `PUBLIC_URL` | Public URL of your backend (ngrok URL) |
| `SYSTEM_PROMPT` | System prompt for the AI agent |
| `VOICE` | Voice for speech synthesis (default: `alloy`) |

> **Note:** No Azure OpenAI API key is needed — authentication uses `DefaultAzureCredential` (Azure CLI, managed identity, etc.).

---

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/call` | Place outbound call |
| `GET` | `/api/calls` | List active calls |
| `POST` | `/twilio/twiml` | TwiML response for call flow |
| `POST` | `/twilio/status` | Twilio status callback |
| `WS` | `/ws/media/{call_id}` | Twilio media stream (audio) |
| `WS` | `/ws/events/{call_id}` | Frontend live transcript |

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Frontend | React 19 + Vite |
| Backend | Python 3.11+ / FastAPI / Uvicorn |
| Telephony | Twilio Programmable Voice + Media Streams |
| AI | Azure OpenAI GPT-Realtime (Voice Live API) |
| Auth | DefaultAzureCredential (azure-identity) |
| Audio | audioop (mulaw ↔ PCM16) |
| Tunnel | ngrok (local dev only) |

---

## Local Development vs Production

### Why ngrok is Needed Locally

Twilio requires **publicly accessible HTTPS URLs** for two critical callbacks:

1. **TwiML webhook** (`POST /twilio/twiml`) — Twilio fetches this when the callee answers to get call instructions
2. **Media Stream WebSocket** (`WSS /ws/media/{call_id}`) — Twilio opens a WebSocket to stream bidirectional audio

Since your FastAPI backend runs on `localhost:8000`, Twilio can't reach it directly. **ngrok** creates a secure tunnel from a public URL (e.g. `https://abc123.ngrok-free.app`) to your local machine.

```
Internet                          Your Machine
────────                          ────────────
Twilio servers
    │
    │  POST https://abc123.ngrok-free.app/twilio/twiml
    │  WSS  wss://abc123.ngrok-free.app/ws/media/{id}
    ▼
┌──────────┐         ┌──────────────┐
│  ngrok   │────────▶│  localhost    │
│  tunnel  │  :8000  │  FastAPI      │
└──────────┘         └──────────────┘
```

**Limitations of ngrok in development:**
- Free plan URLs change on every restart — you must update `PUBLIC_URL` in `.env` each time
- Free plan sessions expire after ~2 hours
- Latency is slightly higher due to the extra hop

### Production Deployment

In production, you **do not need ngrok**. Deploy the backend to a service with a stable public URL:

| Platform | How it works |
|----------|-------------|
| **Azure Container Apps** | Deploy the FastAPI container; gets a stable HTTPS URL automatically. Use managed identity for `DefaultAzureCredential`. |
| **Azure App Service** | Deploy as a Python web app. Built-in WebSocket support. Managed identity for Azure OpenAI auth. |
| **Azure VM + NGINX** | Run behind a reverse proxy with a TLS certificate. Configure the public URL in Twilio. |
| **Any cloud with a public IP** | As long as it exposes HTTPS and WSS endpoints, Twilio can reach it. |

**Key production changes:**

```
# .env (production)
PUBLIC_URL=https://your-app.azurecontainerapps.io     # Stable URL, no ngrok
```

- **`PUBLIC_URL`** → Set to your deployed service's HTTPS URL
- **Authentication** → `DefaultAzureCredential` automatically picks up managed identity (no `az login` needed)
- **Frontend** → Build with `npm run build` and serve as static files, or deploy to Azure Static Web Apps
- **TLS** → Handled by the platform (App Service, Container Apps) or your reverse proxy
- **WebSocket scaling** → Ensure your platform supports long-lived WebSocket connections (Azure Container Apps and App Service both do)

---

## License

MIT
