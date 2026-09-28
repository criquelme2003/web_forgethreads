import { useEffect, useState } from 'react';
import { apiFetch, apiJson } from '../api.js';

export default function Parameters() {
  const [nodeOptions, setNodeOptions] = useState([]);
  const [nodeHint, setNodeHint] = useState('Nodo/GPU específico donde se lanzará el job.');
  const [numeroNodos, setNumeroNodos] = useState('');
  const [seed, setSeed] = useState('');
  const [threshold, setThreshold] = useState('');
  const [conectividad, setConectividad] = useState('');
  const [nodo, setNodo] = useState('');
  const [formMsg, setFormMsg] = useState('');
  const [formKind, setFormKind] = useState('muted');
  const [busy, setBusy] = useState(false);
  const [showDebug, setShowDebug] = useState(false);
  const [jsonRequest, setJsonRequest] = useState('');
  const [jsonResponse, setJsonResponse] = useState('');
  const [copyLabel, setCopyLabel] = useState('Copiar');
  const [pending, setPending] = useState(null);

  useEffect(() => {
    let cancelled = false;
    apiJson('/app/nodes/available')
      .then((data) => {
        if (cancelled) return;
        setNodeOptions(Array.isArray(data.options) ? data.options : []);
        if (data.hint) setNodeHint(data.hint);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  async function loadPending() {
    try {
      const r = await apiFetch('/app/jobs?status=pending');
      const jobs = await r.json().catch(() => []);
      setPending(Array.isArray(jobs) ? jobs : []);
    } catch {
      setPending(null);
    }
  }

  useEffect(() => {
    loadPending();
    const iv = setInterval(loadPending, 2000);
    return () => clearInterval(iv);
  }, []);

  function setStatus(msg, kind) {
    setFormMsg(msg);
    setFormKind(kind || 'muted');
  }

  async function onCopy() {
    try {
      await navigator.clipboard.writeText(jsonResponse);
      setCopyLabel('Copiado');
      setTimeout(() => setCopyLabel('Copiar'), 1200);
    } catch {}
  }

  async function onSubmit(e) {
    e.preventDefault();
    setStatus('', 'muted');
    const payload = {
      numero_nodos: parseInt(numeroNodos, 10),
      threshold: parseFloat(threshold),
      conectividad_promedio: parseInt(conectividad, 10),
      seed: parseInt(seed, 10),
      nodo: nodo ? nodo : null,
    };
    if (!payload.numero_nodos || payload.numero_nodos <= 0) {
      setStatus('Revisa número de nodos.', 'err');
      return;
    }
    if (!Number.isFinite(payload.threshold)) {
      setStatus('Revisa threshold.', 'err');
      return;
    }
    setBusy(true);
    setJsonRequest(JSON.stringify(payload, null, 2));
    setJsonResponse('');
    setShowDebug(true);
    try {
      const res = await apiFetch('/app/execute_maxmin', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const body = await res.json().catch(() => ({}));
      setJsonResponse(JSON.stringify(body, null, 2));
      if (res.ok) {
        setStatus(body.message || 'Tarea ingresada correctamente', 'ok');
      } else {
        const msg = body.detail
          ? (Array.isArray(body.detail) ? body.detail.map((d) => d.msg).join(' · ') : body.detail)
          : (body.message || 'Error al encolar');
        setStatus(msg, 'err');
      }
    } catch (err) {
      setJsonResponse(JSON.stringify({ error: String(err) }, null, 2));
      setStatus('Error de red.', 'err');
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="wrap-narrow" style={{ margin: '0 auto', padding: 0 }}>
      <div className="card card-form">
        <h1 style={{ fontSize: 22 }}>Ejecución MaxMin</h1>
        <p className="sub">Define los parámetros del grafo. Se enviarán a <span className="mono">app/execute_maxmin</span> y quedarán encolados en el nodo elegido.</p>
        <form className="grid" onSubmit={onSubmit} noValidate>
          <div className="grid-2">
            <div className="field">
              <label htmlFor="numero_nodos">Número de nodos</label>
              <input id="numero_nodos" type="number" inputMode="numeric" min="1" step="1" required placeholder="ej. 5000" value={numeroNodos} onChange={(e) => setNumeroNodos(e.target.value)} />
              <div className="hint">Entero &gt; 0</div>
            </div>
            <div className="field">
              <label htmlFor="seed">Seed</label>
              <input id="seed" type="number" inputMode="numeric" step="1" required placeholder="ej. 42" value={seed} onChange={(e) => setSeed(e.target.value)} />
              <div className="hint">Reproducibilidad</div>
            </div>
          </div>
          <div className="grid-2">
            <div className="field">
              <label htmlFor="threshold">Threshold</label>
              <input id="threshold" type="number" step="any" required placeholder="ej. 0.15" value={threshold} onChange={(e) => setThreshold(e.target.value)} />
              <div className="hint">Float</div>
            </div>
            <div className="field">
              <label htmlFor="conectividad_promedio">Conectividad promedio</label>
              <input id="conectividad_promedio" type="number" inputMode="numeric" min="0" step="1" required placeholder="ej. 8" value={conectividad} onChange={(e) => setConectividad(e.target.value)} />
              <div className="hint">Entero ≥ 0</div>
            </div>
          </div>
          <div className="field">
            <label htmlFor="nodo">Nodo / GPU objetivo</label>
            <select id="nodo" name="nodo" value={nodo} onChange={(e) => setNodo(e.target.value)}>
              <option value="">Automático (mejor disponible)</option>
              {nodeOptions.map((o) => (
                <option key={o.value} value={o.value}>{o.label}</option>
              ))}
            </select>
            <div className="hint">{nodeHint}</div>
          </div>
          <div className="actions">
            <button type="submit" className="btn-primary" disabled={busy}>
              {busy ? 'Enviando…' : 'Encolar tarea'}
            </button>
            <span className={`status ${formKind}`} role="status" aria-live="polite">{formMsg}</span>
          </div>
        </form>
        <div className="foot">
          <span>POST <span className="mono">/app/execute_maxmin</span> · requiere sesión · ejecuta <span className="mono">pwd</span> en nodo</span>
          <span className="mono">v1</span>
        </div>
      </div>

      {showDebug && (
        <div style={{ marginTop: 20, border: '1px solid var(--border)', borderRadius: 'var(--radius)', background: '#fff', overflow: 'hidden' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '12px 16px', borderBottom: '1px solid var(--border)', background: '#f9fafb' }}>
            <strong style={{ fontSize: 12, letterSpacing: '.06em', textTransform: 'uppercase', color: '#374151' }}>Respuesta del servidor — JSON completo</strong>
            <button type="button" onClick={onCopy} style={{ fontSize: 11, padding: '5px 9px', border: '1px solid #d1d5db', borderRadius: 6, background: '#fff', cursor: 'pointer', color: '#374151' }}>{copyLabel}</button>
          </div>
          <div style={{ padding: '14px 16px', display: 'grid', gap: 14 }}>
            <div>
              <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: '.05em', textTransform: 'uppercase', color: '#6b7280', marginBottom: 6 }}>REQUEST ENVIADO</div>
              <pre className="mono" style={{ background: '#f9fafb', border: '1px solid var(--border)', padding: 10, borderRadius: 6, overflow: 'auto', fontSize: 12, margin: 0, whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>{jsonRequest}</pre>
            </div>
            <div>
              <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: '.05em', textTransform: 'uppercase', color: '#6b7280', marginBottom: 6 }}>RESPONSE</div>
              <pre className="mono" style={{ background: '#f9fafb', border: '1px solid var(--border)', padding: 10, borderRadius: 6, overflow: 'auto', fontSize: 12, margin: 0, whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>{jsonResponse}</pre>
            </div>
            <div style={{ fontSize: 11, color: 'var(--muted)' }}>El nodo se elige por el selector (idle por defecto <span className="mono">app/services/node_selector.py</span>) salvo que se elija uno manualmente arriba. El notifier reporta el resultado a <span className="mono">POST /app/job_callback</span>.</div>
          </div>
        </div>
      )}

      <div className="card" style={{ marginTop: 20 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
          <h2>Trabajos pendientes</h2>
          <a href="/front/jobs" style={{ fontSize: 12, color: 'var(--muted)', textDecoration: 'none', borderBottom: '1px solid var(--border)' }}>ver todos →</a>
        </div>
        <p className="sub" style={{ margin: '0 0 10px' }}>Polling <span className="mono">GET /app/jobs?status=pending</span> cada 2s · sqlite <span className="mono">data/jobs.db</span></p>
        <div className="small muted">
          {pending === null && 'Error'}
          {pending !== null && pending.length === 0 && 'Sin pendientes'}
          {pending !== null && pending.length > 0 && (
            <div style={{ display: 'grid', gap: 6 }}>
              {pending.slice(0, 5).map((j) => (
                <div key={j.job_id} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '6px 8px', border: '1px solid var(--border)', borderRadius: 6, background: '#f9fafb' }}>
                  <span className="mono">{j.job_id}</span>
                  <span className="small" style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                    <span style={{ padding: '2px 6px', borderRadius: 999, background: '#fef3c7', border: '1px solid #fcd34d', fontSize: 11 }}>{j.status}</span>
                    {j.node}
                  </span>
                </div>
              ))}
              {pending.length > 5 && (
                <div className="small muted">+{pending.length - 5} más · <a href="/front/jobs">ver todos</a></div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
