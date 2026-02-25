import React, { useState, useRef, useEffect, useCallback } from 'react';

const API_BASE = '/api';

function StatusBadge({ status }) {
  const colors = {
    idle: '#6b7280',
    initiating: '#f59e0b',
    queued: '#f59e0b',
    ringing: '#f59e0b',
    'in-progress': '#3b82f6',
    connected: '#22c55e',
    completed: '#6b7280',
    failed: '#ef4444',
  };
  const color = colors[status] || '#6b7280';

  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', gap: 6,
      padding: '4px 12px', borderRadius: 20,
      background: `${color}22`, color, fontSize: 13, fontWeight: 600,
    }}>
      <span style={{
        width: 8, height: 8, borderRadius: '50%', background: color,
        animation: ['connected', 'in-progress'].includes(status) ? 'pulse 1.5s infinite' : 'none',
      }} />
      {status.toUpperCase()}
    </span>
  );
}

function TranscriptMessage({ role, text }) {
  const isUser = role === 'user';
  return (
    <div style={{
      display: 'flex',
      justifyContent: isUser ? 'flex-end' : 'flex-start',
      marginBottom: 8,
    }}>
      <div style={{
        maxWidth: '75%', padding: '10px 14px', borderRadius: 12,
        background: isUser ? 'var(--user-bg)' : 'var(--assistant-bg)',
        borderBottomRightRadius: isUser ? 4 : 12,
        borderBottomLeftRadius: isUser ? 12 : 4,
      }}>
        <div style={{ fontSize: 11, color: 'var(--text-dim)', marginBottom: 4 }}>
          {isUser ? '📞 Caller' : '🤖 AI Agent'}
        </div>
        <div style={{ fontSize: 14, lineHeight: 1.5 }}>{text}</div>
      </div>
    </div>
  );
}

export default function App() {
  const [phoneNumber, setPhoneNumber] = useState('');
  const [callState, setCallState] = useState('idle'); // idle | calling | connected | completed | failed
  const [callId, setCallId] = useState(null);
  const [transcripts, setTranscripts] = useState([]);
  const [error, setError] = useState(null);
  const wsRef = useRef(null);
  const transcriptEndRef = useRef(null);

  // Auto-scroll transcript
  useEffect(() => {
    transcriptEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [transcripts]);

  // WebSocket for live events
  const connectEventStream = useCallback((cid) => {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/ws/events/${cid}`;

    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;

    ws.onmessage = (event) => {
      const data = JSON.parse(event.data);
      if (data.type === 'status') {
        setCallState(data.status === 'completed' ? 'completed' : data.status);
      } else if (data.type === 'transcript') {
        setTranscripts((prev) => [...prev, { role: data.role, text: data.text }]);
      }
    };

    ws.onclose = () => {
      console.log('Event stream closed');
    };

    ws.onerror = (err) => {
      console.error('Event stream error:', err);
    };
  }, []);

  const handleCall = async () => {
    if (!phoneNumber.trim()) return;

    setError(null);
    setCallState('calling');
    setTranscripts([]);

    try {
      const res = await fetch(`${API_BASE}/call`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ phone_number: phoneNumber }),
      });

      if (!res.ok) {
        const errData = await res.json();
        throw new Error(errData.detail || 'Failed to initiate call');
      }

      const data = await res.json();
      setCallId(data.call_id);
      setCallState(data.status || 'queued');

      // Connect to live event stream
      connectEventStream(data.call_id);
    } catch (err) {
      setError(err.message);
      setCallState('failed');
    }
  };

  const handleHangup = () => {
    if (wsRef.current) {
      wsRef.current.close();
      wsRef.current = null;
    }
    setCallState('completed');
  };

  const handleReset = () => {
    setCallState('idle');
    setCallId(null);
    setTranscripts([]);
    setError(null);
    setPhoneNumber('');
  };

  const isCallActive = ['calling', 'queued', 'ringing', 'in-progress', 'connected'].includes(callState);

  return (
    <div style={{
      maxWidth: 520, margin: '60px auto', padding: 24,
      fontFamily: '-apple-system, BlinkMacSystemFont, Segoe UI, Roboto, sans-serif',
    }}>
      {/* Header */}
      <div style={{ textAlign: 'center', marginBottom: 32 }}>
        <h1 style={{ fontSize: 24, fontWeight: 700, marginBottom: 4 }}>
          🤖 Voice Agent
        </h1>
        <p style={{ color: 'var(--text-dim)', fontSize: 14 }}>
          Twilio PSTN + Azure Voice Live (GPT-Realtime)
        </p>
      </div>

      {/* Call Control Card */}
      <div style={{
        background: 'var(--surface)', border: '1px solid var(--border)',
        borderRadius: 16, padding: 24, marginBottom: 24,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 20 }}>
          <span style={{ fontWeight: 600 }}>Call Control</span>
          <StatusBadge status={callState} />
        </div>

        {/* Phone input */}
        <div style={{ marginBottom: 16 }}>
          <input
            type="tel"
            placeholder="+91XXXXXXXXXX"
            value={phoneNumber}
            onChange={(e) => setPhoneNumber(e.target.value)}
            disabled={isCallActive}
            onKeyDown={(e) => e.key === 'Enter' && !isCallActive && handleCall()}
            style={{
              width: '100%', padding: '12px 16px', borderRadius: 10,
              border: '1px solid var(--border)', background: 'var(--bg)',
              color: 'var(--text)', fontSize: 16, outline: 'none',
              opacity: isCallActive ? 0.5 : 1,
            }}
          />
        </div>

        {/* Buttons */}
        <div style={{ display: 'flex', gap: 10 }}>
          {callState === 'idle' || callState === 'failed' ? (
            <button
              onClick={handleCall}
              disabled={!phoneNumber.trim()}
              style={{
                flex: 1, padding: '12px 0', borderRadius: 10, border: 'none',
                background: phoneNumber.trim() ? 'var(--success)' : '#374151',
                color: '#fff', fontSize: 15, fontWeight: 600, cursor: 'pointer',
              }}
            >
              📞 Place Call
            </button>
          ) : isCallActive ? (
            <button
              onClick={handleHangup}
              style={{
                flex: 1, padding: '12px 0', borderRadius: 10, border: 'none',
                background: 'var(--error)', color: '#fff', fontSize: 15,
                fontWeight: 600, cursor: 'pointer',
              }}
            >
              ✖ End Call
            </button>
          ) : (
            <button
              onClick={handleReset}
              style={{
                flex: 1, padding: '12px 0', borderRadius: 10, border: 'none',
                background: 'var(--accent)', color: '#fff', fontSize: 15,
                fontWeight: 600, cursor: 'pointer',
              }}
            >
              🔄 New Call
            </button>
          )}
        </div>

        {error && (
          <div style={{
            marginTop: 12, padding: '10px 14px', borderRadius: 8,
            background: '#7f1d1d33', color: 'var(--error)', fontSize: 13,
          }}>
            {error}
          </div>
        )}

        {callId && (
          <div style={{ marginTop: 12, fontSize: 12, color: 'var(--text-dim)' }}>
            Call ID: {callId}
          </div>
        )}
      </div>

      {/* Transcript */}
      <div style={{
        background: 'var(--surface)', border: '1px solid var(--border)',
        borderRadius: 16, padding: 24,
      }}>
        <h3 style={{ fontSize: 15, fontWeight: 600, marginBottom: 16 }}>
          💬 Live Transcript
        </h3>

        <div style={{
          minHeight: 200, maxHeight: 400, overflowY: 'auto',
          padding: 8, borderRadius: 8,
        }}>
          {transcripts.length === 0 ? (
            <div style={{
              textAlign: 'center', color: 'var(--text-dim)',
              padding: 40, fontSize: 14,
            }}>
              {isCallActive
                ? 'Waiting for conversation…'
                : 'Place a call to see the transcript here'}
            </div>
          ) : (
            transcripts.map((t, i) => (
              <TranscriptMessage key={i} role={t.role} text={t.text} />
            ))
          )}
          <div ref={transcriptEndRef} />
        </div>
      </div>

      {/* Architecture note */}
      <div style={{
        marginTop: 24, padding: 16, borderRadius: 12,
        background: 'var(--surface)', border: '1px solid var(--border)',
        fontSize: 12, color: 'var(--text-dim)', lineHeight: 1.6,
      }}>
        <strong style={{ color: 'var(--text)' }}>Architecture:</strong>
        <br />
        React UI → FastAPI → Twilio (PSTN) → Media Bridge → Azure Voice Live (GPT-Realtime)
        <br />
        Audio: Twilio (mulaw 8kHz) ↔ Bridge (PCM16 24kHz) ↔ Azure Voice Live API
      </div>

      <style>{`
        @keyframes pulse {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.4; }
        }
      `}</style>
    </div>
  );
}
