import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { apiFetch, apiJson } from '../api.js';
import ManualMatrices from '../components/ManualMatrices.jsx';

const MATRICES = [
  { key: 'cc', name: 'CC', shape: 'm × m', hint: 'Causas × causas' },
  { key: 'ce', name: 'CE', shape: 'm × n', hint: 'Causas × efectos' },
  { key: 'ee', name: 'EE', shape: 'n × n', hint: 'Efectos × efectos' },
];
const PREVIEW_MAX = 40; // filas/columnas visibles por matriz

function errorText(body, fallback) {
  if (!body || !body.detail) return fallback;
  if (typeof body.detail === 'string') return body.detail;
  if (Array.isArray(body.detail)) return body.detail.map((d) => d.msg).join(' · ');
  return fallback;
}

function Preview({ name, preview, errors }) {
  if (!preview || preview.col_labels.length === 0) return <div className="empty">Sin datos</div>;
  const byCell = new Map();
  const byRow = new Map();
  for (const e of errors) {
    if (e.row != null && e.col != null) byCell.set(`${e.row}:${e.col}`, e.message);
    else if (e.row != null) byRow.set(e.row, e.message);
  }
  const rows = preview.cells.slice(0, PREVIEW_MAX);
  const cols = preview.col_labels.slice(0, PREVIEW_MAX);
  const clipped = preview.cells.length > PREVIEW_MAX || preview.col_labels.length > PREVIEW_MAX;
  return (
    <div className="matrix-wrap">
      <table className="matrix">
        <thead>
          <tr>
            <th className="mono-sm">{preview.corner || name}</th>
            {cols.map((c, j) => <th key={j} className="mono-sm">{c}</th>)}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i} className={byRow.has(i) ? 'row-err' : undefined} title={byRow.get(i)}>
              <th className="mono-sm">{preview.row_labels[i]}</th>
              {row.slice(0, PREVIEW_MAX).map((cell, j) => {
                const msg = byCell.get(`${i}:${j}`);
                return (
                  <td key={j} className={`mono-sm${msg ? ' cell-err' : ''}`} title={msg}>{cell}</td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
      {clipped && <div className="small muted">Vista parcial: primeras {PREVIEW_MAX} filas y columnas.</div>}
    </div>
  );
}

export default function FeExecute() {
  const navigate = useNavigate();
  const [mode, setMode] = useState('csv'); // 'csv' | 'manual'
  const [texts, setTexts] = useState({ cc: null, ce: null, ee: null });
  const [manualTexts, setManualTexts] = useState(null);
  const [fileNames, setFileNames] = useState({});
  const [validation, setValidation] = useState(null);
  const [validating, setValidating] = useState(false);
  const [tab, setTab] = useState('CC');
  const [thr, setThr] = useState('0.5');
  const [maxorder, setMaxorder] = useState('3');
  const [nodo, setNodo] = useState('');
  const [nodeOptions, setNodeOptions] = useState([]);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState({ text: '', kind: 'muted' });

  useEffect(() => {
    apiJson('/app/nodes/available')
      .then((data) => setNodeOptions(Array.isArray(data.options) ? data.options : []))
      .catch(() => {});
  }, []);

  // Ambos modos producen los mismos CSV; la validación y el lanzamiento no distinguen el origen.
  const active = mode === 'csv' ? texts : manualTexts || { cc: null, ce: null, ee: null };
  const allLoaded = MATRICES.every((m) => active[m.key] != null);

  useEffect(() => {
    if (!allLoaded) {
      setValidation(null);
      return;
    }
    let cancelled = false;
    setValidating(true);
    apiFetch('/app/fe/validate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(active),
    })
      .then(async (res) => {
        const body = await res.json().catch(() => ({}));
        if (cancelled) return;
        if (res.ok) {
          setValidation(body);
          setMsg({ text: '', kind: 'muted' });
        } else {
          setValidation(null);
          setMsg({ text: errorText(body, 'No se pudieron validar las matrices'), kind: 'err' });
        }
      })
      .catch(() => !cancelled && setMsg({ text: 'Error de red al validar.', kind: 'err' }))
      .finally(() => !cancelled && setValidating(false));
    return () => {
      cancelled = true;
    };
  }, [active, allLoaded]);

  async function onFile(key, file) {
    if (!file) {
      setTexts((t) => ({ ...t, [key]: null }));
      return;
    }
    const text = await file.text();
    setFileNames((f) => ({ ...f, [key]: file.name }));
    setTexts((t) => ({ ...t, [key]: text }));
  }

  async function onSubmit(e) {
    e.preventDefault();
    const thrValue = parseFloat(thr);
    const maxorderValue = parseInt(maxorder, 10);
    if (!Number.isFinite(thrValue) || thrValue < 0 || thrValue > 1) {
      setMsg({ text: 'THR debe estar en [0,1].', kind: 'err' });
      return;
    }
    if (!Number.isInteger(maxorderValue) || maxorderValue < 2 || maxorderValue > 10) {
      setMsg({ text: 'El orden máximo debe ser un entero entre 2 y 10.', kind: 'err' });
      return;
    }
    setBusy(true);
    setMsg({ text: 'Subiendo matrices y lanzando el cálculo…', kind: 'muted' });
    try {
      const res = await apiFetch('/app/fe/execute', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...active, thr: thrValue, maxorder: maxorderValue, nodo: nodo || null }),
      });
      const body = await res.json().catch(() => ({}));
      if (res.ok) {
        navigate(`/fe/jobs/${body.job_id}`);
        return;
      }
      if (body.detail && typeof body.detail === 'object' && 'valid' in body.detail) {
        setValidation(body.detail);
        setMsg({ text: 'Las matrices tienen errores.', kind: 'err' });
      } else {
        setMsg({ text: errorText(body, 'Error al lanzar el cálculo'), kind: 'err' });
      }
    } catch {
      setMsg({ text: 'Error de red.', kind: 'err' });
    } finally {
      setBusy(false);
    }
  }

  const valid = validation?.valid === true;
  const tabErrors = validation ? validation.errors.filter((e) => e.matrix === tab) : [];
  const globalErrors = validation ? validation.errors.filter((e) => e.row == null || e.col == null) : [];
  const cellErrorCount = validation ? validation.errors.length - globalErrors.length : 0;
  const emptyCount = validation ? validation.errors.filter((e) => e.message === 'celda vacía').length : 0;

  return (
    <div className="card card-form">
      <h1 style={{ fontSize: 22 }}>Cálculo de caminos</h1>
      <p className="sub">
        Ingresa las matrices CC, CE y EE de un experto, subiendo los CSV o a mano. Cada CSV lleva las etiquetas en
        la primera fila y la primera columna; se acepta separador <span className="mono">,</span> o{' '}
        <span className="mono">;</span> y decimal con punto o coma. Los efectos olvidados se calculan con{' '}
        <span className="mono">forgeffects</span> en el cluster.
      </p>
      <form className="grid" onSubmit={onSubmit} noValidate>
        <div className="controls" role="tablist" aria-label="Forma de ingreso" style={{ marginBottom: 0 }}>
          <button type="button" role="tab" aria-selected={mode === 'csv'} className={mode === 'csv' ? 'primary' : undefined} onClick={() => setMode('csv')}>Subir CSV</button>
          <button type="button" role="tab" aria-selected={mode === 'manual'} className={mode === 'manual' ? 'primary' : undefined} onClick={() => setMode('manual')}>Ingresar a mano</button>
        </div>

        {mode === 'manual' && <ManualMatrices onChange={setManualTexts} errors={validation?.errors} />}

        {mode === 'csv' && <div className="grid-3">
          {MATRICES.map((m) => (
            <div className="field" key={m.key}>
              <label htmlFor={`file-${m.key}`}>{m.name} <span className="muted">({m.shape})</span></label>
              <input id={`file-${m.key}`} type="file" accept=".csv,text/csv,text/plain" onChange={(e) => onFile(m.key, e.target.files[0])} />
              <div className="hint">{fileNames[m.key] || m.hint}</div>
            </div>
          ))}
        </div>}

        {validating && <div className="small muted">Validando…</div>}
        {validation && (
          <div className="validation">
            <div className="small">
              {valid ? (
                <span className="badge success">válido</span>
              ) : (
                <span className="badge error">{validation.errors.length} errores</span>
              )}{' '}
              <span className="muted">
                {validation.causes.length} causas · {validation.effects.length} efectos
              </span>
            </div>
            {validation.warnings.length > 0 && (
              <ul className="small notes">{validation.warnings.map((w, i) => <li key={i}>{w}</li>)}</ul>
            )}
            {globalErrors.length > 0 && (
              <ul className="small notes err">
                {globalErrors.map((e, i) => (
                  <li key={i}>
                    <strong>{e.matrix}</strong>
                    {e.row != null && ` fila ${e.row + 1}`}: {e.message}
                  </li>
                ))}
              </ul>
            )}
            {cellErrorCount > 0 && mode === 'csv' && (
              <div className="small" style={{ color: 'var(--err)' }}>
                {cellErrorCount} celdas con errores (marcadas en rojo; pasa el cursor para ver el motivo).
              </div>
            )}
            {cellErrorCount > 0 && mode === 'manual' && (
              <div className="small" style={{ color: 'var(--err)' }}>
                {emptyCount > 0 && `${emptyCount} celdas vacías (en amarillo)`}
                {emptyCount > 0 && cellErrorCount > emptyCount && ' · '}
                {cellErrorCount > emptyCount && `${cellErrorCount - emptyCount} con valores inválidos (en rojo)`}
              </div>
            )}
            {mode === 'csv' && <>
            <div className="controls" style={{ marginTop: 10 }}>
              {MATRICES.map((m) => {
                const n = validation.errors.filter((e) => e.matrix === m.name).length;
                return (
                  <button key={m.name} type="button" className={tab === m.name ? 'primary' : undefined} onClick={() => setTab(m.name)}>
                    {m.name}{n > 0 ? ` (${n})` : ''}
                  </button>
                );
              })}
            </div>
            <Preview name={tab} preview={validation.previews[tab]} errors={tabErrors} />
            </>}
          </div>
        )}

        <div className="grid-3">
          <div className="field">
            <label htmlFor="thr">THR</label>
            <input id="thr" type="number" step="any" min="0" max="1" value={thr} onChange={(e) => setThr(e.target.value)} />
            <div className="hint">Umbral en [0,1]</div>
          </div>
          <div className="field">
            <label htmlFor="maxorder">Orden máximo</label>
            <input id="maxorder" type="number" step="1" min="2" max="10" value={maxorder} onChange={(e) => setMaxorder(e.target.value)} />
            <div className="hint">Entero entre 2 y 10</div>
          </div>
          <div className="field">
            <label htmlFor="nodo">Nodo</label>
            <select id="nodo" value={nodo} onChange={(e) => setNodo(e.target.value)}>
              <option value="">Automático</option>
              {nodeOptions.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
            </select>
            <div className="hint">Nodo SLURM con GPU</div>
          </div>
        </div>

        <div className="actions">
          <button type="submit" className="btn-primary" disabled={busy || !valid}>
            {busy ? 'Lanzando…' : 'Calcular caminos'}
          </button>
          <span className={`status ${msg.kind}`} role="status" aria-live="polite">{msg.text}</span>
        </div>
      </form>
      <div className="foot">
        <span>POST <span className="mono">/app/fe/execute</span> · sube el input por SFTP y lanza <span className="mono">fe_job.sh</span> + <span className="mono">notifier.sh</span> (afterany)</span>
      </div>
    </div>
  );
}
