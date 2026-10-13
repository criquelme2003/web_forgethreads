import { useEffect, useState } from 'react';
import { apiFetch, apiJson } from '../api.js';
import { errorText } from './MatricesInput.jsx';

/** Regla de matrix_construction.py en el cluster (RF-30): N par y 0 < c ≤ N/2 − 1. */
function checkParams(n, c) {
  if (!Number.isInteger(n) || n <= 0) return 'El número de nodos debe ser un entero positivo.';
  if (n % 2 !== 0) return 'El número de nodos debe ser par.';
  if (!Number.isFinite(c) || c <= 0) return 'La conectividad debe ser mayor que 0.';
  if (c > n / 2 - 1) return `La conectividad debe ser ≤ N/2 − 1 = ${n / 2 - 1}.`;
  return '';
}

/** Lanza un cálculo MaxMin individual dentro de un proyecto de simulación (RF-29). */
export default function MaxMinForm({ projectId, disabled, onLaunched }) {
  const [nodeOptions, setNodeOptions] = useState([]);
  const [numeroNodos, setNumeroNodos] = useState('');
  const [conectividad, setConectividad] = useState('');
  const [threshold, setThreshold] = useState('');
  const [seed, setSeed] = useState('');
  const [nodo, setNodo] = useState('');
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState({ text: '', kind: 'muted' });

  useEffect(() => {
    apiJson('/app/nodes/available')
      .then((data) => setNodeOptions(Array.isArray(data.options) ? data.options : []))
      .catch(() => {});
  }, []);

  async function onSubmit(e) {
    e.preventDefault();
    const payload = {
      project_id: projectId,
      numero_nodos: Number(numeroNodos),
      conectividad_promedio: Number(conectividad.replace(',', '.')),
      threshold: Number(threshold.replace(',', '.')),
      seed: Number(seed),
      nodo: nodo || null,
    };
    const problem = checkParams(payload.numero_nodos, payload.conectividad_promedio)
      || (!(payload.threshold >= 0 && payload.threshold <= 1) ? 'El threshold debe estar en [0,1].' : '')
      || (seed === '' || !Number.isInteger(payload.seed) ? 'La semilla debe ser un entero.' : '');
    if (problem) {
      setMsg({ text: problem, kind: 'err' });
      return;
    }
    setBusy(true);
    setMsg({ text: 'Encolando…', kind: 'muted' });
    try {
      const res = await apiFetch('/app/execute_maxmin', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const body = await res.json().catch(() => ({}));
      if (res.ok) {
        setMsg({ text: `Job ${body.job_id} encolado.`, kind: 'ok' });
        onLaunched?.();
      } else {
        setMsg({ text: errorText(body, 'Error al encolar'), kind: 'err' });
      }
    } catch {
      setMsg({ text: 'Error de red.', kind: 'err' });
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="grid" onSubmit={onSubmit} noValidate>
      <div className="grid-3">
        <div className="field">
          <label htmlFor="numero_nodos">Número de nodos (N)</label>
          <input id="numero_nodos" type="number" inputMode="numeric" min="2" step="2" placeholder="ej. 5000" value={numeroNodos} onChange={(e) => setNumeroNodos(e.target.value)} disabled={disabled} />
          <div className="hint">Entero par</div>
        </div>
        <div className="field">
          <label htmlFor="conectividad">Conectividad (c)</label>
          <input id="conectividad" type="text" inputMode="decimal" placeholder="ej. 2.5" value={conectividad} onChange={(e) => setConectividad(e.target.value)} disabled={disabled} />
          <div className="hint">Decimal, 0 &lt; c ≤ N/2 − 1</div>
        </div>
        <div className="field">
          <label htmlFor="threshold">Threshold</label>
          <input id="threshold" type="text" inputMode="decimal" placeholder="ej. 0.5" value={threshold} onChange={(e) => setThreshold(e.target.value)} disabled={disabled} />
          <div className="hint">En [0,1]</div>
        </div>
      </div>
      <div className="grid-2">
        <div className="field">
          <label htmlFor="seed">Seed</label>
          <input id="seed" type="number" inputMode="numeric" step="1" placeholder="ej. 42" value={seed} onChange={(e) => setSeed(e.target.value)} disabled={disabled} />
        </div>
        <div className="field">
          <label htmlFor="nodo">Nodo / GPU</label>
          <select id="nodo" value={nodo} onChange={(e) => setNodo(e.target.value)} disabled={disabled}>
            <option value="">Automático (mejor disponible)</option>
            {nodeOptions.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
        </div>
      </div>
      <div className="actions">
        <button type="submit" className="btn-primary" disabled={busy || disabled}>{busy ? 'Encolando…' : 'Encolar cálculo'}</button>
        <span className={`status ${msg.kind}`} role="status" aria-live="polite">{msg.text}</span>
      </div>
    </form>
  );
}
