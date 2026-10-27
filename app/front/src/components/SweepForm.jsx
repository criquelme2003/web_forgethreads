import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { apiJson, send } from '../api.js';
import { errorText } from './MatricesInput.jsx';
import { fmt } from './SweepCharts.jsx';

/** "100, 250 500" → [100, 250, 500]; valores separados por coma, punto y coma o espacio. */
function parseList(text) {
  return [...new Set(text.split(/[\s,;]+/).filter(Boolean).map(Number))];
}

function fmtDuration(s) {
  if (s < 90) return `${Math.round(s)} s`;
  if (s < 5400) return `${(s / 60).toFixed(1)} min`;
  return `${(s / 3600).toFixed(1)} h`;
}

/** Grilla N × c: cada celda indica si la combinación se ejecuta o por qué se omite (RF-32). */
function CombinationGrid({ preview, ns, cs }) {
  const skipped = new Map(preview.omitidas.map((x) => [`${x.n}|${x.c}`, x.reason]));
  return (
    <div style={{ overflow: 'auto', maxHeight: 320 }}>
      <table className="combo-grid">
        <thead><tr><th>N \ c</th>{cs.map((c) => <th key={c} className="num">{fmt(c)}</th>)}</tr></thead>
        <tbody>
          {ns.map((n) => (
            <tr key={n}>
              <th className="num">{n}</th>
              {cs.map((c) => {
                const reason = skipped.get(`${n}|${c}`);
                return reason
                  ? <td key={c} className="skip small" title={reason}>{reason}</td>
                  : <td key={c} className="ok small">✓ {preview.reps}</td>;
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Barrido sobre N y c con R repeticiones en un solo job (RF-31 a RF-34). */
export default function SweepForm({ projectId, disabled, onLaunched }) {
  const navigate = useNavigate();
  const [ns, setNs] = useState('100, 250, 500');
  const [cs, setCs] = useState('0.5, 1, 1.125, 1.5, 2, 2.5, 4');
  const [reps, setReps] = useState('20');
  const [thr, setThr] = useState('0.5');
  const [seedBase, setSeedBase] = useState('0');
  const [nodo, setNodo] = useState('');
  const [nodeOptions, setNodeOptions] = useState([]);
  const [preview, setPreview] = useState(null); // {body, data}
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState({ text: '', kind: 'muted' });

  useEffect(() => {
    apiJson('/app/nodes/available')
      .then((data) => setNodeOptions(Array.isArray(data.options) ? data.options : []))
      .catch(() => {});
  }, []);

  // Cualquier cambio invalida la vista previa: hay que revisarla de nuevo antes de lanzar.
  useEffect(() => setPreview(null), [ns, cs, reps, thr, seedBase]);

  function buildBody() {
    const body = {
      ns: parseList(ns),
      cs: parseList(cs),
      reps: Number(reps),
      thr: Number(thr.replace(',', '.')),
      seed_base: Number(seedBase),
    };
    if (!body.ns.length || body.ns.some((n) => !Number.isInteger(n) || n <= 0)) return [null, 'Los N deben ser enteros positivos.'];
    if (!body.cs.length || body.cs.some((c) => !(c > 0))) return [null, 'Los c deben ser números mayores que 0 (decimales con punto).'];
    if (!Number.isInteger(body.reps) || body.reps < 1) return [null, 'Las repeticiones deben ser un entero ≥ 1.'];
    if (!(body.thr >= 0 && body.thr <= 1)) return [null, 'THR debe estar en [0,1].'];
    if (!Number.isInteger(body.seed_base) || body.seed_base < 0) return [null, 'La semilla base debe ser un entero ≥ 0.'];
    return [body, ''];
  }

  async function review(e) {
    e.preventDefault();
    const [body, problem] = buildBody();
    if (problem) {
      setMsg({ text: problem, kind: 'err' });
      return;
    }
    setBusy(true);
    const { ok, data } = await send(`/app/projects/${projectId}/sweeps/preview`, 'POST', body).catch(() => ({ ok: false, data: {} }));
    setBusy(false);
    if (!ok) {
      setMsg({ text: errorText(data, 'No se pudo revisar el barrido'), kind: 'err' });
      return;
    }
    setMsg({ text: '', kind: 'muted' });
    setPreview({ body, data });
  }

  async function launch() {
    const { body, data } = preview;
    if (!window.confirm(`¿Lanzar el barrido? ${data.validas.length} combinaciones, ${data.ejecuciones} ejecuciones, límite de ${data.time_limit} en SLURM.`)) return;
    setBusy(true);
    setMsg({ text: 'Lanzando…', kind: 'muted' });
    const res = await send(`/app/projects/${projectId}/sweeps`, 'POST', { ...body, nodo: nodo || null }).catch(() => ({ ok: false, data: {} }));
    setBusy(false);
    if (!res.ok) {
      setMsg({ text: errorText(res.data, 'No se pudo lanzar el barrido'), kind: 'err' });
      return;
    }
    onLaunched?.();
    navigate(`/sweeps/${res.data.job_id}`);
  }

  const data = preview?.data;

  return (
    <form className="grid-tight" onSubmit={review} noValidate>
      <div className="grid-2">
        <div className="field">
          <label htmlFor="sw-ns">Valores de N</label>
          <input id="sw-ns" value={ns} onChange={(e) => setNs(e.target.value)} disabled={disabled} />
          <div className="hint">Enteros pares, separados por coma o espacio</div>
        </div>
        <div className="field">
          <label htmlFor="sw-cs">Valores de c</label>
          <input id="sw-cs" value={cs} onChange={(e) => setCs(e.target.value)} disabled={disabled} />
          <div className="hint">Decimales con punto; se omite c &gt; N/2 − 1</div>
        </div>
      </div>
      <div className="grid-3">
        <div className="field">
          <label htmlFor="sw-reps">Repeticiones (R)</label>
          <input id="sw-reps" type="number" min="1" step="1" value={reps} onChange={(e) => setReps(e.target.value)} disabled={disabled} />
          <div className="hint">Por cada combinación válida</div>
        </div>
        <div className="field">
          <label htmlFor="sw-thr">THR</label>
          <input id="sw-thr" inputMode="decimal" value={thr} onChange={(e) => setThr(e.target.value)} disabled={disabled} />
          <div className="hint">Fijo en todo el barrido</div>
        </div>
        <div className="field">
          <label htmlFor="sw-seed">Semilla base</label>
          <input id="sw-seed" type="number" min="0" step="1" value={seedBase} onChange={(e) => setSeedBase(e.target.value)} disabled={disabled} />
          <div className="hint">0 reproduce las semillas de test2.py</div>
        </div>
      </div>
      <div className="grid-3">
        <div className="field">
          <label htmlFor="sw-nodo">Nodo</label>
          <select id="sw-nodo" value={nodo} onChange={(e) => setNodo(e.target.value)} disabled={disabled}>
            <option value="">Automático</option>
            {nodeOptions.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
        </div>
      </div>
      <div className="actions">
        <button type="submit" className="btn-primary" disabled={disabled || busy}>{busy && !data ? 'Revisando…' : 'Revisar combinaciones'}</button>
        <span className={`status ${msg.kind} small`} role="status" aria-live="polite">{msg.text}</span>
      </div>

      {data && (
        <div className="validation">
          <p className="small" style={{ marginTop: 0 }}>
            <strong>{data.validas.length}</strong> combinaciones válidas
            {data.omitidas.length > 0 && <>, {data.omitidas.length} omitidas</>}
            {' '}· <strong>{data.ejecuciones}</strong> ejecuciones · duración estimada {fmtDuration(data.estimado_s)}
            {' '}· se pedirá <span className="mono">--time={data.time_limit}</span>
          </p>
          <CombinationGrid preview={data} ns={preview.body.ns} cs={preview.body.cs} />
          {data.warnings.map((w) => <p key={w} className="status small">{w}</p>)}
          {data.excede && (
            <p className="status err small">
              Supera la duración máxima permitida ({fmtDuration(data.max_s)}): reduce las combinaciones o las repeticiones.
            </p>
          )}
          {data.validas.length === 0 && <p className="status err small">Ninguna combinación es válida.</p>}
          <div className="actions" style={{ marginTop: 10 }}>
            <button type="button" className="btn-primary" disabled={busy || data.excede || data.validas.length === 0} onClick={launch}>
              {busy ? 'Lanzando…' : 'Lanzar barrido'}
            </button>
          </div>
        </div>
      )}
    </form>
  );
}
