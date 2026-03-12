# Twilio ↔ Azure OpenAI Realtime — Outbound Voice Agent

A sample application that places **outbound PSTN calls** via **Twilio** and
connects the callee to an AI voice agent powered by the **Azure OpenAI Realtime API
(`gpt-4o-realtime-preview`)**. The entire conversation — user speech in, AI speech out —
happens in real time over the phone.

> **Note:** This sample also includes an alternative client (`azure_voicelive_client.py`)
> that connects to the **Azure Voice Live API** — a separate service hosted on
> Azure AI Services (Cognitive Services) endpoints. See
> [Using Azure Voice Live API instead](#using-azure-voice-live-api-instead) below.

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
│   ├── azure_gpt_realtime_client.py  # Azure OpenAI Realtime API WebSocket client
│   ├── azure_voicelive_client.py  # Azure Voice Live API WebSocket client
│   ├── cosmosdb_client.py   # Azure Cosmos DB client (interview result persistence)
│   ├── cosmosdb_network.py  # Cosmos DB network access check (management API)
│   ├── scenario_manager.py  # Loads and manages scenario configurations
│   ├── requirements.txt     # Python dependencies
│   ├── .env.example         # Environment variable template
│   ├── .env                 # Your local config (git-ignored)
│   ├── scenarios/           # Scenario JSON configs (prompts, VAD, tools)
│   └── results/             # Local JSON backups of interview results
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
| **azure_gpt_realtime_client.py** | Opens a WSS connection to **Azure OpenAI Realtime API** (`/openai/realtime`). Authenticates via `DefaultAzureCredential` with the `cognitiveservices.azure.com` scope. Configures server VAD + Whisper transcription. Streams audio in/out and emits transcript events. |
| **azure_voicelive_client.py** | Connects to the **Azure Voice Live API** (`/voice-live/realtime`) on an Azure AI Services (Cognitive Services) endpoint. Uses the `ai.azure.com` scope, supports Azure Speech voices, noise suppression, and echo cancellation. Same interface as `azure_gpt_realtime_client.py`. |
| **cosmosdb_client.py** | Async Azure Cosmos DB client using `DefaultAzureCredential`. Eagerly initialised at startup to avoid credential timeout during calls. Upserts interview result documents. |
| **cosmosdb_network.py** | Checks Cosmos DB network access via the Azure Management API. Reports whether public access is enabled so the UI can warn before a call. |
| **scenario_manager.py** | Loads scenario JSON files from `scenarios/`. Each scenario defines a system prompt, VAD settings, voice config, and tools. |
| **config.py** | Loads `.env` and exposes typed settings. Builds the Azure WSS URL from endpoint, deployment, and API version. |
| **App.jsx** | React UI with phone number input, call/hangup controls, status badge, and a chat-style live transcript view. |

### Additional Documentation

| Document | Description |
|----------|-------------|
| [ARCHITECTURE.md](ARCHITECTURE.md) | Solution architecture diagram — development (ngrok) and production (Azure Container Apps) diagrams, per-call isolation model, 33 numbered flow steps, interruption handling, and Cosmos DB persistence flows. |
| [AUDIO_PIPELINE.md](AUDIO_PIPELINE.md) | Deep dive into mulaw vs PCM16 encoding, sample rate resampling, byte-level walkthrough, and why the conversion pipeline is necessary. |
| [PRODUCTION.md](PRODUCTION.md) | Production architecture guide — dev→prod transition (ngrok elimination), in-process coupling constraints, scaling bottlenecks, multi-worker deployment, Cosmos DB at scale, interruption handling considerations, and Azure Container Apps configuration. |
| [NGROK_TUNNEL.md](NGROK_TUNNEL.md) | Protocol-level explanation of how ngrok tunneling works — the three connections, frame-by-frame walkthrough, latency impact, and why it's replaced by Azure Container Apps in production. |

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

> **Development only.** ngrok tunnels Twilio's traffic to your local machine.
> In production (Azure Container Apps), ngrok is eliminated entirely — Twilio
> connects directly to the Container Apps public URL. See
> [NGROK_TUNNEL.md](NGROK_TUNNEL.md) for protocol-level details and
> [PRODUCTION.md](PRODUCTION.md) for the production architecture.

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
# ── Twilio ───────────────────────────────────────────────────
TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TWILIO_AUTH_TOKEN=your_auth_token_here
TWILIO_PHONE_NUMBER=+12131234567

# ── Azure OpenAI Realtime API (GPT-Realtime backend) ────────
# Authentication uses DefaultAzureCredential (managed identity / Azure CLI)
AZURE_OPENAI_ENDPOINT=https://your-resource.cognitiveservices.azure.com
AZURE_OPENAI_DEPLOYMENT=gpt-realtime
AZURE_OPENAI_API_VERSION=2025-04-01-preview

# ── Azure Voice Live API (Voice Live backend) ───────────────
AZURE_VOICE_LIVE_ENDPOINT=https://your-resource.cognitiveservices.azure.com
AZURE_VOICE_LIVE_API_VERSION=2025-05-01-preview
VOICE_LIVE_MODEL=gpt-realtime
AZURE_TTS_VOICE_NAME=en-IN-AartiIndicNeural

# ── Server ───────────────────────────────────────────────────
HOST=0.0.0.0
PORT=8000
PUBLIC_URL=https://xxxx-xxx-xxx-xxx-xx.ngrok-free.app

# ── Agent Configuration ─────────────────────────────────────
SYSTEM_PROMPT=You are a helpful voice assistant. Be concise and conversational.
VOICE=alloy

# ── Azure Cosmos DB (interview result persistence) ──────────
AZURE_COSMOS_DB_ENDPOINT=https://your-account.documents.azure.com:443/
AZURE_COSMOS_DB_DATABASE=db001
AZURE_COSMOS_DB_CONTAINER=sales-screening-oall-output

# ── Azure Cosmos DB Management (auto network access check) ──
AZURE_SUBSCRIPTION_ID=your-subscription-id
AZURE_RESOURCE_GROUP=your-resource-group
AZURE_COSMOS_DB_ACCOUNT_NAME=your-cosmos-account-name
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

### Twilio

| Variable | Description |
|----------|-------------|
| `TWILIO_ACCOUNT_SID` | Your Twilio Account SID (starts with `AC`) |
| `TWILIO_AUTH_TOKEN` | Twilio Auth Token |
| `TWILIO_PHONE_NUMBER` | Your Twilio phone number (E.164 format) |

### Azure OpenAI Realtime API (GPT-Realtime backend)

| Variable | Description |
|----------|-------------|
| `AZURE_OPENAI_ENDPOINT` | Azure AI Services / OpenAI endpoint URL |
| `AZURE_OPENAI_DEPLOYMENT` | Deployment name (e.g. `gpt-realtime`) |
| `AZURE_OPENAI_API_VERSION` | API version (default: `2025-04-01-preview`) |

### Azure Voice Live API (Voice Live backend)

| Variable | Description |
|----------|-------------|
| `AZURE_VOICE_LIVE_ENDPOINT` | Azure AI Services (Cognitive Services) endpoint URL |
| `AZURE_VOICE_LIVE_API_VERSION` | API version (default: `2025-05-01-preview`) |
| `VOICE_LIVE_MODEL` | Model deployment name (e.g. `gpt-realtime`) |
| `AZURE_TTS_VOICE_NAME` | Azure Speech voice for TTS (e.g. `en-IN-AartiIndicNeural`) |

### Server

| Variable | Description |
|----------|-------------|
| `HOST` | Bind address (default: `0.0.0.0`) |
| `PORT` | Server port (default: `8000`) |
| `PUBLIC_URL` | Public URL of your backend (ngrok URL in dev) |

### Agent

| Variable | Description |
|----------|-------------|
| `SYSTEM_PROMPT` | System prompt for the AI agent |
| `VOICE` | Voice for GPT-Realtime speech synthesis (default: `alloy`) |

### Azure Cosmos DB

| Variable | Description |
|----------|-------------|
| `AZURE_COSMOS_DB_ENDPOINT` | Cosmos DB account endpoint (e.g. `https://account.documents.azure.com:443/`) |
| `AZURE_COSMOS_DB_DATABASE` | Database name (e.g. `db001`) |
| `AZURE_COSMOS_DB_CONTAINER` | Container name for interview results |
| `AZURE_SUBSCRIPTION_ID` | Azure subscription ID (for network access check) |
| `AZURE_RESOURCE_GROUP` | Resource group containing the Cosmos DB account |
| `AZURE_COSMOS_DB_ACCOUNT_NAME` | Cosmos DB account name (for management API calls) |

> **Note:** No API keys are needed for any Azure service — all authentication uses `DefaultAzureCredential` (Azure CLI, managed identity, etc.).

---

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/call` | Place outbound call |
| `GET` | `/api/calls` | List active calls |
| `GET` | `/api/scenarios` | List available scenarios |
| `GET` | `/api/cosmosdb/network-status` | Check Cosmos DB network accessibility |
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
| AI | Azure OpenAI GPT-Realtime / Azure Voice Live API |
| Persistence | Azure Cosmos DB for NoSQL |
| Auth | DefaultAzureCredential (azure-identity) — no API keys |
| Audio | audioop (mulaw ↔ PCM16), gpt-4o-transcribe |
| Tunnel | ngrok (local dev only) |

---

## Why Azure Cosmos DB?

Interview results (scores, transcripts, metadata) must be persisted reliably
after every call. Azure Cosmos DB for NoSQL is used because:

1. **Schema-flexible documents** — Each call produces a JSON document with
   varying structure (different scenarios have different scoring fields).
   Cosmos DB's schemaless NoSQL model stores these directly without migrations.

2. **Low-latency writes** — Results are saved during the call via a
   `save_interview_results` function call from the AI model. Cosmos DB's
   single-digit-millisecond writes ensure this doesn't block the audio
   pipeline.

3. **DefaultAzureCredential (no keys)** — The same `DefaultAzureCredential`
   pattern used for Azure OpenAI and Voice Live API works for Cosmos DB,
   keeping the codebase key-free. In production, managed identity provides
   seamless auth with no secrets to rotate.

4. **Fire-and-forget persistence** — The Cosmos DB save is dispatched via
   `asyncio.ensure_future()` so it completes independently of the media
   bridge lifecycle. Even if the call disconnects immediately after the AI
   invokes the save function, the write still completes.

5. **Eager client initialisation** — The Cosmos DB client is initialised at
   application startup (`@app.on_event("startup")`) rather than on first use.
   This avoids a 4–5 second credential acquisition delay (IMDS timeout on
   non-Azure machines falling back to Azure CLI) during a live call.

### Cosmos DB Setup

1. Create an Azure Cosmos DB for NoSQL account
2. Create a database (e.g. `db001`) and container (e.g. `sales-screening-oall-output`)
   with `/id` as the partition key
3. Assign the **Cosmos DB Built-in Data Contributor** role to your identity:
   ```bash
   az cosmosdb sql role assignment create \
     --account-name <cosmos-account> \
     --resource-group <rg> \
     --role-definition-name "Cosmos DB Built-in Data Contributor" \
     --scope "/" \
     --principal-id <your-object-id>
   ```
4. Set the `AZURE_COSMOS_DB_*` variables in `.env`

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

## Interruption Handling (Barge-in) on Twilio PSTN

Allowing a caller to interrupt ("barge in") while the AI agent is speaking is
essential for natural conversation. This turned out to be one of the hardest
problems to solve when connecting Azure OpenAI Realtime / Voice Live API to a
real PSTN call via Twilio Media Streams.

### Why the "standard" approach doesn't work

Most Azure Realtime API quickstarts show a simple pattern:

1. Azure's server-side VAD fires `input_audio_buffer.speech_started`.
2. You assume Azure automatically cancels the in-progress response.
3. The user hears silence and the next response begins.

This works when the client is a browser or native app with *direct* audio
playback — audio deltas are played immediately as they arrive, so stopping the
playback loop instantly silences the agent.

**With Twilio PSTN, two additional challenges break this pattern:**

#### Challenge 1 — Twilio buffers outbound audio

Twilio Media Streams accepts `media` events from the backend and buffers them
in an internal playout queue. By the time Azure fires `speech_started`, Twilio
may already have **2–5 seconds of audio queued** that will keep playing
regardless of whether the backend stops sending new audio. Simply stopping the
flow of `response.audio.delta` packets does nothing to silence what's already
in Twilio's buffer.

**Solution:** On every `speech_started` event, immediately send a Twilio
[`clear`](https://www.twilio.com/docs/voice/media-streams/websocket-messages#clear-message)
message to flush the outbound audio buffer:

```python
msg = {"event": "clear", "streamSid": self.stream_sid}
await self.twilio_ws.send_text(json.dumps(msg))
```

#### Challenge 2 — Azure finishes generating before Twilio finishes playing

Azure generates audio much faster than real-time. A 10-second spoken response
may be fully generated and delivered to the backend in 2–3 seconds. Azure then
sends `response.done` and sets `_response_in_progress = False`. However,
Twilio is still playing back the buffered audio for another 7+ seconds.

If the interrupt handler only triggers when `_response_in_progress == True`,
the Twilio flush never happens because from Azure's perspective the response
is already complete.

**Solution:** **Always** flush the Twilio buffer on `speech_started`,
regardless of whether Azure is still generating:

```python
# speech_started handler
if self._on_interrupt:
    await self._on_interrupt()          # Always flush Twilio buffer

if self._response_in_progress:
    # Azure is still generating — also cancel + mute
    self._mute_audio = True
    await self.ws.send(json.dumps({"type": "response.cancel"}))
```

#### Challenge 3 — Stale audio deltas arrive after cancel

Even after sending `response.cancel` to Azure, a few more
`response.audio.delta` and `response.audio_transcript.done` messages arrive
before Azure acknowledges the cancellation with `response.done`. If these are
forwarded to Twilio they re-fill the buffer you just flushed.

**Solution:** A `_mute_audio` flag is set to `True` on interrupt. While
muted, all incoming `response.audio.delta` and transcript events are silently
dropped. The flag is reset to `False` when `response.done` arrives, ensuring
the next fresh response flows through normally.

### The complete interrupt sequence

```
User speaks        Backend (speech_started handler)       Twilio          Azure
─────────          ────────────────────────────────       ──────          ─────
  │                                                        │               │
  │  speech_started                                        │               │
  │──────────────────▶│                                    │               │
  │                   │  1. Send "clear" event             │               │
  │                   │───────────────────────────────────▶│               │
  │                   │  (flush queued audio immediately)  │               │
  │                   │                                    │               │
  │                   │  2. If response still in progress: │               │
  │                   │     a. Set _mute_audio = True      │               │
  │                   │     b. Send response.cancel        │               │
  │                   │────────────────────────────────────────────────────▶│
  │                   │                                    │               │
  │                   │  3. Drop any late audio.delta      │               │
  │                   │     (muted — don't forward)        │               │
  │                   │                                    │               │
  │                   │  4. response.done arrives           │               │
  │                   │◀────────────────────────────────────────────────────│
  │                   │     _mute_audio = False             │               │
  │                   │                                    │               │
  │  speech_stopped   │                                    │               │
  │──────────────────▶│                                    │               │
  │                   │  5. Azure processes user input     │               │
  │                   │     and generates new response     │               │
  │                   │◀────────────────────────────────────────────────────│
  │                   │  6. Forward fresh audio.delta       │               │
  │                   │───────────────────────────────────▶│  ▶ phone      │
```

### Key files involved

| File | Role in interruption handling |
|------|-------------------------------|
| `azure_voicelive_client.py` | Listens for `speech_started`, sends `response.cancel`, manages `_mute_audio` flag, calls `on_interrupt_callback` to flush Twilio. |
| `azure_gpt_realtime_client.py` | Same logic — both backends share identical interrupt handling. |
| `media_bridge.py` | Provides `_flush_twilio_audio()` which sends the Twilio `clear` event. Wired as `on_interrupt_callback`. |

### Does this work with both backends?

Yes. The interrupt handling is **identical** in both `azure_voicelive_client.py`
(Voice Live API) and `azure_gpt_realtime_client.py` (Azure OpenAI Realtime
API). Both clients:

- Always flush Twilio's buffer on `speech_started`
- Send `response.cancel` to Azure if a response is still being generated
- Mute stale audio/transcript deltas until `response.done`
- Call the same `on_interrupt_callback` → `_flush_twilio_audio()`

The only prerequisite is that the Twilio `Stream` WebSocket is active and the
`streamSid` is known (captured from the `start` event).

---

## Using Azure Voice Live API Instead

The default implementation (`azure_gpt_realtime_client.py`) connects directly to the **Azure OpenAI Realtime API** at `/openai/realtime`. An alternative client (`azure_voicelive_client.py`) connects to the **Azure Voice Live API** at `/voice-live/realtime` — a separate service hosted on Azure AI Services (Cognitive Services) endpoints.

Both backends are available at runtime — select which one to use via the **AI Backend** toggle in the UI before placing a call.

### Key Differences

| | Azure OpenAI Realtime (`azure_gpt_realtime_client.py`) | Azure Voice Live API (`azure_voicelive_client.py`) |
|---|---|---|
| **Endpoint** | Azure OpenAI resource | Azure AI Services (Cognitive Services) |
| **URL path** | `/openai/realtime` | `/voice-live/realtime` |
| **Auth scope** | `cognitiveservices.azure.com/.default` | `ai.azure.com/.default` |
| **Voice config** | Simple name (e.g. `alloy`) | Azure Speech voices (e.g. `en-US-AriaNeural`) |
| **Noise suppression** | — | `azure_deep_noise_suppression` |
| **Echo cancellation** | — | `server_echo_cancellation` |

### Configuration for Voice Live API

To use the Voice Live API backend, add these variables to your `.env`:

   ```ini
   # Azure Voice Live API (Cognitive Services endpoint)
   AZURE_VOICE_LIVE_ENDPOINT=https://your-resource.cognitiveservices.azure.com
   AZURE_VOICE_LIVE_API_VERSION=2025-05-01-preview
   VOICE_LIVE_MODEL=gpt-4o-realtime-preview
   AZURE_TTS_VOICE_NAME=en-US-AriaNeural
   ```

Ensure your identity has the right role on the Azure AI Services resource and `az login` to the correct tenant.

---

## License

MIT
