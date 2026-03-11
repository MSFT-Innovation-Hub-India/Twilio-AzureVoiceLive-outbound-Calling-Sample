import React, { useState, useRef, useEffect, useCallback } from 'react';

const API_BASE = '/api';
const CONTACTS_KEY = 'voice_agent_contacts';

// ─── Helpers ─────────────────────────────────────────────────────

function loadContacts() {
  try {
    return JSON.parse(localStorage.getItem(CONTACTS_KEY)) || [];
  } catch { return []; }
}

function saveContacts(contacts) {
  localStorage.setItem(CONTACTS_KEY, JSON.stringify(contacts));
}

// ─── Small Components ────────────────────────────────────────────

function StatusBadge({ status }) {
  const colors = {
    idle: '#6b7280', initiating: '#f59e0b', queued: '#f59e0b', ringing: '#f59e0b',
    'in-progress': '#3b82f6', connected: '#22c55e', completed: '#6b7280', failed: '#ef4444',
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
    <div style={{ display: 'flex', justifyContent: isUser ? 'flex-end' : 'flex-start', marginBottom: 8 }}>
      <div style={{
        maxWidth: '75%', padding: '10px 14px', borderRadius: 12,
        background: isUser ? 'var(--user-bg)' : 'var(--assistant-bg)',
        borderBottomRightRadius: isUser ? 4 : 12, borderBottomLeftRadius: isUser ? 12 : 4,
      }}>
        <div style={{ fontSize: 11, color: 'var(--text-dim)', marginBottom: 4 }}>
          {isUser ? '📞 Candidate' : '🤖 AI Agent'}
        </div>
        <div style={{ fontSize: 14, lineHeight: 1.5 }}>{text}</div>
      </div>
    </div>
  );
}

const cardStyle = {
  background: 'var(--surface)', border: '1px solid var(--border)',
  borderRadius: 16, padding: 24, marginBottom: 24,
};

const inputStyle = {
  width: '100%', padding: '10px 14px', borderRadius: 8,
  border: '1px solid var(--border)', background: 'var(--bg)',
  color: 'var(--text)', fontSize: 14, outline: 'none',
};

const btnSmall = (active) => ({
  padding: '6px 14px', borderRadius: 6, border: 'none', fontSize: 12, fontWeight: 600,
  cursor: 'pointer', background: active ? 'var(--accent)' : '#374151', color: '#fff',
});

// ─── Main App ────────────────────────────────────────────────────

export default function App() {
  // Scenarios
  const [scenarios, setScenarios] = useState([]);
  const [selectedScenario, setSelectedScenario] = useState(null);

  // Contacts
  const [contacts, setContacts] = useState(loadContacts);
  const [newName, setNewName] = useState('');
  const [newPhone, setNewPhone] = useState('');

  // Call state
  const [selectedContact, setSelectedContact] = useState(null);
  const [manualPhone, setManualPhone] = useState('');
  const [backend, setBackend] = useState('gpt-realtime');
  const [callState, setCallState] = useState('idle');
  const [callId, setCallId] = useState(null);
  const [transcripts, setTranscripts] = useState([]);
  const [error, setError] = useState(null);
  const wsRef = useRef(null);
  const transcriptEndRef = useRef(null);

  // Load scenarios on mount
  useEffect(() => {
    fetch(`${API_BASE}/scenarios`).then(r => r.json())
      .then(d => setScenarios(d.scenarios || []))
      .catch(() => {});
  }, []);

  // Persist contacts
  useEffect(() => { saveContacts(contacts); }, [contacts]);

  // Auto-scroll transcript
  useEffect(() => { transcriptEndRef.current?.scrollIntoView({ behavior: 'smooth' }); }, [transcripts]);

  const addContact = () => {
    const name = newName.trim();
    const phone = newPhone.trim();
    if (!name || !phone) return;
    setContacts((prev) => [...prev, { id: Date.now().toString(), name, phone }]);
    setNewName('');
    setNewPhone('');
  };

  const removeContact = (id) => {
    setContacts((prev) => prev.filter((c) => c.id !== id));
    if (selectedContact?.id === id) setSelectedContact(null);
  };

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
    ws.onclose = () => console.log('Event stream closed');
    ws.onerror = (err) => console.error('Event stream error:', err);
  }, []);

  const getCallTarget = () => {
    if (selectedContact) return { phone: selectedContact.phone, name: selectedContact.name };
    if (manualPhone.trim()) return { phone: manualPhone.trim(), name: null };
    return null;
  };

  const handleCall = async () => {
    const target = getCallTarget();
    if (!target) return;

    setError(null);
    setCallState('calling');
    setTranscripts([]);

    try {
      const body = {
        phone_number: target.phone,
        backend,
        scenario_id: selectedScenario?.id || null,
        candidate_name: target.name || null,
      };
      const res = await fetch(`${API_BASE}/call`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        const errData = await res.json();
        throw new Error(errData.detail || 'Failed to initiate call');
      }
      const data = await res.json();
      setCallId(data.call_id);
      setCallState(data.status || 'queued');
      connectEventStream(data.call_id);
    } catch (err) {
      setError(err.message);
      setCallState('failed');
    }
  };

  const handleHangup = () => {
    if (wsRef.current) { wsRef.current.close(); wsRef.current = null; }
    setCallState('completed');
  };

  const handleReset = () => {
    setCallState('idle');
    setCallId(null);
    setTranscripts([]);
    setError(null);
    setManualPhone('');
    setSelectedContact(null);
  };

  const isCallActive = ['calling', 'queued', 'ringing', 'in-progress', 'connected'].includes(callState);
  const canCall = !!(getCallTarget()) && !isCallActive;

  return (
    <div style={{
      maxWidth: 620, margin: '40px auto', padding: 24,
      fontFamily: '-apple-system, BlinkMacSystemFont, Segoe UI, Roboto, sans-serif',
    }}>
      {/* Header */}
      <div style={{ textAlign: 'center', marginBottom: 28 }}>
        <h1 style={{ fontSize: 24, fontWeight: 700, marginBottom: 4 }}>🤖 Voice Agent</h1>
        <p style={{ color: 'var(--text-dim)', fontSize: 14 }}>
          Outbound Calling &middot; Twilio PSTN + Azure AI
        </p>
      </div>

      {/* ─── Step 1: Select Scenario ─── */}
      <div style={cardStyle}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 16 }}>
          <span style={{ fontSize: 18 }}>1</span>
          <span style={{ fontWeight: 600 }}>Select Scenario</span>
          {selectedScenario && (
            <span style={{ marginLeft: 'auto', fontSize: 12, color: 'var(--accent)' }}>
              ✓ {selectedScenario.name}
            </span>
          )}
        </div>

        {scenarios.length === 0 ? (
          <div style={{ color: 'var(--text-dim)', fontSize: 13, padding: 16, textAlign: 'center' }}>
            No scenarios found. Add JSON files to <code>backend/scenarios/</code>
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {/* "No scenario" option — generic assistant */}
            <button
              onClick={() => !isCallActive && setSelectedScenario(null)}
              disabled={isCallActive}
              style={{
                textAlign: 'left', padding: '12px 16px', borderRadius: 10,
                border: !selectedScenario ? '2px solid var(--accent)' : '1px solid var(--border)',
                background: !selectedScenario ? 'var(--accent)11' : 'var(--bg)',
                color: 'var(--text)', cursor: isCallActive ? 'default' : 'pointer',
                opacity: isCallActive ? 0.5 : 1,
              }}
            >
              <div style={{ fontWeight: 600, fontSize: 14 }}>💬 Generic Voice Assistant</div>
              <div style={{ fontSize: 12, color: 'var(--text-dim)', marginTop: 4 }}>
                No structured scenario — free-form conversation
              </div>
            </button>
            {scenarios.map((s) => (
              <button
                key={s.id}
                onClick={() => !isCallActive && setSelectedScenario(s)}
                disabled={isCallActive}
                style={{
                  textAlign: 'left', padding: '12px 16px', borderRadius: 10,
                  border: selectedScenario?.id === s.id ? '2px solid var(--accent)' : '1px solid var(--border)',
                  background: selectedScenario?.id === s.id ? 'var(--accent)11' : 'var(--bg)',
                  color: 'var(--text)', cursor: isCallActive ? 'default' : 'pointer',
                  opacity: isCallActive ? 0.5 : 1,
                }}
              >
                <div style={{ fontWeight: 600, fontSize: 14 }}>📋 {s.name}</div>
                {s.description && (
                  <div style={{ fontSize: 12, color: 'var(--text-dim)', marginTop: 4 }}>
                    {s.description.length > 120 ? s.description.slice(0, 120) + '…' : s.description}
                  </div>
                )}
              </button>
            ))}
          </div>
        )}
      </div>

      {/* ─── Step 2: Select Candidate / Contact ─── */}
      <div style={cardStyle}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 16 }}>
          <span style={{ fontSize: 18 }}>2</span>
          <span style={{ fontWeight: 600 }}>Select Candidate</span>
        </div>

        {/* Add contact form */}
        <div style={{ display: 'flex', gap: 8, marginBottom: 12 }}>
          <input
            placeholder="Name"
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            disabled={isCallActive}
            style={{ ...inputStyle, flex: 1 }}
          />
          <input
            placeholder="+91XXXXXXXXXX"
            value={newPhone}
            onChange={(e) => setNewPhone(e.target.value)}
            disabled={isCallActive}
            onKeyDown={(e) => e.key === 'Enter' && addContact()}
            style={{ ...inputStyle, flex: 1 }}
          />
          <button onClick={addContact} disabled={isCallActive || !newName.trim() || !newPhone.trim()} style={btnSmall(true)}>
            + Add
          </button>
        </div>

        {/* Contact grid */}
        {contacts.length > 0 && (
          <div style={{
            maxHeight: 200, overflowY: 'auto', borderRadius: 8,
            border: '1px solid var(--border)', marginBottom: 12,
          }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
              <thead>
                <tr style={{ background: 'var(--bg)', color: 'var(--text-dim)', textAlign: 'left' }}>
                  <th style={{ padding: '8px 12px', fontWeight: 600 }}></th>
                  <th style={{ padding: '8px 12px', fontWeight: 600 }}>Name</th>
                  <th style={{ padding: '8px 12px', fontWeight: 600 }}>Phone</th>
                  <th style={{ padding: '8px 4px', fontWeight: 600, width: 30 }}></th>
                </tr>
              </thead>
              <tbody>
                {contacts.map((c) => (
                  <tr
                    key={c.id}
                    onClick={() => !isCallActive && setSelectedContact(selectedContact?.id === c.id ? null : c)}
                    style={{
                      cursor: isCallActive ? 'default' : 'pointer',
                      background: selectedContact?.id === c.id ? 'var(--accent)15' : 'transparent',
                      borderTop: '1px solid var(--border)',
                    }}
                  >
                    <td style={{ padding: '8px 12px' }}>
                      <input
                        type="radio"
                        checked={selectedContact?.id === c.id}
                        readOnly
                        style={{ accentColor: 'var(--accent)' }}
                      />
                    </td>
                    <td style={{ padding: '8px 12px' }}>{c.name}</td>
                    <td style={{ padding: '8px 12px', color: 'var(--text-dim)' }}>{c.phone}</td>
                    <td style={{ padding: '8px 4px', textAlign: 'center' }}>
                      <button
                        onClick={(e) => { e.stopPropagation(); removeContact(c.id); }}
                        disabled={isCallActive}
                        style={{
                          background: 'none', border: 'none', color: 'var(--error)',
                          cursor: 'pointer', fontSize: 14, padding: 2,
                        }}
                        title="Remove contact"
                      >✕</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {/* Or manual entry */}
        <div style={{ fontSize: 12, color: 'var(--text-dim)', marginBottom: 6 }}>
          {contacts.length > 0 ? 'Or enter a number manually:' : 'Enter phone number:'}
        </div>
        <input
          type="tel"
          placeholder="+91XXXXXXXXXX"
          value={manualPhone}
          onChange={(e) => { setManualPhone(e.target.value); if (e.target.value.trim()) setSelectedContact(null); }}
          disabled={isCallActive}
          onKeyDown={(e) => e.key === 'Enter' && canCall && handleCall()}
          style={{ ...inputStyle, opacity: isCallActive ? 0.5 : 1 }}
        />
      </div>

      {/* ─── Step 3: Call Control ─── */}
      <div style={cardStyle}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 16 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span style={{ fontSize: 18 }}>3</span>
            <span style={{ fontWeight: 600 }}>Call</span>
          </div>
          <StatusBadge status={callState} />
        </div>

        {/* Backend selector */}
        <div style={{ marginBottom: 16 }}>
          <label style={{ fontSize: 13, fontWeight: 600, display: 'block', marginBottom: 6 }}>AI Backend</label>
          <div style={{ display: 'flex', gap: 8 }}>
            {[
              { value: 'gpt-realtime', label: 'Azure OpenAI Realtime' },
              { value: 'voice-live', label: 'Azure Voice Live API' },
            ].map((opt) => (
              <button
                key={opt.value}
                onClick={() => setBackend(opt.value)}
                disabled={isCallActive}
                style={{
                  flex: 1, padding: '10px 12px', borderRadius: 8,
                  border: backend === opt.value ? '2px solid var(--accent)' : '1px solid var(--border)',
                  background: backend === opt.value ? 'var(--accent)22' : 'var(--bg)',
                  color: backend === opt.value ? 'var(--accent)' : 'var(--text-dim)',
                  fontSize: 13, fontWeight: backend === opt.value ? 600 : 400,
                  cursor: isCallActive ? 'default' : 'pointer', opacity: isCallActive ? 0.5 : 1,
                }}
              >{opt.label}</button>
            ))}
          </div>
        </div>

        {/* Summary before calling */}
        {!isCallActive && callState !== 'completed' && (
          <div style={{
            padding: 12, borderRadius: 8, background: 'var(--bg)',
            fontSize: 13, marginBottom: 16, lineHeight: 1.6,
          }}>
            <div><strong>Scenario:</strong> {selectedScenario ? selectedScenario.name : 'Generic Assistant'}</div>
            <div><strong>Calling:</strong> {getCallTarget()
              ? `${getCallTarget().name ? getCallTarget().name + ' — ' : ''}${getCallTarget().phone}`
              : <span style={{ color: 'var(--text-dim)' }}>Select a contact or enter a number</span>
            }</div>
            <div><strong>Backend:</strong> {backend === 'voice-live' ? 'Azure Voice Live API' : 'Azure OpenAI Realtime'}</div>
          </div>
        )}

        {/* Buttons */}
        <div style={{ display: 'flex', gap: 10 }}>
          {callState === 'idle' || callState === 'failed' ? (
            <button
              onClick={handleCall}
              disabled={!canCall}
              style={{
                flex: 1, padding: '12px 0', borderRadius: 10, border: 'none',
                background: canCall ? 'var(--success)' : '#374151',
                color: '#fff', fontSize: 15, fontWeight: 600, cursor: canCall ? 'pointer' : 'default',
              }}
            >📞 Place Call</button>
          ) : isCallActive ? (
            <button
              onClick={handleHangup}
              style={{
                flex: 1, padding: '12px 0', borderRadius: 10, border: 'none',
                background: 'var(--error)', color: '#fff', fontSize: 15, fontWeight: 600, cursor: 'pointer',
              }}
            >✖ End Call</button>
          ) : (
            <button
              onClick={handleReset}
              style={{
                flex: 1, padding: '12px 0', borderRadius: 10, border: 'none',
                background: 'var(--accent)', color: '#fff', fontSize: 15, fontWeight: 600, cursor: 'pointer',
              }}
            >🔄 New Call</button>
          )}
        </div>

        {error && (
          <div style={{ marginTop: 12, padding: '10px 14px', borderRadius: 8, background: '#7f1d1d33', color: 'var(--error)', fontSize: 13 }}>
            {error}
          </div>
        )}
        {callId && (
          <div style={{ marginTop: 12, fontSize: 12, color: 'var(--text-dim)' }}>Call ID: {callId}</div>
        )}
      </div>

      {/* ─── Transcript ─── */}
      <div style={cardStyle}>
        <h3 style={{ fontSize: 15, fontWeight: 600, marginBottom: 16 }}>💬 Live Transcript</h3>
        <div style={{ minHeight: 200, maxHeight: 400, overflowY: 'auto', padding: 8, borderRadius: 8 }}>
          {transcripts.length === 0 ? (
            <div style={{ textAlign: 'center', color: 'var(--text-dim)', padding: 40, fontSize: 14 }}>
              {isCallActive ? 'Waiting for conversation…' : 'Place a call to see the transcript here'}
            </div>
          ) : (
            transcripts.map((t, i) => <TranscriptMessage key={i} role={t.role} text={t.text} />)
          )}
          <div ref={transcriptEndRef} />
        </div>
      </div>

      {/* Architecture note */}
      <div style={{
        padding: 16, borderRadius: 12, background: 'var(--surface)',
        border: '1px solid var(--border)', fontSize: 12, color: 'var(--text-dim)', lineHeight: 1.6,
      }}>
        <strong style={{ color: 'var(--text)' }}>Architecture:</strong><br />
        React UI → FastAPI → Twilio (PSTN) → Media Bridge → {backend === 'voice-live' ? 'Azure Voice Live API' : 'Azure OpenAI Realtime'}<br />
        Audio: Twilio (mulaw 8kHz) ↔ Bridge (PCM16 24kHz) ↔ {backend === 'voice-live' ? 'Voice Live API' : 'GPT-Realtime API'}
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
