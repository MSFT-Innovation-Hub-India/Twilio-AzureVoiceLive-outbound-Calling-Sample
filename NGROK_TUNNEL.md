# How ngrok Tunneling Works

This document explains what happens at the protocol level when ngrok "forwards"
or "tunnels" a WebSocket frame between Twilio and your local FastAPI server.

---

## Overview

ngrok is **not** a single end-to-end connection. It creates a relay through
ngrok's cloud servers, stitching together three persistent connections:

```
Twilio                 ngrok Cloud Relay              Your Machine
  │                         │                              │
  │── WSS (TLS) ───────────▶│                              │
  │   wss://ac3d-...        │◀──── persistent TLS ────────│  ngrok CLI
  │   .ngrok-free.app       │      tunnel (outbound)       │  (ngrok http 8000)
  │                         │                              │
  │                         │       WS / HTTP ────────────▶│  localhost:8000
  │                         │                              │  (FastAPI)
```

---

## The Three Connections

### 1. ngrok CLI → ngrok Cloud (the tunnel)

When you run `ngrok http 8000`, the ngrok CLI on your machine opens a
**persistent outbound TLS connection** to ngrok's cloud servers. This is the
"tunnel."

- **Direction:** Your machine → ngrok cloud (outbound)
- **Protocol:** TLS-encrypted, multiplexed proprietary protocol
- **Initiated by:** The ngrok CLI process on your machine
- **Why outbound matters:** Because your machine initiates the connection, no
  inbound firewall rules or port forwarding are needed. The tunnel punches
  through NAT and firewalls naturally.
- **Lifetime:** Stays open the entire time ngrok is running

This is the key insight — there is no "ngrok agent" to install separately. The
`ngrok` CLI you run **is** the tunnel endpoint.

### 2. Twilio → ngrok Cloud (the public WebSocket)

Twilio opens a standard **WSS** (WebSocket Secure) connection to the public
ngrok URL:

```
wss://ac3d-167-220-238-19.ngrok-free.app/ws/media/{call_id}
```

- **Direction:** Twilio → ngrok cloud
- **Protocol:** WSS (WebSocket over TLS, standard RFC 6455)
- **Initiated by:** Twilio Media Streams after processing the `<Stream>` TwiML
- **Lifetime:** Persistent for the duration of the phone call (~50 frames/sec)

Twilio has no idea your server is local. It just sees a regular `wss://` URL.

### 3. ngrok CLI → localhost:8000 (the local connection)

The ngrok CLI on your machine opens a local connection to FastAPI:

```
ws://localhost:8000/ws/media/{call_id}
```

- **Direction:** ngrok CLI → FastAPI (loopback)
- **Protocol:** WS (plain WebSocket, no TLS needed on localhost)
- **Initiated by:** The ngrok CLI when it receives an incoming connection
  through the tunnel
- **Lifetime:** Mirrors the Twilio connection — opens and closes with it

---

## What "Forwards the WebSocket Frame" Means

When ARCHITECTURE.md says ngrok "forwards the WebSocket frame," here is what
literally happens for a single audio frame:

### Inbound (Twilio → FastAPI)

```
Step 1: Twilio sends a WebSocket frame to ngrok cloud
        ┌─────────────────────────────────────────────┐
        │ {                                           │
        │   "event": "media",                         │
        │   "media": {                                │
        │     "payload": "<base64 mulaw audio>"       │
        │   }                                         │
        │ }                                           │
        └─────────────────────────────────────────────┘
              │
              │  WSS (TLS) over public internet
              ▼
Step 2: ngrok cloud relay receives the frame
        Copies the frame payload byte-for-byte
              │
              │  Pushes down the persistent tunnel
              │  (TLS-encrypted proprietary protocol)
              ▼
Step 3: ngrok CLI on your machine receives the frame
        Writes the identical frame into the local WS connection
              │
              │  WS (plain) on localhost
              ▼
Step 4: FastAPI receives the frame at /ws/media/{call_id}
        MediaBridge._process_twilio_message() handles it
```

### Outbound (FastAPI → Twilio)

The exact reverse — FastAPI writes a media frame to the local WebSocket, ngrok
CLI pushes it up the tunnel, ngrok cloud relays it to Twilio's WSS connection.

---

## Latency Impact

The ngrok relay hop adds **~30–80ms** of round-trip latency compared to a
direct connection:

| Segment | Typical Latency |
|---------|----------------|
| Twilio → ngrok cloud | ~10-30ms (depends on Twilio region → ngrok PoP) |
| ngrok cloud → your machine (tunnel) | ~10-40ms (depends on your ISP) |
| ngrok CLI → localhost | <1ms (loopback) |
| **Total overhead (one direction)** | **~20-70ms** |

---

## Production: ngrok Is Eliminated

ngrok is a **development-only tool**. In production (Azure Container Apps),
it is replaced entirely:

```
DEVELOPMENT:
Twilio ──WSS──▶ ngrok cloud ──tunnel──▶ ngrok CLI ──WS──▶ FastAPI:8000
                 (3 connections)         (your machine)
                 ~30-80ms overhead

PRODUCTION:
Twilio ──WSS──▶ Container Apps ingress ──▶ FastAPI:8000 (same container)
                (1 connection)
                ~2-5ms (same Azure region)
```

Azure Container Apps provides everything ngrok did:

| ngrok provided | Container Apps equivalent |
|---|---|
| Public HTTPS URL | `your-app.azurecontainerapps.io` |
| TLS termination | Managed certificates, automatic renewal |
| WebSocket support | `transport: auto` in ingress config |
| NAT/firewall traversal | Not needed — container is internet-facing |

The only change: set `PUBLIC_URL` in `.env` to the Container Apps URL. The
FastAPI code, MediaBridge, and Azure session code remain identical.

See [PRODUCTION.md](PRODUCTION.md) for the full production architecture and
scaling guide.

This is why the [production guide](PRODUCTION.md) recommends replacing ngrok
with a direct public endpoint (e.g., Azure Container Apps) in production — it
eliminates the extra hop entirely.

---

## Why ngrok Works Without Inbound Firewall Rules

Traditional port forwarding requires opening an inbound port on your
router/firewall. ngrok avoids this because:

1. The ngrok CLI **initiates** the tunnel connection outbound to ngrok's cloud
2. Once established, the tunnel is bidirectional — ngrok's cloud can push data
   back down the same connection
3. From your firewall's perspective, it looks like any other outbound HTTPS
   connection

This is why ngrok "just works" in corporate networks, behind NAT, and without
admin privileges.
