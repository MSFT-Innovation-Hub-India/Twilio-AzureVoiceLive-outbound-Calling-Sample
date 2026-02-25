# Production Architecture & Scaling Guide

This document covers the design considerations, architectural changes, and
operational practices needed to run the Twilio ↔ Azure Voice Live bridge at
production scale — handling tens to hundreds of concurrent calls.

---

## Table of Contents

- [Current Architecture — What Already Works](#current-architecture--what-already-works)
- [Scaling Bottlenecks](#scaling-bottlenecks)
- [Production Architecture](#production-architecture)
- [1. Credential Caching](#1-credential-caching)
- [2. Offloading CPU-Bound Audio Conversion](#2-offloading-cpu-bound-audio-conversion)
- [3. Backpressure with Bounded Queues](#3-backpressure-with-bounded-queues)
- [4. Multi-Worker Deployment](#4-multi-worker-deployment)
- [5. Graceful Shutdown](#5-graceful-shutdown)
- [6. Observability](#6-observability)
- [7. Infrastructure Recommendations](#7-infrastructure-recommendations)
- [Capacity Planning](#capacity-planning)

---

## Current Architecture — What Already Works

The sample is already designed with per-call isolation:

```
                    ┌──────────────────────────────────────────┐
                    │            FastAPI Process               │
                    │                                          │
  Twilio WS ──────▶│  active_sessions = {                     │
  (Call A)          │      "call-A": MediaBridge(A),  ◀──────▶ Azure WS (A)
                    │      "call-B": MediaBridge(B),  ◀──────▶ Azure WS (B)
  Twilio WS ──────▶│      "call-C": MediaBridge(C),  ◀──────▶ Azure WS (C)
  (Call B)          │  }                                       │
                    │                                          │
  Twilio WS ──────▶│  Each MediaBridge owns:                  │
  (Call C)          │    • Its own Twilio WebSocket            │
                    │    • Its own Azure WebSocket             │
                    │    • Its own transcript buffer           │
                    └──────────────────────────────────────────┘
```

| Property | Status |
|----------|--------|
| Per-call state isolation | **Yes** — each `MediaBridge` holds its own WebSocket connections and buffers |
| No shared mutable audio state | **Yes** — `mulaw_to_pcm16` / `pcm16_to_mulaw` are stateless functions |
| Concurrent WebSocket handling | **Yes** — FastAPI + asyncio handles multiple connections on one thread |
| No audio cross-talk | **Yes** — audio streams are fully independent per `call_id` |

This means **the current code can handle multiple simultaneous calls correctly**.
The constraints that appear at scale are operational, not architectural.

---

## Scaling Bottlenecks

| # | Bottleneck | Impact | Severity |
|---|-----------|--------|----------|
| 1 | **New `DefaultAzureCredential` per call** | Each call creates a fresh credential, probing IMDS (~7s timeout on non-Azure hosts), then falling back to CLI. Adds latency to every call setup. | High |
| 2 | **CPU-bound audio conversion on the event loop** | `audioop.ulaw2lin`, `audioop.ratecv`, `audioop.lin2ulaw` are C functions that block the async event loop. With many concurrent calls, this creates head-of-line blocking. | High |
| 3 | **No backpressure** | If Azure is slow to consume audio, Twilio audio buffers grow unbounded in memory. | Medium |
| 4 | **Single-process state** | `active_sessions`, `call_metadata`, `event_subscribers` are in-process dicts. A single process caps CPU and memory. | Medium |
| 5 | **No graceful shutdown** | On SIGTERM, active WebSocket connections (Twilio and Azure) drop without cleanup. Calls hang until Twilio times out. | Low-Medium |
| 6 | **No observability** | No metrics, no distributed tracing, no structured logging for production debugging. | Low-Medium |

---

## Production Architecture

```
                           ┌───────────────────────────┐
                           │   Azure Load Balancer /   │
   Twilio ────────────────▶│   Container Apps Ingress │
   (HTTPS + WSS)           │   (sticky sessions)       │
                           └────┬────────┬────────┬────┘
                                │        │        │
                           ┌────▼──┐ ┌───▼───┐ ┌──▼────┐
                           │Worker │ │Worker │ │Worker │
                           │  1    │ │  2    │ │  3    │
                           │       │ │       │ │       │
                           │ N     │ │ N     │ │ N     │
                           │bridges│ │bridges│ │bridges│
                           └───┬───┘ └───┬───┘ └───┬───┘
                               │         │         │
                               ▼         ▼         ▼
                         ┌──────────────────────────────┐
                         │  Azure OpenAI / Voice Live   │
                         │  (multiple WSS connections)  │
                         └──────────────────────────────┘
```

---

### 1. Credential Caching

**Problem:** Each call creates `DefaultAzureCredential()`, which probes IMDS
(~7s timeout on non-Azure machines) before falling back to `AzureCliCredential`.

**Solution:** Create a single credential instance at module level. The Azure
Identity SDK handles token caching and refresh internally.

```python
# azure_voicelive_client.py / azure_gpt_realtime_client.py

from azure.identity.aio import DefaultAzureCredential

# Module-level — shared across all sessions, token cache built-in
_credential = DefaultAzureCredential()

class AzureVoiceLiveSession:
    async def connect(self):
        token = await _credential.get_token(_AZURE_AI_SCOPE)
        # ... use token.token
```

**Impact:** Eliminates the 7s IMDS probe on every call. Token refresh happens
transparently when the cached token nears expiry.

---

### 2. Offloading CPU-Bound Audio Conversion

**Problem:** `audioop` functions are CPU-bound C code. When called directly in
the async event loop, they block all other coroutines. With 50+ concurrent calls
each sending 20ms audio chunks (50 chunks/sec), that's 2,500+ blocking calls/sec.

**Solution:** Offload to a thread pool using `asyncio.to_thread()`:

```python
import asyncio

async def _process_twilio_message(self, message: dict):
    # ...
    if event == "media":
        mulaw_audio = base64.b64decode(payload)

        # Offload CPU-bound conversion to thread pool
        pcm_audio = await asyncio.to_thread(mulaw_to_pcm16, mulaw_audio)

        if self.azure_session:
            await self.azure_session.send_audio(pcm_audio)

async def _send_audio_to_twilio(self, pcm_audio: bytes):
    # Offload CPU-bound conversion to thread pool
    mulaw_audio = await asyncio.to_thread(pcm16_to_mulaw, pcm_audio)
    # ... send to Twilio
```

**Impact:** The event loop stays free to service other WebSocket I/O while audio
conversion runs on a background thread. Python's default `ThreadPoolExecutor`
uses `min(32, os.cpu_count() + 4)` threads.

For very high concurrency, consider a `ProcessPoolExecutor` to leverage
multiple CPU cores (audioop releases the GIL for its C operations):

```python
from concurrent.futures import ProcessPoolExecutor

_audio_pool = ProcessPoolExecutor(max_workers=4)

async def convert_async(fn, *args):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_audio_pool, fn, *args)
```

---

### 3. Backpressure with Bounded Queues

**Problem:** If the Azure WebSocket is slower than Twilio's audio stream (e.g.,
during a model inference spike), decoded audio buffers accumulate in memory.

**Solution:** Insert a bounded `asyncio.Queue` between Twilio ingestion and
Azure forwarding. Drop oldest audio when the queue is full (audio streaming
tolerates small gaps better than unbounded latency).

```python
class MediaBridge:
    def __init__(self, call_sid, backend="gpt-realtime"):
        # ...
        self._audio_queue: asyncio.Queue = asyncio.Queue(maxsize=100)

    async def handle_twilio_stream(self, websocket):
        # Start a consumer task that drains the queue → Azure
        consumer = asyncio.create_task(self._audio_consumer())
        try:
            # ... existing message loop puts audio into queue
            pass
        finally:
            consumer.cancel()

    async def _process_twilio_message(self, message):
        if event == "media":
            pcm_audio = await asyncio.to_thread(mulaw_to_pcm16, mulaw_audio)
            try:
                self._audio_queue.put_nowait(pcm_audio)
            except asyncio.QueueFull:
                # Drop oldest frame to prevent memory growth
                self._audio_queue.get_nowait()
                self._audio_queue.put_nowait(pcm_audio)

    async def _audio_consumer(self):
        while not self._closed:
            pcm_audio = await self._audio_queue.get()
            if self.azure_session:
                await self.azure_session.send_audio(pcm_audio)
```

**Impact:** Memory usage is bounded per call. Under normal conditions the queue
stays near-empty; under backpressure, audio is shed gracefully.

---

### 4. Multi-Worker Deployment

**Problem:** A single Python process has limited CPU capacity and a single
event loop. All call state (WebSockets, bridges) lives in-process.

**Solution:** Run multiple Uvicorn workers behind a load balancer with
**sticky sessions** (session affinity).

```bash
# Production start command
uvicorn main:app --host 0.0.0.0 --port 8000 --workers 4
```

**Why sticky sessions are required:** Twilio opens its WebSocket to a specific
worker process (via `/ws/media/{call_id}`). The `MediaBridge` and Azure session
for that call live in that worker's memory. If a subsequent Twilio HTTP callback
(e.g., `/twilio/status`) is routed to a different worker, it won't find the
session. Sticky sessions ensure all requests for a given call hit the same worker.

| Platform | Sticky Session Config |
|----------|----------------------|
| **Azure Container Apps** | `sessionAffinity: sticky` in ingress config |
| **Azure App Service** | ARR Affinity enabled by default |
| **NGINX** | `ip_hash` or `sticky cookie` directive |
| **AWS ALB** | Stickiness enabled on target group |

**Scaling limits per worker:**

| Resource | Approximate Limit |
|----------|-------------------|
| Concurrent WebSocket connections | ~1,000 per worker (OS-level fd limits) |
| Concurrent audio bridges | ~50-100 per worker (CPU-bound audio conversion) |
| Memory per call | ~2-5 MB (WebSocket buffers + audio queue) |

---

### 5. Graceful Shutdown

**Problem:** `SIGTERM` kills the process immediately. Active calls are dropped
without notifying Twilio or closing Azure WebSockets cleanly.

**Solution:** Register a shutdown handler that drains active sessions:

```python
import signal

@app.on_event("shutdown")
async def shutdown_event():
    """Gracefully close all active media bridges on shutdown."""
    logger.info(f"Shutting down — closing {len(active_sessions)} active sessions")
    close_tasks = [bridge.close() for bridge in list(active_sessions.values())]
    await asyncio.gather(*close_tasks, return_exceptions=True)
    logger.info("All sessions closed")
```

For zero-downtime deployments, use a **pre-stop hook** that:
1. Stops accepting new calls (remove from load balancer)
2. Waits for active calls to complete (with a timeout)
3. Force-closes remaining sessions
4. Exits

---

### 6. Observability

#### Structured Logging

Replace the basic `logging` config with structured JSON logs for log aggregation:

```python
import logging
import json

class JSONFormatter(logging.Formatter):
    def format(self, record):
        return json.dumps({
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "call_id": getattr(record, "call_id", None),
        })
```

#### Metrics to Track

| Metric | Type | Description |
|--------|------|-------------|
| `active_calls` | Gauge | Number of currently active media bridges |
| `call_setup_duration_seconds` | Histogram | Time from POST /api/call to Azure WS connected |
| `audio_conversion_duration_seconds` | Histogram | Time spent in mulaw↔PCM16 conversion |
| `azure_ws_latency_seconds` | Histogram | Round-trip latency to Azure Voice Live |
| `audio_queue_depth` | Gauge | Current depth of the backpressure queue per call |
| `calls_total` | Counter | Total calls placed (label: backend, status) |

#### Application Insights Integration

For Azure-hosted deployments, integrate with Application Insights using the
OpenTelemetry SDK:

```bash
pip install azure-monitor-opentelemetry
```

```python
from azure.monitor.opentelemetry import configure_azure_monitor

configure_azure_monitor(connection_string="InstrumentationKey=...")
```

This gives you distributed tracing, live metrics, and failure analysis in the
Azure portal.

---

### 7. Infrastructure Recommendations

#### Azure Container Apps (Recommended)

```
┌──────────────────────────────────────────────────┐
│               Azure Container Apps               │
│                                                  │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐        │
│  │ Replica 1│  │ Replica 2│  │ Replica 3│  ←── Auto-scale on
│  │ 4 workers│  │ 4 workers│  │ 4 workers│      concurrent connections
│  └──────────┘  └──────────┘  └──────────┘        │
│                                                  │
│  Ingress: session affinity = sticky              │
│  Min replicas: 1                                 │
│  Max replicas: 10                                │
│  Scale rule: concurrent connections > 50         │
│  WebSocket support: built-in                     │
│  Managed identity: system-assigned               │
└──────────────────────────────────────────────────┘
         │                            │
         ▼                            ▼
┌──────────────────┐    ┌──────────────────────────┐
│  Azure OpenAI /  │    │  Twilio                  │
│  Voice Live API  │    │  (callbacks via public   │
│                  │    │   Container Apps URL)    │
└──────────────────┘    └──────────────────────────┘
```

**Key configuration:**

```yaml
# Container Apps ingress config
ingress:
  external: true
  targetPort: 8000
  transport: auto          # Supports both HTTP and WebSocket
  sessionAffinity: sticky  # Required for WebSocket state

# Scaling
scale:
  minReplicas: 1
  maxReplicas: 10
  rules:
    - name: concurrent-connections
      custom:
        type: tcp
        metadata:
          concurrentConnections: "50"
```

#### Resource Sizing Guide

| Concurrent Calls | Workers | vCPUs | Memory | Replicas |
|-------------------|---------|-------|--------|----------|
| 1-10 | 2 | 1 | 2 GB | 1 |
| 10-50 | 4 | 2 | 4 GB | 1-2 |
| 50-200 | 4 | 4 | 8 GB | 2-5 |
| 200-500 | 4 | 4 | 8 GB | 5-10 |

**Memory formula:** ~5 MB per call (WebSocket buffers + audio queue + overhead).
200 concurrent calls ≈ 1 GB of active call memory + base process overhead.

---

## Capacity Planning

### Twilio Limits

| Resource | Default Limit |
|----------|---------------|
| Concurrent outbound calls | 1 per second (CPS) for new accounts |
| Max concurrent calls | Varies by account (typically 100-500) |
| Media Stream duration | Up to 4 hours |
| Media Stream payload | ~50 messages/sec (20ms audio chunks) |

### Azure OpenAI Realtime Limits

| Resource | Limit |
|----------|-------|
| Concurrent sessions per deployment | Varies by tier and quota |
| Max session duration | Deployment-dependent |
| Audio input rate | Real-time (~24,000 samples/sec) |

### Bandwidth

| Direction | Per Call | 100 Calls |
|-----------|---------|-----------|
| Twilio → Backend (mulaw 8kHz) | ~64 kbps | ~6.4 Mbps |
| Backend → Azure (PCM16 24kHz) | ~384 kbps | ~38.4 Mbps |
| Azure → Backend (PCM16 24kHz) | ~384 kbps | ~38.4 Mbps |
| Backend → Twilio (mulaw 8kHz) | ~64 kbps | ~6.4 Mbps |

**Total bandwidth per call:** ~896 kbps (~1 Mbps including overhead).
100 concurrent calls: ~100 Mbps.

---

## Summary Checklist

- [ ] Cache `DefaultAzureCredential` at module level
- [ ] Offload `audioop` calls to `asyncio.to_thread()`
- [ ] Add bounded queue for audio backpressure
- [ ] Configure multi-worker Uvicorn with sticky sessions
- [ ] Add graceful shutdown handler
- [ ] Integrate structured logging + Application Insights
- [ ] Deploy to Azure Container Apps with session affinity
- [ ] Configure auto-scaling rules
- [ ] Set up health checks and alerting
- [ ] Load test with target concurrent call count
