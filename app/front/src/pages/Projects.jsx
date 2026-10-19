import { useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { apiFetch, apiJson } from '../api.js';
import { errorText } from '../components/MatricesInput.jsx';

export const TIPO_LABELS = { expertos: 'Expertos', simulacion: 'Simulación' };

export function parseLabels(text) {
  return text.split('\n').map((l) => l.trim()).filter(Boolean);
}

export function fmtDate(iso) {
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

/** Etiquetas de un proyecto de expertos (RF-08). THR y orden máximo se eligen al lanzar cada cálculo. */
export function ExpertFields({ value, onChange, disabled }) {
  const set = (key) => (e) => onChange({ ...value, [key]: e.target.value });
  return (
    <>
      <div className="grid-2">
        <div className="field">
          <label htmlFor="causas">Causas ({parseLabels(value.causas).length})</label>
          <textarea id="causas" rows={6} placeholder={'Una por línea, ej.\nPrecio\nPublicidad'} value={value.causas} onChange={set('causas')} disabled={disabled} />
        </div>
        <div className="field">
          <label htmlFor="efectos">Efectos ({parseLabels(value.efectos).length})</label>
          <textarea id="efectos" rows={6} placeholder={'Una por línea, ej.\nVentas\nCostos'} value={value.efectos} onChange={set('efectos')} disabled={disabled} />
        </div>
      </div>
    </>
  );
}

export function expertPayload(value) {
  return {
    causas: parseLabels(value.causas),
    efectos: parseLabels(value.efectos),
  };
}

function CreateProject({ onCancel }) {
  const navigate = useNavigate();
  const [nombre, setNombre] = useState('');
  const [descripcion, setDescripcion] = useState('');
  const [tipo, setTipo] = useState('expertos');
  const [expert, setExpert] = useState({ causas: '', efectos: '' });
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState('');

  async function onSubmit(e) {
    e.preventDefault();
    setBusy(true);
    setMsg('');
    try {
      const body = { nombre, descripcion, tipo, ...(tipo === 'expertos' ? expertPayload(expert) : {}) };
      const res = await apiFetch('/app/projects', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const data = await res.json().catch(() => ({}));
      if (res.ok) {
        navigate(`/projects/${data.id}`);
        return;
      }
      setMsg(errorText(data, 'No se pudo crear el proyecto'));
    } catch {
      setMsg('Error de red.');
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card card-form" style={{ marginBottom: 20 }}>
      <h2 style={{ marginBottom: 12 }}>Nuevo proyecto</h2>
      <form className="grid" onSubmit={onSubmit} noValidate>
        <div className="grid-2">
          <div className="field">
            <label htmlFor="nombre">Nombre</label>
            <input id="nombre" value={nombre} onChange={(e) => setNombre(e.target.value)} maxLength={200} required />
          </div>
          <div className="field">
            <label htmlFor="tipo">Tipo</label>
            <select id="tipo" value={tipo} onChange={(e) => setTipo(e.target.value)}>
              <option value="expertos">Expertos (forgeffects)</option>
              <option value="simulacion">Simulación (MaxMin)</option>
            </select>
          </div>
        </div>
        <div className="field">
          <label htmlFor="descripcion">Descripción</label>
          <textarea id="descripcion" rows={2} value={descripcion} onChange={(e) => setDescripcion(e.target.value)} maxLength={5000} />
          {tipo === 'expertos' && <div className="hint">Los expertos la verán al abrir su invitación.</div>}
        </div>
        {tipo === 'expertos' && <ExpertFields value={expert} onChange={setExpert} />}
        {tipo === 'expertos' && (
          <p className="small muted" style={{ margin: 0 }}>
            El proyecto parte en <strong>borrador</strong>: las etiquetas se pueden editar hasta abrirlo a los expertos. THR y orden máximo se eligen al lanzar cada cálculo.
          </p>
        )}
        <div className="actions">
          <button type="submit" className="btn-primary" disabled={busy || !nombre.trim()}>{busy ? 'Creando…' : 'Crear proyecto'}</button>
          <button type="button" className="btn" onClick={onCancel}>Cancelar</button>
          <span className="status err" role="status" aria-live="polite">{msg}</span>
        </div>
      </form>
    </div>
  );
}

export default function Projects() {
  const [projects, setProjects] = useState(null);
  const [creating, setCreating] = useState(false);
  const [filter, setFilter] = useState('');

  useEffect(() => {
    apiJson('/app/projects')
      .then((data) => setProjects(Array.isArray(data) ? data : []))
      .catch(() => setProjects([]));
  }, []);

  const shown = (projects || []).filter((p) => !filter || p.tipo === filter);

  return (
    <>
      {creating && <CreateProject onCancel={() => setCreating(false)} />}
      <div className="card">
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 12 }}>
          <h1>Proyectos</h1>
          {!creating && <button type="button" className="btn primary" onClick={() => setCreating(true)}>Nuevo proyecto</button>}
        </div>
        <p className="sub">Proyectos de expertos (matrices CC, CE y EE con forgeffects) y de simulación (MaxMin).</p>
        <div className="controls">
          <select value={filter} onChange={(e) => setFilter(e.target.value)} aria-label="Tipo">
            <option value="">Todos los tipos</option>
            <option value="expertos">Expertos</option>
            <option value="simulacion">Simulación</option>
          </select>
          <span className="small muted">{projects === null ? '' : `${shown.length} proyectos`}</span>
        </div>
        <div style={{ overflow: 'auto' }}>
          <table>
            <thead>
              <tr><th>Nombre</th><th>Tipo</th><th>Estado</th><th>Dueño</th><th>Invitaciones</th><th>Inputs</th><th>Cálculos</th><th>Creado</th></tr>
            </thead>
            <tbody>
              {projects === null && <tr><td colSpan="8" className="empty">Cargando…</td></tr>}
              {projects !== null && shown.length === 0 && <tr><td colSpan="8" className="empty">Sin proyectos</td></tr>}
              {shown.map((p) => (
                <tr key={p.id}>
                  <td><Link to={`/projects/${p.id}`}>{p.nombre}</Link></td>
                  <td>{TIPO_LABELS[p.tipo] || p.tipo}</td>
                  <td><span className={`badge estado-${p.estado}`}>{p.estado}</span></td>
                  <td className="small">
                    {p.owner ? p.owner.nombre || p.owner.email : '—'}
                    {p.admins.length > 0 && <span className="muted"> +{p.admins.length}</span>}
                  </td>
                  <td>{p.tipo === 'expertos' ? p.conteos.invitaciones || 0 : '—'}</td>
                  <td>{p.tipo === 'expertos' ? p.conteos.inputs || 0 : '—'}</td>
                  <td>{p.conteos.calculos || 0}</td>
                  <td className="small">{fmtDate(p.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </>
  );
}
