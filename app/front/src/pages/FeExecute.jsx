import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { apiFetch, apiJson } from '../api.js';
import MatricesInput, { errorText } from '../components/MatricesInput.jsx';

function validateStandalone(texts) {
  return apiFetch('/app/fe/validate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(texts),
  });
}

export default function FeExecute() {
  const navigate = useNavigate();
  const [active, setActive] = useState(null);
  const [validation, setValidation] = useState(null);
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
        <MatricesInput
          validate={validateStandalone}
          validation={validation}
          onValidation={setValidation}
          onTexts={setActive}
          onMessage={(text) => setMsg(text ? { text, kind: 'err' } : { text: '', kind: 'muted' })}
        />

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
          <button type="submit" className="btn-primary" disabled={busy || !valid || !active}>
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
