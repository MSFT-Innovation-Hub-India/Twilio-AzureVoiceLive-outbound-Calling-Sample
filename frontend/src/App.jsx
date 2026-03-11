import React, { useState, useRef, useEffect, useCallback } from 'react';

const API_BASE = '/api';
const CONTACTS_KEY = 'voice_agent_contacts';
const THEME_KEY = 'voice_agent_theme';

// ─── Helpers ─────────────────────────────────────────────────────

function loadContacts() {
  try {
    return JSON.parse(localStorage.getItem(CONTACTS_KEY)) || [];
  } catch { return []; }
}

function saveContacts(contacts) {
  localStorage.setItem(CONTACTS_KEY, JSON.stringify(contacts));
}

function formatTimestamp(ts) {
  if (!ts) return '—';
  try {
    const d = new Date(ts);
    return d.toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' }) +
      ' ' + d.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' });
  } catch { return ts; }
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

function RecommendationBadge({ value }) {
  const colorMap = {
    recommended: { border: '#22c55e', tint: '#22c55e' },
    borderline: { border: '#f59e0b', tint: '#f59e0b' },
    not_recommended: { border: '#ef4444', tint: '#ef4444' },
  };
  const c = colorMap[value] || colorMap.borderline;
  return (
    <span style={{
      padding: '3px 10px', borderRadius: 12, fontSize: 12, fontWeight: 600,
      background: `${c.tint}18`, border: `1px solid ${c.border}`, color: c.tint,
    }}>
      {(value || 'unknown').replace(/_/g, ' ').toUpperCase()}
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
  cursor: 'pointer', background: active ? 'var(--accent)' : 'var(--border)', color: active ? '#fff' : 'var(--text)',
});

// ─── Results Dashboard Components ────────────────────────────────

function ResultsGrid({ onSelect }) {
  const [results, setResults] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetch(`${API_BASE}/results`).then(r => r.json())
      .then(d => { setResults(d.results || []); setLoading(false); })
      .catch(() => setLoading(false));
  }, []);

  if (loading) return (
    <div style={{ textAlign: 'center', padding: 60, color: 'var(--text-dim)' }}>Loading results…</div>
  );

  if (results.length === 0) return (
    <div style={{ textAlign: 'center', padding: 60, color: 'var(--text-dim)' }}>
      <div style={{ fontSize: 40, marginBottom: 12 }}>📭</div>
      <div style={{ fontSize: 15 }}>No interview results yet</div>
      <div style={{ fontSize: 13, marginTop: 6 }}>Complete an interview call to see results here</div>
    </div>
  );

  return (
    <div style={{ ...cardStyle, padding: 0, overflow: 'hidden' }}>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 14 }}>
        <thead>
          <tr style={{ background: 'var(--bg)', borderBottom: '1px solid var(--border)', textAlign: 'left' }}>
            <th style={{ padding: '14px 16px', fontWeight: 600, color: 'var(--text-dim)', fontSize: 12, textTransform: 'uppercase', letterSpacing: 0.5 }}>Candidate</th>
            <th style={{ padding: '14px 16px', fontWeight: 600, color: 'var(--text-dim)', fontSize: 12, textTransform: 'uppercase', letterSpacing: 0.5 }}>Outcome</th>
            <th style={{ padding: '14px 16px', fontWeight: 600, color: 'var(--text-dim)', fontSize: 12, textTransform: 'uppercase', letterSpacing: 0.5 }}>Recommendation</th>
            <th style={{ padding: '14px 16px', fontWeight: 600, color: 'var(--text-dim)', fontSize: 12, textTransform: 'uppercase', letterSpacing: 0.5 }}>Scenario</th>
            <th style={{ padding: '14px 16px', fontWeight: 600, color: 'var(--text-dim)', fontSize: 12, textTransform: 'uppercase', letterSpacing: 0.5 }}>Date</th>
          </tr>
        </thead>
        <tbody>
          {results.map((r) => (
            <tr
              key={r.call_id}
              onClick={() => onSelect(r.call_id)}
              style={{ cursor: 'pointer', borderBottom: '1px solid var(--border)', transition: 'background 0.15s' }}
              onMouseEnter={e => e.currentTarget.style.background = 'var(--bg)'}
              onMouseLeave={e => e.currentTarget.style.background = 'transparent'}
            >
              <td style={{ padding: '12px 16px', fontWeight: 500 }}>{r.candidate_name || 'Unknown'}</td>
              <td style={{ padding: '12px 16px' }}>
                <span style={{
                  padding: '2px 8px', borderRadius: 8, fontSize: 12, fontWeight: 500,
                  background: r.call_outcome === 'completed' ? '#22c55e18' : '#ef444418',
                  color: r.call_outcome === 'completed' ? '#16a34a' : '#dc2626',
                }}>{r.call_outcome || 'unknown'}</span>
              </td>
              <td style={{ padding: '12px 16px' }}><RecommendationBadge value={r.overall_recommendation} /></td>
              <td style={{ padding: '12px 16px', color: 'var(--text-dim)', fontSize: 13 }}>
                {(r.scenario_id || '—').replace(/_/g, ' ')}
              </td>
              <td style={{ padding: '12px 16px', color: 'var(--text-dim)', fontSize: 13 }}>{formatTimestamp(r.timestamp)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ResultDetail({ callId, onBack }) {
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetch(`${API_BASE}/results/${encodeURIComponent(callId)}`).then(r => r.json())
      .then(d => { setResult(d); setLoading(false); })
      .catch(() => setLoading(false));
  }, [callId]);

  if (loading) return (
    <div style={{ textAlign: 'center', padding: 60, color: 'var(--text-dim)' }}>Loading…</div>
  );

  if (!result) return (
    <div style={{ textAlign: 'center', padding: 60, color: 'var(--text-dim)' }}>Result not found</div>
  );

  const eligibility = result.eligibility || {};
  const assessment = result.assessment || {};
  const transcripts = result.transcripts || [];

  const infoRow = (label, value) => (
    <div style={{ display: 'flex', justifyContent: 'space-between', padding: '8px 0', borderBottom: '1px solid var(--border)', fontSize: 13 }}>
      <span style={{ color: 'var(--text-dim)' }}>{label}</span>
      <span style={{ fontWeight: 500, textAlign: 'right', maxWidth: '60%' }}>{value || '—'}</span>
    </div>
  );

  return (
    <div>
      {/* Back button */}
      <button
        onClick={onBack}
        style={{
          background: 'none', border: '1px solid var(--border)', borderRadius: 8,
          color: 'var(--text)', padding: '6px 14px', fontSize: 13, cursor: 'pointer',
          marginBottom: 16, display: 'inline-flex', alignItems: 'center', gap: 6,
        }}
      >← Back to Results</button>

      {/* Header */}
      <div style={{ ...cardStyle, display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 12 }}>
        <div>
          <h2 style={{ fontSize: 20, fontWeight: 700, marginBottom: 4 }}>{result.candidate_name || 'Unknown Candidate'}</h2>
          <div style={{ fontSize: 13, color: 'var(--text-dim)' }}>
            {(result.scenario_id || '').replace(/_/g, ' ')} &middot; {formatTimestamp(result.timestamp)} &middot; Call ID: {result.call_id}
          </div>
        </div>
        <RecommendationBadge value={result.overall_recommendation} />
      </div>

      {/* Two-column layout */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 20, alignItems: 'start' }}>
        {/* Left: Outcomes */}
        <div>
          {/* Recommendation */}
          <div style={cardStyle}>
            <h3 style={{ fontSize: 14, fontWeight: 600, marginBottom: 12, color: 'var(--accent)' }}>Recommendation</h3>
            {infoRow('Overall', (result.overall_recommendation || '').replace(/_/g, ' '))}
            {result.recommendation_justification && (
              <div style={{ marginTop: 10, fontSize: 13, lineHeight: 1.6, color: 'var(--text-dim)', fontStyle: 'italic' }}>
                "{result.recommendation_justification}"
              </div>
            )}
          </div>

          {/* Call Info */}
          <div style={cardStyle}>
            <h3 style={{ fontSize: 14, fontWeight: 600, marginBottom: 12, color: 'var(--accent)' }}>Call Info</h3>
            {infoRow('Outcome', result.call_outcome)}
            {infoRow('Role Confirmed', result.role_confirmed === true ? 'Yes' : result.role_confirmed === false ? 'No' : '—')}
          </div>

          {/* Eligibility */}
          {Object.keys(eligibility).length > 0 && (
            <div style={cardStyle}>
              <h3 style={{ fontSize: 14, fontWeight: 600, marginBottom: 12, color: 'var(--accent)' }}>Eligibility</h3>
              {Object.entries(eligibility).map(([k, v]) => (
                <div key={k}>{infoRow(k.replace(/_/g, ' '), String(v))}</div>
              ))}
            </div>
          )}

          {/* Assessment */}
          {Object.keys(assessment).length > 0 && (
            <div style={cardStyle}>
              <h3 style={{ fontSize: 14, fontWeight: 600, marginBottom: 12, color: 'var(--accent)' }}>Assessment</h3>
              {Object.entries(assessment).map(([k, v]) => (
                <div key={k}>{infoRow(k.replace(/_/g, ' '), String(v))}</div>
              ))}
            </div>
          )}
        </div>

        {/* Right: Transcript */}
        <div style={{ ...cardStyle, position: 'sticky', top: 20, maxHeight: 'calc(100vh - 80px)', display: 'flex', flexDirection: 'column' }}>
          <h3 style={{ fontSize: 14, fontWeight: 600, marginBottom: 12, color: 'var(--accent)' }}>
            Conversation Transcript
            <span style={{ fontWeight: 400, color: 'var(--text-dim)', marginLeft: 8, fontSize: 12 }}>
              {transcripts.length} messages
            </span>
          </h3>
          <div style={{ flex: 1, overflowY: 'auto', padding: 4 }}>
            {transcripts.length === 0 ? (
              <div style={{ textAlign: 'center', color: 'var(--text-dim)', padding: 32, fontSize: 13 }}>
                No transcript available
              </div>
            ) : (
              transcripts.map((t, i) => <TranscriptMessage key={i} role={t.role} text={t.text} />)
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

// ─── Main App ────────────────────────────────────────────────────

export default function App() {
  // Theme
  const [theme, setTheme] = useState(() => localStorage.getItem(THEME_KEY) || 'dark');
  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme);
    localStorage.setItem(THEME_KEY, theme);
  }, [theme]);

  // Navigation: 'agent' | 'results'
  const [view, setView] = useState('agent');
  // For results detail view
  const [selectedCallId, setSelectedCallId] = useState(null);

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

  // Cosmos DB network status: 'checking' | 'enabled' | 'disabled' | 'enabling' | 'error'
  const [cosmosStatus, setCosmosStatus] = useState('checking');
  const [cosmosDetail, setCosmosDetail] = useState('');

  // Check Cosmos DB network access on mount — auto-enable if disabled
  useEffect(() => {
    let cancelled = false;
    async function checkAndEnable() {
      try {
        const res = await fetch(`${API_BASE}/cosmosdb/network-status`);
        if (!res.ok) throw new Error(`Status check failed (${res.status})`);
        const data = await res.json();
        if (cancelled) return;

        if (data.enabled) {
          setCosmosStatus('enabled');
          setCosmosDetail(`Public access is enabled on ${data.account_name}`);
          return;
        }

        // Disabled — auto-enable
        setCosmosStatus('enabling');
        setCosmosDetail(`Public access is disabled on ${data.account_name}. Enabling…`);

        const enableRes = await fetch(`${API_BASE}/cosmosdb/enable-network`, { method: 'POST' });
        if (!enableRes.ok) throw new Error(`Enable failed (${enableRes.status})`);
        const enableData = await enableRes.json();
        if (cancelled) return;

        if (enableData.enabled) {
          setCosmosStatus('enabled');
          setCosmosDetail(`Public access enabled on ${enableData.account_name}`);
        } else {
          setCosmosStatus('error');
          setCosmosDetail(`Failed to enable public access (${enableData.raw_value})`);
        }
      } catch (err) {
        if (!cancelled) {
          setCosmosStatus('error');
          setCosmosDetail(err.message);
        }
      }
    }
    checkAndEnable();
    return () => { cancelled = true; };
  }, []);

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
  const cosmosReady = cosmosStatus === 'enabled';
  const canCall = !!(getCallTarget()) && !isCallActive && cosmosReady;

  return (
    <div style={{
      maxWidth: view === 'results' && selectedCallId ? 1100 : 620,
      margin: '40px auto', padding: 24,
      fontFamily: '-apple-system, BlinkMacSystemFont, Segoe UI, Roboto, sans-serif',
      transition: 'max-width 0.3s ease',
    }}>
      {/* ─── Top Nav ─── */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 0, marginBottom: 28 }}>
        <div style={{ flex: 1, textAlign: 'center' }}>
          <h1 style={{ fontSize: 24, fontWeight: 700, marginBottom: 4 }}>🤖 Voice Agent</h1>
          <p style={{ color: 'var(--text-dim)', fontSize: 14 }}>
            Outbound Calling &middot; Twilio PSTN + Azure AI
          </p>
        </div>
        <button
          onClick={() => setTheme(t => t === 'dark' ? 'light' : 'dark')}
          title={`Switch to ${theme === 'dark' ? 'light' : 'dark'} mode`}
          style={{
            position: 'absolute', right: 24, top: 24,
            background: 'var(--surface)', border: '1px solid var(--border)',
            borderRadius: 8, padding: '6px 10px', cursor: 'pointer',
            fontSize: 18, lineHeight: 1,
          }}
        >{theme === 'dark' ? '☀️' : '🌙'}</button>
      </div>

      {/* Nav Tabs */}
      <div style={{ display: 'flex', gap: 4, marginBottom: 24, background: 'var(--surface)', borderRadius: 10, padding: 4, border: '1px solid var(--border)' }}>
        {[
          { key: 'agent', label: '📞 Agent', },
          { key: 'results', label: '📊 Results', },
        ].map(tab => (
          <button
            key={tab.key}
            onClick={() => { setView(tab.key); setSelectedCallId(null); }}
            style={{
              flex: 1, padding: '10px 0', borderRadius: 8, border: 'none', fontSize: 14, fontWeight: 600,
              cursor: 'pointer', transition: 'all 0.15s',
              background: view === tab.key ? 'var(--accent)' : 'transparent',
              color: view === tab.key ? '#fff' : 'var(--text-dim)',
            }}
          >{tab.label}</button>
        ))}
      </div>

      {view === 'agent' ? (
        <>
          {/* Cosmos DB Network Status Banner */}
      {(() => {
        const bannerStyles = {
          checking:  { bg: '#3b82f618', border: '#3b82f6', color: '#3b82f6', icon: '🔄', label: 'Checking Cosmos DB connectivity…' },
          enabling:  { bg: '#f59e0b18', border: '#f59e0b', color: '#d97706', icon: '⚡', label: 'Enabling Cosmos DB public access…' },
          enabled:   { bg: '#22c55e18', border: '#22c55e', color: '#16a34a', icon: '✓',  label: 'Cosmos DB connected' },
          disabled:  { bg: '#ef444418', border: '#ef4444', color: '#dc2626', icon: '✕',  label: 'Cosmos DB public access disabled' },
          error:     { bg: '#ef444418', border: '#ef4444', color: '#dc2626', icon: '⚠',  label: 'Cosmos DB connectivity error' },
        };
        const b = bannerStyles[cosmosStatus] || bannerStyles.error;
        return (
          <div style={{
            display: 'flex', alignItems: 'center', gap: 10,
            padding: '10px 16px', borderRadius: 10, marginBottom: 20,
            background: b.bg, border: `1px solid ${b.border}`, color: b.color, fontSize: 13,
          }}>
            <span style={{
              fontSize: 16,
              animation: ['checking', 'enabling'].includes(cosmosStatus) ? 'spin 1s linear infinite' : 'none',
            }}>{b.icon}</span>
            <div style={{ flex: 1 }}>
              <span style={{ fontWeight: 600 }}>{b.label}</span>
              {cosmosDetail && <span style={{ marginLeft: 8, opacity: 0.8 }}>— {cosmosDetail}</span>}
            </div>
            {cosmosStatus === 'error' && (
              <button
                onClick={() => { setCosmosStatus('checking'); setCosmosDetail(''); location.reload(); }}
                style={{
                  background: 'none', border: `1px solid ${b.border}`, borderRadius: 6,
                  color: b.color, padding: '4px 12px', fontSize: 12, cursor: 'pointer',
                }}
              >Retry</button>
            )}
          </div>
        );
      })()}

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
                background: canCall ? 'var(--success)' : 'var(--border)',
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
        </>
      ) : (
        /* ─── Results View ─── */
        selectedCallId ? (
          <ResultDetail callId={selectedCallId} onBack={() => setSelectedCallId(null)} />
        ) : (
          <>
            <h2 style={{ fontSize: 18, fontWeight: 600, marginBottom: 16 }}>Interview Results</h2>
            <ResultsGrid onSelect={(id) => setSelectedCallId(id)} />
          </>
        )
      )}

      <style>{`
        @keyframes pulse {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.4; }
        }
        @keyframes spin {
          from { transform: rotate(0deg); }
          to { transform: rotate(360deg); }
        }
      `}</style>
    </div>
  );
}
