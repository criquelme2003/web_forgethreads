import { useCallback, useEffect, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { apiFetch } from '../api.js';
import { errorText } from '../components/MatricesInput.jsx';
import MaxMinForm from '../components/MaxMinForm.jsx';
import { ExpertFields, expertPayload, fmtDate, TIPO_LABELS } from './Projects.jsx';

const POLL_MS = 5000;

const TRANSITION_LABELS = {
  abierto: { borrador: 'Abrir a expertos', cerrado: 'Reabrir proyecto' },
  cerrado: { abierto: 'Cerrar proyecto' },
};
const TRANSITION_CONFIRM = {
  abierto: {
    borrador: 'Al abrir el proyecto, las etiquetas, THR y el orden máximo quedan fijos. ¿Continuar?',
  },
  cerrado: {
    abierto: '¿Cerrar el proyecto? No se aceptarán nuevas respuestas ni cálculos hasta reabrirlo.',
  },
};

async function send(path, method, body) {
  const res = await apiFetch(path, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = res.status === 204 ? {} : await res.json().catch(() => ({}));
  return { ok: res.ok, status: res.status, data };
}

function jobBadge(status) {
  return ['pending', 'success', 'partial'].includes(status) ? status : 'error';
}

// --- edición -----------------------------------------------------------------------------------

function EditProject({ project, onSaved, onCancel }) {
  const draft = project.tipo === 'expertos' && project.estado === 'borrador';
  const [nombre, setNombre] = useState(project.nombre);
  const [descripcion, setDescripcion] = useState(project.descripcion);
  const [expert, setExpert] = useState({
    causas: project.causas.join('\n'),
    efectos: project.efectos.join('\n'),
    thr: String(project.thr ?? ''),
    maxorder: String(project.maxorder ?? ''),
  });
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState('');

  async function onSubmit(e) {
    e.preventDefault();
    setBusy(true);
    const body = { nombre, descripcion, ...(draft ? expertPayload(expert) : {}) };
    const { ok, data } = await send(`/app/projects/${project.id}`, 'PATCH', body).catch(() => ({ ok: false, data: {} }));
    setBusy(false);
    if (ok) onSaved(data);
    else setMsg(errorText(data, 'No se pudo guardar'));
  }

  return (
    <form className="grid" onSubmit={onSubmit} noValidate style={{ marginTop: 14 }}>
      <div className="field">
        <label htmlFor="edit-nombre">Nombre</label>
        <input id="edit-nombre" value={nombre} onChange={(e) => setNombre(e.target.value)} maxLength={200} />
      </div>
      <div className="field">
        <label htmlFor="edit-desc">Descripción</label>
        <textarea id="edit-desc" rows={2} value={descripcion} onChange={(e) => setDescripcion(e.target.value)} maxLength={5000} />
      </div>
      {draft && <ExpertFields value={expert} onChange={setExpert} />}
      {project.tipo === 'expertos' && !draft && (
        <p className="small muted" style={{ margin: 0 }}>Las etiquetas y los parámetros quedaron fijos al abrir el proyecto.</p>
      )}
      <div className="actions">
        <button type="submit" className="btn-primary" disabled={busy || !nombre.trim()}>{busy ? 'Guardando…' : 'Guardar'}</button>
        <button type="button" className="btn" onClick={onCancel}>Cancelar</button>
        <span className="status err">{msg}</span>
      </div>
    </form>
  );
}

// --- invitaciones ------------------------------------------------------------------------------

function LinkBox({ invitation, onClose }) {
  const [copied, setCopied] = useState(false);
  async function copy() {
    try {
      await navigator.clipboard.writeText(invitation.link);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {}
  }
  let delivery;
  if (invitation.envio === 'manual') delivery = 'Copia el enlace y envíaselo al experto.';
  else if (invitation.envio_error) delivery = `No se pudo enviar el correo (${invitation.envio_error}). Envía el enlace a mano.`;
  else delivery = `Enviado por correo a ${invitation.email}. También puedes copiar el enlace.`;
  return (
    <div className="validation" style={{ marginBottom: 12 }}>
      <div className="small" style={{ marginBottom: 6 }}>
        <strong>Enlace para {invitation.email}</strong>{' '}
        <span className={invitation.envio_error ? 'status err' : 'muted'}>{delivery}</span>
      </div>
      <div className="controls" style={{ marginBottom: 0 }}>
        <input className="mono link-input" readOnly value={invitation.link} onFocus={(e) => e.target.select()} aria-label="Enlace de invitación" />
        <button type="button" onClick={copy}>{copied ? 'Copiado' : 'Copiar'}</button>
        <button type="button" onClick={onClose}>Ocultar</button>
      </div>
      <div className="small muted" style={{ marginTop: 6 }}>
        Solo se muestra ahora: si se pierde, regenera la invitación (el enlace anterior deja de funcionar).
      </div>
    </div>
  );
}

function Invitations({ project, onChanged }) {
  const [items, setItems] = useState(null);
  const [email, setEmail] = useState('');
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState('');
  const [link, setLink] = useState(null);
  const closed = project.estado === 'cerrado';

  const load = useCallback(async () => {
    const { ok, data } = await send(`/app/projects/${project.id}/invitations`, 'GET').catch(() => ({}));
    if (ok) setItems(data);
  }, [project.id]);

  useEffect(() => {
    load();
  }, [load]);

  async function invite(e) {
    e.preventDefault();
    setBusy(true);
    setMsg('');
    const { ok, data } = await send(`/app/projects/${project.id}/invitations`, 'POST', { email: email.trim() })
      .catch(() => ({ ok: false, data: {} }));
    setBusy(false);
    if (ok) {
      setLink(data);
      setEmail('');
      load();
      onChanged();
    } else {
      setMsg(errorText(data, 'No se pudo crear la invitación'));
    }
  }

  async function action(inv, verb) {
    if (verb === 'revoke' && !window.confirm(`¿Revocar la invitación de ${inv.email}?`)) return;
    const { ok, data } = await send(`/app/projects/${project.id}/invitations/${inv.id}/${verb}`, 'POST')
      .catch(() => ({ ok: false, data: {} }));
    if (!ok) {
      setMsg(errorText(data, 'No se pudo actualizar la invitación'));
      return;
    }
    if (verb === 'regenerate') setLink(data);
    load();
  }

  return (
    <div className="card">
      <h2>Invitaciones</h2>
      <p className="sub">
        Cada experto recibe un enlace personal de un solo uso. Solo puede responder mientras el proyecto está <strong>abierto</strong>.
      </p>
      {link && <LinkBox invitation={link} onClose={() => setLink(null)} />}
      {!closed && (
        <form className="controls" onSubmit={invite} noValidate>
          <input type="email" className="inline-input" placeholder="correo@experto.cl" value={email} onChange={(e) => setEmail(e.target.value)} aria-label="Correo del experto" />
          <button type="submit" className="primary" disabled={busy || !email.trim()}>{busy ? 'Invitando…' : 'Invitar'}</button>
          <span className="status err small">{msg}</span>
        </form>
      )}
      <div style={{ overflow: 'auto' }}>
        <table>
          <thead><tr><th>Correo</th><th>Estado</th><th>Creada</th><th>Vence</th><th>Envío</th><th /></tr></thead>
          <tbody>
            {items === null && <tr><td colSpan="6" className="empty">Cargando…</td></tr>}
            {items !== null && items.length === 0 && <tr><td colSpan="6" className="empty">Sin invitaciones</td></tr>}
            {(items || []).map((inv) => (
              <tr key={inv.id}>
                <td>{inv.email}</td>
                <td><span className={`badge inv-${inv.estado}`}>{inv.estado}</span></td>
                <td className="small">{fmtDate(inv.created_at)}</td>
                <td className="small">{inv.estado === 'usada' ? '—' : fmtDate(inv.expira_at)}</td>
                <td className="small" title={inv.envio_error || undefined}>
                  {inv.enviado_at ? 'enviado' : inv.envio_error ? <span className="status err">falló</span> : '—'}
                </td>
                <td style={{ whiteSpace: 'nowrap' }}>
                  {inv.estado === 'pendiente' && <button type="button" className="btn" onClick={() => action(inv, 'revoke')}>Revocar</button>}{' '}
                  {inv.estado !== 'usada' && !closed && <button type="button" className="btn" onClick={() => action(inv, 'regenerate')}>Regenerar</button>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// --- inputs de expertos ------------------------------------------------------------------------

function canRetry(item) {
  const last = item.calculos[0];
  return last ? ['error', 'partial'].includes(last.status) : true;
}

function Inputs({ project, onChanged }) {
  const [items, setItems] = useState(null);
  const [msg, setMsg] = useState('');
  const [busyId, setBusyId] = useState(null);

  const load = useCallback(async () => {
    const { ok, data } = await send(`/app/projects/${project.id}/inputs`, 'GET').catch(() => ({}));
    if (ok) setItems(data);
    return ok ? data : null;
  }, [project.id]);

  useEffect(() => {
    let timer;
    let cancelled = false;
    async function poll() {
      const data = await load();
      if (cancelled) return;
      // Sigue consultando mientras haya cálculos en curso (o envíos recientes sin cálculo aún).
      const pending = (data || []).some((i) => i.calculos[0]?.status === 'pending' || (!i.calculos.length && !i.launch_error));
      if (pending) timer = setTimeout(poll, POLL_MS);
    }
    poll();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [load]);

  async function toggleExcluded(item) {
    const { ok, data } = await send(`/app/projects/${project.id}/inputs/${item.id}`, 'PATCH', { excluido: !item.excluido })
      .catch(() => ({ ok: false, data: {} }));
    if (ok) load();
    else setMsg(errorText(data, 'No se pudo actualizar el input'));
  }

  async function retry(item) {
    setBusyId(item.id);
    setMsg('');
    const { ok, data } = await send(`/app/projects/${project.id}/inputs/${item.id}/retry`, 'POST')
      .catch(() => ({ ok: false, data: {} }));
    setBusyId(null);
    if (!ok) setMsg(errorText(data, 'No se pudo relanzar el cálculo'));
    await load();
    onChanged();
  }

  return (
    <div className="card">
      <h2>Inputs de expertos</h2>
      <p className="sub">
        Al recibir un input se lanza automáticamente forgeffects con THR = {project.thr} y orden máximo {project.maxorder}.
        Un input excluido se conserva, pero no entra en los análisis conjuntos.
      </p>
      {msg && <p className="status err small">{msg}</p>}
      <div style={{ overflow: 'auto' }}>
        <table>
          <thead><tr><th>Experto</th><th>Recibido</th><th>Cálculo</th><th>Excluido</th><th /></tr></thead>
          <tbody>
            {items === null && <tr><td colSpan="5" className="empty">Cargando…</td></tr>}
            {items !== null && items.length === 0 && <tr><td colSpan="5" className="empty">Aún no hay respuestas</td></tr>}
            {(items || []).map((item) => {
              const last = item.calculos[0];
              return (
                <tr key={item.id} className={item.excluido ? 'row-muted' : undefined}>
                  <td>{item.email || `invitación ${item.invitation_id}`}</td>
                  <td className="small">{fmtDate(item.created_at)}</td>
                  <td>
                    {last && (
                      <Link to={`/fe/jobs/${last.job_id}`} className="small">
                        <span className={`badge ${jobBadge(last.status)}`}>{last.status}</span> job <span className="mono">{last.job_id}</span>
                      </Link>
                    )}
                    {!last && item.launch_error && <span className="badge error" title={item.launch_error}>no lanzado</span>}
                    {!last && !item.launch_error && <span className="small muted">lanzando…</span>}
                    {item.calculos.length > 1 && <div className="small muted">{item.calculos.length - 1} intentos anteriores</div>}
                    {!last && item.launch_error && <div className="small status err">{item.launch_error}</div>}
                  </td>
                  <td>
                    <input type="checkbox" checked={item.excluido} onChange={() => toggleExcluded(item)} aria-label="Excluir de los análisis conjuntos" />
                  </td>
                  <td>
                    {canRetry(item) && (item.launch_error || last) && (
                      <button type="button" className="btn" disabled={busyId === item.id} onClick={() => retry(item)}>
                        {busyId === item.id ? 'Relanzando…' : 'Reintentar'}
                      </button>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// --- simulación --------------------------------------------------------------------------------

function SimulationJobs({ project, refreshKey }) {
  const [jobs, setJobs] = useState(null);

  useEffect(() => {
    let timer;
    let cancelled = false;
    async function poll() {
      const { ok, data } = await send(`/app/projects/${project.id}/jobs`, 'GET').catch(() => ({}));
      if (cancelled) return;
      if (ok) setJobs(data);
      if (!ok || data.some((j) => j.status === 'pending')) timer = setTimeout(poll, POLL_MS);
    }
    poll();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [project.id, refreshKey]);

  return (
    <div className="card">
      <h2 style={{ marginBottom: 10 }}>Cálculos MaxMin</h2>
      <div style={{ overflow: 'auto' }}>
        <table>
          <thead><tr><th>Job</th><th>Estado</th><th>N</th><th>c</th><th>THR</th><th>Seed</th><th>Orden</th><th>Tiempo (s)</th><th>Nodo</th><th>Creado</th></tr></thead>
          <tbody>
            {jobs === null && <tr><td colSpan="10" className="empty">Cargando…</td></tr>}
            {jobs !== null && jobs.length === 0 && <tr><td colSpan="10" className="empty">Sin cálculos</td></tr>}
            {(jobs || []).map((j) => {
              const p = j.params || {};
              return (
                <tr key={j.job_id}>
                  <td className="mono">{j.job_id}</td>
                  <td><span className={`badge ${jobBadge(j.status)}`} title={j.logs || undefined}>{j.status}</span></td>
                  <td>{p.numero_nodos ?? '—'}</td>
                  <td>{p.conectividad_promedio ?? '—'}</td>
                  <td>{p.threshold ?? '—'}</td>
                  <td>{p.seed ?? '—'}</td>
                  <td>{j.effective_order ?? '—'}</td>
                  <td>{j.computation_time_s != null ? j.computation_time_s.toFixed(2) : '—'}</td>
                  <td>{j.node}</td>
                  <td className="small">{fmtDate(j.created_at)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// --- página ------------------------------------------------------------------------------------

export default function ProjectDetail() {
  const { projectId } = useParams();
  const navigate = useNavigate();
  const [project, setProject] = useState(null);
  const [error, setError] = useState('');
  const [editing, setEditing] = useState(false);
  const [msg, setMsg] = useState('');
  const [refreshKey, setRefreshKey] = useState(0);

  const load = useCallback(async () => {
    const { ok, data } = await send(`/app/projects/${projectId}`, 'GET').catch(() => ({ ok: false, data: {} }));
    if (ok) setProject(data);
    else setError(errorText(data, 'No se pudo cargar el proyecto'));
  }, [projectId]);

  useEffect(() => {
    load();
  }, [load]);

  async function transition(estado) {
    const confirmText = TRANSITION_CONFIRM[estado]?.[project.estado];
    if (confirmText && !window.confirm(confirmText)) return;
    const { ok, data } = await send(`/app/projects/${project.id}/estado`, 'POST', { estado }).catch(() => ({ ok: false, data: {} }));
    if (ok) {
      setProject(data);
      setMsg('');
      load();
    } else {
      setMsg(errorText(data, 'No se pudo cambiar el estado'));
    }
  }

  async function remove() {
    if (!window.confirm(`¿Borrar el proyecto «${project.nombre}»?`)) return;
    const { ok, data } = await send(`/app/projects/${project.id}`, 'DELETE').catch(() => ({ ok: false, data: {} }));
    if (ok) navigate('/projects');
    else setMsg(errorText(data, 'No se pudo borrar el proyecto'));
  }

  if (error) {
    return (
      <div className="card">
        <p className="status err">{error}</p>
        <Link to="/projects" className="small">← proyectos</Link>
      </div>
    );
  }
  if (!project) return <div className="card"><div className="empty">Cargando…</div></div>;

  const expert = project.tipo === 'expertos';
  const empty = !project.conteos.inputs && !project.conteos.calculos;

  return (
    <div className="stack">
      <div className="card">
        <div className="small"><Link to="/projects" className="muted">← proyectos</Link></div>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 12, flexWrap: 'wrap' }}>
          <h1>{project.nombre}</h1>
          <div className="controls" style={{ marginBottom: 0 }}>
            {project.transiciones.map((t) => (
              <button key={t} type="button" className={t === 'abierto' ? 'primary' : undefined} onClick={() => transition(t)}>
                {TRANSITION_LABELS[t]?.[project.estado] || t}
              </button>
            ))}
            {!editing && <button type="button" onClick={() => setEditing(true)}>Editar</button>}
            {empty && <button type="button" onClick={remove}>Borrar</button>}
          </div>
        </div>
        {msg && <p className="status err small">{msg}</p>}
        {project.descripcion && !editing && <p className="sub" style={{ whiteSpace: 'pre-wrap' }}>{project.descripcion}</p>}
        {editing ? (
          <EditProject
            project={project}
            onSaved={(p) => { setProject({ ...project, ...p }); setEditing(false); }}
            onCancel={() => setEditing(false)}
          />
        ) : (
          <div className="kv">
            <span>Tipo</span><span>{TIPO_LABELS[project.tipo]}</span>
            <span>Estado</span><span><span className={`badge estado-${project.estado}`}>{project.estado}</span></span>
            {expert && <>
              <span>Causas ({project.causas.length})</span><span className="small">{project.causas.join(', ')}</span>
              <span>Efectos ({project.efectos.length})</span><span className="small">{project.efectos.join(', ')}</span>
              <span>THR</span><span>{project.thr}</span>
              <span>Orden máximo</span><span>{project.maxorder}</span>
            </>}
            <span>Creado</span><span className="small">{fmtDate(project.created_at)}</span>
          </div>
        )}
      </div>

      {expert && <Invitations project={project} onChanged={load} />}
      {expert && <Inputs project={project} onChanged={load} />}

      {!expert && (
        <div className="card">
          <h2 style={{ marginBottom: 10 }}>Nuevo cálculo MaxMin</h2>
          {project.estado !== 'abierto' && <p className="small muted">El proyecto está cerrado: reábrelo para lanzar cálculos.</p>}
          <MaxMinForm projectId={project.id} disabled={project.estado !== 'abierto'} onLaunched={() => { setRefreshKey((k) => k + 1); load(); }} />
        </div>
      )}
      {!expert && <SimulationJobs project={project} refreshKey={refreshKey} />}
    </div>
  );
}
