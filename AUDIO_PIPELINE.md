# Audio Pipeline Deep Dive — mulaw, PCM16, and Resampling

This document explains the audio encoding formats used in the Twilio ↔ Azure
Voice Live bridge, why conversions are necessary, and how the pipeline works
at the byte level.

---

## Table of Contents

- [The Two Worlds](#the-two-worlds)
- [What is mulaw (μ-law)?](#what-is-mulaw-μ-law)
- [What is PCM16?](#what-is-pcm16)
- [Why the Conversion is Necessary](#why-the-conversion-is-necessary)
- [Sample Rate Resampling](#sample-rate-resampling)
- [The Full Conversion Pipeline](#the-full-conversion-pipeline)
- [Byte-Level Walkthrough](#byte-level-walkthrough)
- [audioop — The Conversion Engine](#audioop--the-conversion-engine)
- [Why Not Use a Different Format?](#why-not-use-a-different-format)
- [Audio Quality Considerations](#audio-quality-considerations)

---

## The Two Worlds

The bridge sits between two systems that speak different audio languages:

| Property | Twilio (Telephony) | Azure Voice Live (AI) |
|----------|-------------------|----------------------|
| **Encoding** | mulaw (μ-law) | PCM16 (Linear) |
| **Sample Rate** | 8,000 Hz | 24,000 Hz |
| **Bit Depth** | 8 bits per sample | 16 bits per sample |
| **Bitrate** | 64 kbps | 384 kbps |
| **Transport** | Base64 over WebSocket JSON | Base64 over WebSocket JSON |
| **Origin** | PSTN telephony standard (ITU G.711) | Modern AI/ML audio standard |

These formats are fundamentally incompatible. The bridge must convert between
them in both directions, in real time, with minimal latency.

---

## What is mulaw (μ-law)?

**μ-law** (mu-law, G.711 μ) is a companding algorithm standardized by the ITU
in the 1970s for digital telephony. It's the encoding used on phone networks
(PSTN) in North America and Japan.

### How It Works

μ-law is a **logarithmic compression** scheme. It maps 14-bit linear audio
samples down to 8 bits using a logarithmic curve:

```
      Linear (PCM16)                    μ-law (8-bit)
   ┌─────────────────┐             ┌──────────────────┐
   │                 │             │                  │
   │  16-bit samples │  compress   │  8-bit samples   │
   │  -32768..+32767 │────────────▶│  0..255          │
   │                 │             │                  │
   │  Range: 96 dB   │             │  Range: ~72 dB   │
   └─────────────────┘             └──────────────────┘
```

The key insight is **non-uniform quantization**: quiet sounds get more
precision than loud sounds. This matches human hearing — we're more sensitive
to differences in quiet sounds than loud ones.

```
Linear (uniform quantization):
  ├──┼──┼──┼──┼──┼──┼──┼──┤   ← Equal step sizes everywhere
  Low                    High

μ-law (non-uniform quantization):
  ├┼┼┼┼──┼───┼────┼──────┤   ← Small steps for quiet, large for loud
  Low                    High
```

### Why Telephony Uses It

1. **Bandwidth efficiency:** 8 bits per sample × 8,000 samples/sec = **64 kbps** —
   fits exactly in one DS0 telephony channel.
2. **Perceptual quality:** The logarithmic curve preserves speech intelligibility
   far better than 8-bit linear PCM would (which sounds terrible for voice).
3. **Universal support:** Every phone network, PBX, and telephony API (including
   Twilio) speaks μ-law natively.

### The μ-law Formula

```
F(x) = sgn(x) · ln(1 + μ|x|) / ln(1 + μ)
```

Where `μ = 255` (for 8-bit encoding) and `x` is the normalized input (-1 to +1).

---

## What is PCM16?

**PCM16** (Pulse Code Modulation, 16-bit) is **linear, uncompressed digital
audio**. Each sample is a signed 16-bit integer representing the instantaneous
amplitude of the sound wave.

```
One PCM16 sample = 2 bytes (16 bits), little-endian, signed
Range: -32,768 to +32,767
```

### Sample Layout in Memory

```
Byte offset:  0    1    2    3    4    5    ...
             └─┬──┘   └─┬──┘   └─┬──┘
           Sample 0  Sample 1  Sample 2
           (int16)   (int16)   (int16)
```

### Why AI Models Use It

1. **Lossless representation:** No compression artifacts. The AI model receives
   exactly what was captured.
2. **Mathematical simplicity:** Linear samples can be directly fed into neural
   network tensor operations — no decoding step needed.
3. **Standard ML format:** Whisper, GPT-4o audio, and most speech models
   expect PCM16 at 16 kHz or 24 kHz.

---

## Why the Conversion is Necessary

The bridge **must** convert because Twilio and Azure speak incompatible formats:

```
Twilio sends:     mulaw, 8kHz, 8-bit     (64 kbps)
Azure expects:    PCM16, 24kHz, 16-bit    (384 kbps)
                  ▲
                  │
                  └── 6x more data!
```

Two transformations are needed:

### 1. Encoding Conversion (mulaw → PCM16)

μ-law compresses the dynamic range logarithmically. To get back to linear PCM16,
each 8-bit μ-law sample must be **expanded** back to a 16-bit linear sample
using the inverse of the μ-law curve.

```
                    Compression (14-bit → 8-bit)
    PCM16 ─────────────────────────────────────────▶ μ-law
              Logarithmic quantization
              Quiet sounds: fine steps
              Loud sounds: coarse steps

                    Expansion (8-bit → 16-bit)
    μ-law ─────────────────────────────────────────▶ PCM16
              Inverse logarithmic mapping
              Reconstructs linear amplitude
```

This is a **lossless** operation in the sense that no further quality is lost —
the original quantization error from the μ-law compression already occurred on
the phone network side.

### 2. Sample Rate Conversion (8 kHz → 24 kHz)

The sample rate determines how many times per second the sound wave is measured.
Higher rates capture higher frequencies:

| Sample Rate | Max Frequency (Nyquist) | Use Case |
|-------------|------------------------|----------|
| 8,000 Hz | 4,000 Hz | Telephony (voice only) |
| 16,000 Hz | 8,000 Hz | Wideband speech |
| 24,000 Hz | 12,000 Hz | Azure Voice Live / GPT-Realtime |
| 44,100 Hz | 22,050 Hz | CD quality |
| 48,000 Hz | 24,000 Hz | Professional audio |

**8 kHz → 24 kHz is a 3x upsample.** The algorithm (in `audioop.ratecv`)
interpolates new samples between existing ones:

```
8 kHz:    ●     ●     ●     ●     ●     ●
          │     │     │     │     │     │
24 kHz:   ●  ○  ○  ●  ○  ○  ●  ○  ○  ●  ○  ○  ●  ○  ○  ●
          │  │  │  │  │  │  │  │  │  │  │  │  │  │  │  │
          ▲  ▲  ▲  ▲  ▲  ▲  ▲  ▲  ▲  ▲  ▲  ▲  ▲  ▲  ▲  ▲
          │  │  │
          │  └──┴── Interpolated samples (computed)
          └────── Original sample (from phone)
```

Upsampling **doesn't add new information** (the phone only captured up to 4 kHz),
but it puts the audio in the format the AI model expects. The model's neural
network was trained on 24 kHz data and expects that sample rate.

---

## The Full Conversion Pipeline

### Inbound: Phone → AI

```
Step 1: Receive from Twilio
   Raw bytes: mulaw, 8kHz, 8-bit
   ~160 bytes per 20ms chunk (8000 × 0.020 × 1 byte)

Step 2: Decode mulaw → PCM16
   audioop.ulaw2lin(mulaw_bytes, 2)
   ~320 bytes (same # of samples, but 2 bytes each)

Step 3: Resample 8kHz → 24kHz
   audioop.ratecv(pcm_bytes, 2, 1, 8000, 24000, None)
   ~960 bytes (3x more samples, 2 bytes each)

Step 4: Base64 encode → send to Azure
   base64.b64encode(pcm_bytes)
   ~1,280 chars (base64 overhead ≈ 33%)
```

### Outbound: AI → Phone

```
Step 1: Receive from Azure
   PCM16, 24kHz, 16-bit audio chunk

Step 2: Resample 24kHz → 8kHz
   audioop.ratecv(pcm_bytes, 2, 1, 24000, 8000, None)
   1/3 the samples (downsample, anti-alias filter applied)

Step 3: Encode PCM16 → mulaw
   audioop.lin2ulaw(pcm_bytes, 2)
   1/2 the bytes (16-bit → 8-bit logarithmic compression)

Step 4: Base64 encode → send to Twilio
   base64.b64encode(mulaw_bytes)
   Twilio plays it to the phone speaker
```

---

## Byte-Level Walkthrough

Here's what happens to a single 20ms chunk of audio:

```
┌────────────────────────────────────────────────────────────┐
│ From Twilio (mulaw, 8kHz)                                  │
│                                                            │
│ 160 samples × 1 byte = 160 bytes                           │
│ Duration: 20ms                                             │
│ Frequency range: 0-4000 Hz                                 │
│                                                            │
│ Hex: 7F 3E 5A 2C 1F 4B ...                                │
│      │                                                     │
│      └── Each byte = one 8-bit mulaw sample                │
└────────────────────────────────────────────────────────────┘
                         │
                    ulaw2lin()
                         │
                         ▼
┌────────────────────────────────────────────────────────────┐
│ PCM16 @ 8kHz                                               │
│                                                            │
│ 160 samples × 2 bytes = 320 bytes                          │
│ Duration: 20ms                                             │
│                                                            │
│ Hex: 00 00  FC 03  A8 F9  24 0E ...                       │
│      └──┬──┘ └──┬──┘                                       │
│      Sample 0   Sample 1                                   │
│      (int16 LE) (int16 LE)                                 │
└────────────────────────────────────────────────────────────┘
                         │
                    ratecv()
                    8000 → 24000
                         │
                         ▼
┌────────────────────────────────────────────────────────────┐
│ PCM16 @ 24kHz                                              │
│                                                            │
│ 480 samples × 2 bytes = 960 bytes                          │
│ Duration: 20ms                                             │
│ (3x samples — interpolated)                                │
│                                                            │
│ This is what Azure Voice Live receives                     │
└────────────────────────────────────────────────────────────┘
```

---

## audioop — The Conversion Engine

The bridge uses Python's built-in `audioop` module for all audio conversions.
This module provides C-level performance for common audio operations.

| Function | Purpose | In This Project |
|----------|---------|-----------------|
| `audioop.ulaw2lin(data, width)` | Decode μ-law → linear PCM | Twilio audio → PCM16 |
| `audioop.lin2ulaw(data, width)` | Encode linear PCM → μ-law | PCM16 → Twilio audio |
| `audioop.ratecv(data, width, nchannels, inrate, outrate, state)` | Resample audio | 8kHz ↔ 24kHz |

### Important Notes

- **`width=2`** means 16-bit (2 bytes per sample). This is always used for PCM16.
- **`ratecv` returns a tuple** `(converted_bytes, new_state)`. The `state`
  parameter enables seamless resampling across consecutive chunks. Passing `None`
  resets the state each time (acceptable for telephony audio).
- **`audioop` is deprecated in Python 3.13+** and removed in 3.14. For newer
  Python versions, use the `audioop-lts` package as a drop-in replacement:

  ```bash
  pip install audioop-lts
  ```

---

## Why Not Use a Different Format?

### Why can't Twilio send PCM16 directly?

Twilio Media Streams are designed around telephony infrastructure, which uses
μ-law natively. The format is dictated by the PSTN — every phone call in North
America uses G.711 μ-law. Twilio passes through the native telephony encoding
to avoid an unnecessary encode/decode cycle on their side.

### Why can't Azure accept mulaw?

Azure OpenAI's Realtime API is built for high-quality audio ML inference. The
models were trained on linear PCM data at 24 kHz. Accepting mulaw would require
Azure to do the conversion server-side, adding latency and complexity for all
clients — most of which aren't coming from telephony.

### Why 24 kHz specifically?

The GPT-4o audio model architecture operates at 24 kHz internally. This sample
rate captures frequencies up to 12 kHz (by Nyquist theorem), which covers the
full range of human speech plus environmental context that helps the model
understand the acoustic scene. It's a practical balance between quality and
computational cost.

### What about Opus or other codecs?

Some real-time APIs support Opus or G.722, but the current Azure Realtime API
specifies PCM16 for its input/output format. The trade-off is higher bandwidth
for simpler processing — there's no codec negotiation or decoding latency.

---

## Audio Quality Considerations

### What's Lost in the Pipeline

| Stage | Quality Impact |
|-------|---------------|
| Phone mic → mulaw 8kHz | Biggest loss. 4 kHz bandwidth limit cuts all high frequencies. μ-law quantization adds ~38 dB SNR. |
| mulaw → PCM16 | Lossless expansion (no further loss) |
| 8kHz → 24kHz upsample | No new information added. High frequencies remain absent. |
| AI model processing | Model generates 24kHz PCM16 output natively |
| 24kHz → 8kHz downsample | Frequencies above 4kHz discarded (anti-alias filter) |
| PCM16 → mulaw | Slight quantization noise reintroduced |

**The bottleneck is always the phone network.** The 8 kHz / 4 kHz bandwidth
limit of PSTN telephony is the primary quality constraint. The bridge's
conversions are essentially lossless relative to what the phone network provides.

### Latency Budget

Each conversion step adds a small amount of processing latency:

| Operation | Typical Latency |
|-----------|-----------------|
| `ulaw2lin` (160 bytes) | < 0.01 ms |
| `ratecv` 8k→24k (320 bytes) | < 0.05 ms |
| `ratecv` 24k→8k | < 0.05 ms |
| `lin2ulaw` | < 0.01 ms |
| **Total per direction** | **< 0.1 ms** |

The audio conversion itself is negligible. The dominant latencies in the system
are network round-trips (50-200ms to Azure) and the AI model's inference time
(200-1000ms for speech generation).
