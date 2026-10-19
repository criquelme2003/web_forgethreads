import { useCallback, useEffect, useRef, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { apiJson, fetchMe, send } from '../api.js';
import LinkBox from '../components/LinkBox.jsx';
import { errorText } from '../components/MatricesInput.jsx';
import MaxMinForm from '../components/MaxMinForm.jsx';
import { ExpertFields, expertPayload, fmtDate, TIPO_LABELS } from './Projects.jsx';

const POLL_MS = 5000; // con cálculos en curso
const IDLE_POLL_MS = 15000; // proyecto abierto sin cálculos en curso: esperando inputs nuevos

const TRANSITION_LABELS = {
  abierto: { borrador: 'Abrir a expertos', cerrado: 'Reabrir proyecto' },
  cerrado: { abierto: 'Cerrar proyecto' },
};
const TRANSITION_CONFIRM = {
  abierto: {
    borrador: 'Al abrir el proyecto, las etiquetas quedan fijas. ¿Continuar?',
  },
  cerrado: {
    abierto: '¿Cerrar el proyecto? No se aceptarán nuevas respuestas ni cálculos hasta reabrirlo.',
  },
};

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
        <p className="small muted" style={{ margin: 0 }}>Las etiquetas quedaron fijas al abrir el proyecto.</p>
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

function expertName(item) {
  return item.nombre ? `${item.nombre} <${item.email}>` : item.email;
}

function InvitationLink({ invitation, onClose }) {
  return (
    <LinkBox
      title={`Enlace para ${expertName(invitation)}`}
      link={invitation.link}
      envio={invitation.envio}
      envioError={invitation.envio_error}
      email={invitation.email}
      onClose={onClose}
      hint="Solo se muestra ahora: si se pierde, regenera la invitación (el enlace anterior deja de funcionar)."
    />
  );
}

function Invitations({ project, onChanged, refreshKey }) {
  const [items, setItems] = useState(null);
  const [email, setEmail] = useState('');
  const [nombre, setNombre] = useState('');
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState('');
  const [links, setLinks] = useState([]); // enlaces recién generados (se ven solo ahora)
  const closed = project.estado === 'cerrado';

  const load = useCallback(async () => {
    const { ok, data } = await send(`/app/projects/${project.id}/invitations`, 'GET').catch(() => ({}));
    if (ok) setItems(data);
  }, [project.id]);

  useEffect(() => {
    load();
  }, [load, refreshKey]);

  async function invite(e) {
    e.preventDefault();
    setBusy(true);
    setMsg('');
    const { ok, data } = await send(`/app/projects/${project.id}/invitations`, 'POST', { email: email.trim(), nombre: nombre.trim() || null })
      .catch(() => ({ ok: false, data: {} }));
    setBusy(false);
    if (ok) {
      setLinks([data]);
      setEmail('');
      setNombre('');
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
    if (verb === 'regenerate') setLinks([data]);
    load();
  }

  async function remove(inv) {
    if (!window.confirm(`¿Eliminar a ${inv.email} del proyecto? Su enlace dejará de funcionar.`)) return;
    const { ok, data } = await send(`/app/projects/${project.id}/invitations/${inv.id}`, 'DELETE')
      .catch(() => ({ ok: false, data: {} }));
    if (!ok) setMsg(errorText(data, 'No se pudo eliminar'));
    load();
    onChanged();
  }

  const pending = (items || []).filter((i) => i.estado === 'pendiente' || i.estado === 'expirada');

  /** Reenvío por lotes a quienes no han respondido (RF-50): regenera cada enlace. */
  async function resendPending() {
    const list = pending.map((i) => i.email).join(', ');
    if (!window.confirm(`¿Reenviar a ${pending.length} expertos sin respuesta (${list})? Sus enlaces anteriores dejarán de funcionar.`)) return;
    setBusy(true);
    setMsg('');
    const { ok, data } = await send(`/app/projects/${project.id}/invitations/resend`, 'POST', {})
      .catch(() => ({ ok: false, data: {} }));
    setBusy(false);
    if (!ok) {
      setMsg(errorText(data, 'No se pudo reenviar'));
      return;
    }
    setLinks(data.reenviadas);
    if (data.omitidas.length) setMsg(`${data.omitidas.length} omitidas: ${data.omitidas.map((o) => o.detail).join(', ')}`);
    load();
  }

  return (
    <div className="card">
      <h2>Invitaciones</h2>
      <p className="sub">
        Cada experto recibe un enlace personal. Solo puede responder mientras el proyecto está <strong>abierto</strong>;
        después de enviar, con el mismo enlace puede modificar su input durante una semana. Se puede eliminar a quien
        aún no responde; a quien ya respondió se lo excluye en la tabla de inputs.
      </p>
      {links.length > 0 && (
        <div className="link-list">
          {links.map((l) => (
            <InvitationLink key={l.id} invitation={l} onClose={() => setLinks((prev) => prev.filter((x) => x.id !== l.id))} />
          ))}
        </div>
      )}
      {!closed && (
        <form className="controls" onSubmit={invite} noValidate>
          <input className="inline-input" placeholder="Nombre (opcional)" value={nombre} onChange={(e) => setNombre(e.target.value)} aria-label="Nombre del experto" maxLength={200} />
          <input type="email" className="inline-input" placeholder="correo@experto.cl" value={email} onChange={(e) => setEmail(e.target.value)} aria-label="Correo del experto" />
          <button type="submit" className="primary" disabled={busy || !email.trim()}>{busy ? 'Invitando…' : 'Invitar'}</button>
          {pending.length > 0 && (
            <button type="button" disabled={busy} onClick={resendPending}>Reenviar a quienes no han respondido ({pending.length})</button>
          )}
        </form>
      )}
      {msg && <p className="status err small">{msg}</p>}
      <div style={{ overflow: 'auto' }}>
        <table>
          <thead><tr><th>Experto</th><th>Estado</th><th>Creada</th><th>Vence</th><th>Envío</th><th /></tr></thead>
          <tbody>
            {items === null && <tr><td colSpan="6" className="empty">Cargando…</td></tr>}
            {items !== null && items.length === 0 && <tr><td colSpan="6" className="empty">Sin invitaciones</td></tr>}
            {(items || []).map((inv) => (
              <tr key={inv.id}>
                <td>{inv.nombre ? <>{inv.nombre} <span className="small muted">{inv.email}</span></> : inv.email}</td>
                <td><span className={`badge inv-${inv.estado}`}>{inv.estado === 'usada' ? 'respondida' : inv.estado}</span></td>
                <td className="small">{fmtDate(inv.created_at)}</td>
                <td className="small">{inv.estado === 'usada' ? '—' : fmtDate(inv.expira_at)}</td>
                <td className="small" title={inv.envio_error || undefined}>
                  {inv.enviado_at ? 'enviado' : inv.envio_error ? <span className="status err">falló</span> : '—'}
                </td>
                <td style={{ whiteSpace: 'nowrap' }}>
                  {inv.estado === 'pendiente' && <button type="button" className="btn" onClick={() => action(inv, 'revoke')}>Revocar</button>}{' '}
                  {inv.estado !== 'usada' && !closed && <button type="button" className="btn" onClick={() => action(inv, 'regenerate')}>Regenerar</button>}{' '}
                  {inv.estado !== 'usada' && <button type="button" className="btn" onClick={() => remove(inv)}>Eliminar</button>}
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

function ComputationList({ calculos }) {
  if (!calculos.length) return <span className="small muted">sin cálculos</span>;
  return (
    <div className="calc-list">
      {calculos.map((c) => (
        <Link key={c.job_id} to={`/fe/jobs/${c.job_id}`} className="small">
          <span className={`badge ${jobBadge(c.status)}`}>{c.status}</span>{' '}
          THR {c.thr ?? '—'} · orden {c.maxorder ?? '—'} · v{c.input_version} · job <span className="mono">{c.job_id}</span>
          {c.desactualizado && <>{' '}<span className="badge outdated" title="El experto modificó su input después de este cálculo">input modificado</span></>}
        </Link>
      ))}
    </div>
  );
}

/** Lanza forgeffects sobre los inputs elegidos con el THR y el orden máximo que indica el admin (RF-21). */
function ComputeForm({ project, selected, names, onDone }) {
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
    const thrValue = Number(thr.replace(',', '.'));
    const maxorderValue = Number(maxorder);
    if (!(thrValue >= 0 && thrValue <= 1)) {
      setMsg({ text: 'THR debe estar en [0,1].', kind: 'err' });
      return;
    }
    if (!Number.isInteger(maxorderValue) || maxorderValue < 2 || maxorderValue > 10) {
      setMsg({ text: 'El orden máximo debe ser un entero entre 2 y 10.', kind: 'err' });
      return;
    }
    setBusy(true);
    setMsg({ text: `Lanzando ${selected.length} cálculos…`, kind: 'muted' });
    const { ok, data } = await send(`/app/projects/${project.id}/compute`, 'POST', {
      thr: thrValue, maxorder: maxorderValue, nodo: nodo || null, input_ids: selected,
    }).catch(() => ({ ok: false, data: {} }));
    setBusy(false);
    if (!ok) {
      setMsg({ text: errorText(data, 'No se pudieron lanzar los cálculos'), kind: 'err' });
      return;
    }
    if (data.errores.length) {
      const detail = data.errores.map((x) => `${names[x.input_id] || `input ${x.input_id}`}: ${x.detail}`).join(' · ');
      setMsg({ text: `${data.lanzados.length} lanzados, ${data.errores.length} con error (${detail}). Puedes volver a lanzarlos.`, kind: 'err' });
    } else {
      setMsg({ text: `${data.lanzados.length} cálculos lanzados.`, kind: 'ok' });
    }
    onDone();
  }

  return (
    <form className="compute-form" onSubmit={onSubmit} noValidate>
      <div className="grid-3">
        <div className="field">
          <label htmlFor="compute-thr">THR</label>
          <input id="compute-thr" type="text" inputMode="decimal" value={thr} onChange={(e) => setThr(e.target.value)} />
          <div className="hint">Umbral en [0,1]</div>
        </div>
        <div className="field">
          <label htmlFor="compute-maxorder">Orden máximo</label>
          <input id="compute-maxorder" type="number" step="1" min="2" max="10" value={maxorder} onChange={(e) => setMaxorder(e.target.value)} />
          <div className="hint">Entero entre 2 y 10</div>
        </div>
        <div className="field">
          <label htmlFor="compute-nodo">Nodo</label>
          <select id="compute-nodo" value={nodo} onChange={(e) => setNodo(e.target.value)}>
            <option value="">Automático</option>
            {nodeOptions.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
          <div className="hint">Nodo SLURM con GPU</div>
        </div>
      </div>
      <div className="actions">
        <button type="submit" className="btn-primary" disabled={busy || selected.length === 0}>
          {busy ? 'Lanzando…' : `Calcular ${selected.length} ${selected.length === 1 ? 'input' : 'inputs'}`}
        </button>
        <span className={`status ${msg.kind} small`} role="status" aria-live="polite">{msg.text}</span>
      </div>
    </form>
  );
}

function Inputs({ project, onChanged, onNewInputs }) {
  const [items, setItems] = useState(null);
  const [selected, setSelected] = useState(null); // Set de ids; null = aún sin cargar
  const [msg, setMsg] = useState('');
  const [pollKey, setPollKey] = useState(0);
  const knownIds = useRef(null); // ids ya vistos, para elegir por defecto solo los inputs nuevos

  const load = useCallback(async () => {
    const { ok, data } = await send(`/app/projects/${project.id}/inputs`, 'GET').catch(() => ({}));
    if (!ok) return null;
    setItems(data);
    // Por defecto quedan elegidos los inputs no excluidos; los nuevos se agregan a la selección.
    const known = knownIds.current;
    setSelected((prev) => {
      const next = new Set(prev ? [...prev].filter((id) => data.some((i) => i.id === id)) : []);
      for (const i of data) if (!i.excluido && (known === null || !known.has(i.id))) next.add(i.id);
      return next;
    });
    knownIds.current = new Set(data.map((i) => i.id));
    return data;
  }, [project.id]);

  useEffect(() => {
    let timer;
    let cancelled = false;
    let lastCount = null;
    let lastPending = false;
    let inFlight = false;

    function schedule() {
      // Con cálculos en curso, cada 5 s. Abierto sin cálculos, cada 15 s (pueden llegar inputs).
      // Cerrado y sin cálculos en curso no puede cambiar nada: se deja de consultar.
      const delay = lastPending ? POLL_MS : project.estado === 'abierto' ? IDLE_POLL_MS : null;
      if (delay) timer = setTimeout(poll, delay);
    }

    async function poll() {
      // Con la pestaña oculta no se consulta; se retoma al volver (ver onVisible).
      if (document.hidden || inFlight) return;
      inFlight = true;
      const data = await load().finally(() => { inFlight = false; });
      if (cancelled) return;
      if (data) {
        if (lastCount !== null && data.length > lastCount) onNewInputs?.();
        lastCount = data.length;
        lastPending = data.some((i) => i.calculos.some((c) => c.status === 'pending'));
      }
      schedule();
    }

    function onVisible() {
      if (document.hidden || cancelled) return;
      clearTimeout(timer);
      poll();
    }

    poll();
    document.addEventListener('visibilitychange', onVisible);
    return () => {
      cancelled = true;
      clearTimeout(timer);
      document.removeEventListener('visibilitychange', onVisible);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [load, pollKey, project.estado]);

  async function toggleExcluded(item) {
    const { ok, data } = await send(`/app/projects/${project.id}/inputs/${item.id}`, 'PATCH', { excluido: !item.excluido })
      .catch(() => ({ ok: false, data: {} }));
    if (!ok) {
      setMsg(errorText(data, 'No se pudo actualizar el input'));
      return;
    }
    setSelected((prev) => {
      const next = new Set(prev);
      if (data.excluido) next.delete(item.id);
      return next;
    });
    load();
  }

  function toggleSelected(id) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  const all = items || [];
  const sel = selected || new Set();
  const allSelected = all.length > 0 && all.every((i) => sel.has(i.id));

  return (
    <div className="card">
      <h2>Inputs de expertos</h2>
      <p className="sub">
        Los inputs se guardan al recibirse; los cálculos se lanzan desde aquí con el THR y el orden máximo que elijas,
        y un mismo input puede tener varios cálculos. Un input excluido se conserva, pero no entra en los análisis conjuntos.
      </p>
      {all.length > 0 && <ComputeForm project={project} selected={all.filter((i) => sel.has(i.id)).map((i) => i.id)} names={Object.fromEntries(all.map((i) => [i.id, i.nombre || i.email]))} onDone={() => { setPollKey((k) => k + 1); onChanged(); }} />}
      {msg && <p className="status err small">{msg}</p>}
      <div style={{ overflow: 'auto' }}>
        <table>
          <thead>
            <tr>
              <th>
                <input
                  type="checkbox"
                  checked={allSelected}
                  disabled={all.length === 0}
                  onChange={() => setSelected(allSelected ? new Set() : new Set(all.map((i) => i.id)))}
                  aria-label="Elegir todos"
                />
              </th>
              <th>Experto</th><th>Recibido</th><th>Cálculos</th><th>Excluido</th>
            </tr>
          </thead>
          <tbody>
            {items === null && <tr><td colSpan="5" className="empty">Cargando…</td></tr>}
            {items !== null && items.length === 0 && <tr><td colSpan="5" className="empty">Aún no hay respuestas</td></tr>}
            {all.map((item) => (
              <tr key={item.id} className={item.excluido ? 'row-muted' : undefined}>
                <td>
                  <input type="checkbox" checked={sel.has(item.id)} onChange={() => toggleSelected(item.id)} aria-label={`Elegir ${item.email || item.id}`} />
                </td>
                <td>{item.email ? expertName(item) : `invitación ${item.invitation_id}`}</td>
                <td className="small">
                  {fmtDate(item.created_at)}
                  {item.version > 1 && <div className="muted">v{item.version} · modificado {fmtDate(item.updated_at)}</div>}
                  {item.editable && <div className="muted">editable hasta {fmtDate(item.editable_hasta)}</div>}
                </td>
                <td><ComputationList calculos={item.calculos} /></td>
                <td>
                  <input type="checkbox" checked={item.excluido} onChange={() => toggleExcluded(item)} aria-label="Excluir de los análisis conjuntos" />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// --- administradores del proyecto (RF-49) ------------------------------------------------------

function personLabel(p) {
  return `${p.nombre || p.email}${p.nombre ? ` <${p.email}>` : ''}${p.activo ? '' : ' (desactivado)'}`;
}

function ProjectAdmins({ project, me, onChanged }) {
  const [directory, setDirectory] = useState([]);
  const [choice, setChoice] = useState('');
  const [msg, setMsg] = useState('');
  const manage = project.puede_gestionar;

  useEffect(() => {
    if (!manage) return;
    send('/app/users/directory', 'GET').then(({ ok, data }) => ok && setDirectory(data)).catch(() => {});
  }, [manage]);

  async function call(path, method, body, confirmText) {
    if (confirmText && !window.confirm(confirmText)) return;
    const { ok, data } = await send(path, method, body).catch(() => ({ ok: false, data: {} }));
    if (!ok) {
      setMsg(errorText(data, 'No se pudo actualizar'));
      return;
    }
    setMsg('');
    setChoice('');
    onChanged(data);
  }

  const members = new Set([project.owner?.id, ...project.admins.map((a) => a.id)]);
  const candidates = directory.filter((u) => !members.has(u.id));
  const base = `/app/projects/${project.id}`;

  return (
    <div className="card">
      <h2>Administradores</h2>
      <p className="sub">El dueño y los co-admins gestionan el proyecto. Solo el dueño (o el superadmin) agrega o quita co-admins y puede borrarlo.</p>
      <div className="people">
        <div className="people-row">
          <span className="badge estado-abierto">dueño</span>
          <span>{project.owner ? personLabel(project.owner) : '—'}</span>
        </div>
        {project.admins.map((a) => (
          <div key={a.id} className="people-row">
            <span className="badge estado-borrador">co-admin</span>
            <span>{personLabel(a)}</span>
            {manage && <button type="button" className="btn" onClick={() => call(`${base}/admins/${a.id}`, 'DELETE', null, `¿Quitar a ${a.email} del proyecto?`)}>Quitar</button>}
            {manage && a.activo && <button type="button" className="btn" onClick={() => call(`${base}/owner`, 'POST', { user_id: a.id }, `¿Transferir la propiedad a ${a.email}? Quedarás como co-admin.`)}>Hacer dueño</button>}
            {!manage && me && a.id === me.id && (
              <button type="button" className="btn" onClick={() => call(`${base}/admins/${a.id}`, 'DELETE', null, '¿Salir del proyecto? Dejarás de verlo.')}>Salir del proyecto</button>
            )}
          </div>
        ))}
      </div>
      {manage && (
        <div className="controls">
          <select value={choice} onChange={(e) => setChoice(e.target.value)} aria-label="Admin a agregar">
            <option value="">Agregar co-admin…</option>
            {candidates.map((u) => <option key={u.id} value={u.id}>{u.nombre || u.email} ({u.email})</option>)}
          </select>
          <button type="button" className="primary" disabled={!choice} onClick={() => call(`${base}/admins`, 'POST', { user_id: Number(choice) })}>Agregar</button>
          {candidates.length === 0 && <span className="small muted">No hay otros admins activos. Los crea el superadmin en Usuarios.</span>}
        </div>
      )}
      {msg && <p className="status err small">{msg}</p>}
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
                  <td className="mono"><Link to={`/maxmin/jobs/${j.job_id}`}>{j.job_id}</Link></td>
                  <td><span className={`badge ${jobBadge(j.status)}`}>{j.status}</span></td>
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
  const [inputsKey, setInputsKey] = useState(0); // sube al llegar inputs nuevos: recarga invitaciones
  const [me, setMe] = useState(null);

  useEffect(() => {
    fetchMe().then(setMe).catch(() => {});
  }, []);

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
            {empty && project.puede_gestionar && <button type="button" onClick={remove}>Borrar</button>}
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
            </>}
            <span>Creado</span><span className="small">{fmtDate(project.created_at)}</span>
          </div>
        )}
      </div>

      <ProjectAdmins
        project={project}
        me={me}
        onChanged={(p) => {
          if (me && !p.puede_gestionar && p.owner?.id !== me.id && !p.admins.some((a) => a.id === me.id) && me.rol !== 'superadmin') navigate('/projects');
          else setProject(p);
        }}
      />
      {expert && <Invitations project={project} onChanged={load} refreshKey={inputsKey} />}
      {expert && (
        <Inputs
          project={project}
          onChanged={load}
          onNewInputs={() => { setInputsKey((k) => k + 1); load(); }}
        />
      )}

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
