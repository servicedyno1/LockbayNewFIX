import React, { useState, useEffect } from 'react';

const BACKEND = process.env.REACT_APP_BACKEND_URL;

const C = {
  bg: '#0f1923',
  card: '#1a2a38',
  border: '#253545',
  accent: '#3BB5C8',
  text: '#e0e8ef',
  sub: '#8ba3b8',
  green: '#22c55e',
  amber: '#f59e0b',
  red: '#ef4444',
  muted: '#6b8299',
};

function Dot({ color }) {
  return (
    <span style={{ width: 9, height: 9, borderRadius: '50%', background: color, display: 'inline-block' }} />
  );
}

function Row({ label, ok, note, testid }) {
  return (
    <div
      data-testid={testid}
      style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '7px 0' }}
    >
      <span style={{ color: C.text }}>{label}</span>
      <span style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <Dot color={ok ? C.green : C.amber} />
        <span style={{ fontSize: 13, color: ok ? C.green : C.amber }}>{note || (ok ? 'OK' : 'Not set')}</span>
      </span>
    </div>
  );
}

function App() {
  const [status, setStatus] = useState(null);
  const [err, setErr] = useState(false);

  useEffect(() => {
    fetch(`${BACKEND}/api/status`)
      .then((r) => r.json())
      .then(setStatus)
      .catch(() => setErr(true));
  }, []);

  const db = status?.database || {};
  const ints = status?.integrations || {};

  return (
    <div
      style={{
        minHeight: '100vh',
        background: C.bg,
        color: C.text,
        fontFamily: "'Segoe UI', system-ui, sans-serif",
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: 24,
      }}
    >
      <div style={{ width: '100%', maxWidth: 560 }} data-testid="lockbay-dashboard">
        <div style={{ textAlign: 'center', marginBottom: 28 }}>
          <div style={{ fontSize: 44, marginBottom: 4 }}>
            <span style={{ color: C.accent }}>$</span>
          </div>
          <h1 style={{ fontSize: 34, fontWeight: 700, margin: '0 0 6px', color: '#fff' }} data-testid="brand-title">
            {status?.brand || 'LockBay'}
          </h1>
          <p style={{ color: C.sub, fontSize: 15, margin: 0 }}>Secure Escrow Trading on Telegram</p>
        </div>

        {/* Overall status */}
        <div
          style={{
            background: C.card,
            borderRadius: 12,
            padding: 20,
            marginBottom: 18,
            border: `1px solid ${C.border}`,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
          }}
          data-testid="status-banner"
        >
          <span style={{ fontSize: 15, color: C.accent, fontWeight: 600 }}>Configuration Status</span>
          <span style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            {err ? (
              <>
                <Dot color={C.red} />
                <span style={{ fontSize: 13, color: C.red }}>Server unreachable</span>
              </>
            ) : !status ? (
              <span style={{ color: C.muted, fontSize: 13 }}>Checking…</span>
            ) : (
              <>
                <Dot color={C.green} />
                <span style={{ fontSize: 13, color: C.green }}>
                  {status.environment?.toUpperCase()} · configured
                </span>
              </>
            )}
          </span>
        </div>

        {/* Core setup */}
        <div style={{ background: C.card, borderRadius: 12, padding: '10px 20px 16px', marginBottom: 18, border: `1px solid ${C.border}` }}>
          <h2 style={{ fontSize: 14, letterSpacing: 0.5, textTransform: 'uppercase', color: C.sub, margin: '10px 0' }}>Core Setup</h2>
          <Row testid="check-database" label="PostgreSQL Database" ok={!!db.connected} note={db.connected ? 'Connected' : 'Disconnected'} />
          <Row testid="check-tables" label="Database Tables" ok={(db.tables || 0) > 0} note={db.tables ? `${db.tables} tables` : '—'} />
          <Row testid="check-bot-token" label="Telegram Bot Token" ok={!!status?.bot_token_configured} note={status?.bot_username ? `@${status.bot_username}` : 'Configured'} />
          <Row testid="check-admins" label="Admin User IDs" ok={!!status?.admin_configured} note={status?.admin_configured ? 'Configured' : 'Not set'} />
        </div>

        {/* Integrations */}
        <div style={{ background: C.card, borderRadius: 12, padding: '10px 20px 16px', border: `1px solid ${C.border}` }}>
          <h2 style={{ fontSize: 14, letterSpacing: 0.5, textTransform: 'uppercase', color: C.sub, margin: '10px 0' }}>Integrations</h2>
          <Row testid="check-brevo" label="Brevo (Email)" ok={!!ints.brevo_email} />
          <Row testid="check-tatum" label="Tatum (Crypto)" ok={!!ints.tatum_crypto} />
          <Row testid="check-kraken" label="Kraken (Exchange)" ok={!!ints.kraken_exchange} />
          <Row testid="check-fincra" label="Fincra (NGN)" ok={!!ints.fincra_ngn} />
          <Row testid="check-blockbee" label="BlockBee (Payments)" ok={!!ints.blockbee} />
          <Row testid="check-dynopay" label="DynoPay (Payments)" ok={!!ints.dynopay} />
          <Row testid="check-twilio" label="Twilio (SMS)" ok={!!ints.twilio_sms} />
        </div>

        <p style={{ marginTop: 22, fontSize: 12, color: C.muted, textAlign: 'center', lineHeight: 1.7 }}>
          {status?.mode === 'preview-server-only'
            ? 'Preview runs in server-only mode. The live bot runs on your Railway deployment.'
            : 'Credentials loaded from /app/.env'}
        </p>
      </div>
    </div>
  );
}

export default App;
