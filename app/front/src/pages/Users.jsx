import { useCallback, useEffect, useState } from 'react';
import { send } from '../api.js';
import LinkBox from '../components/LinkBox.jsx';
import { errorText } from '../components/MatricesInput.jsx';
import { fmtDate } from './Projects.jsx';

const ESTADO_LABELS = { activo: 'activo', pendiente: 'sin activar', inactivo: 'desactivado' };

/** Gestión de administradores: solo el superadmin (RF-01, RF-04, RF-48). */
export default function Users() {
  const [users, setUsers] = useState(null);
  const [error, setError] = useState('');
  const [email, setEmail] = useState('');
  const [nombre, setNombre] = useState('');
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState({ text: '', kind: 'muted' });
  const [link, setLink] = useState(null);
  const [deactivating, setDeactivating] = useState(null); // usuario a desactivar
  const [target, setTarget] = useState('');

  const load = useCallback(async () => {
    const { ok, status, data } = await send('/app/users', 'GET').catch(() => ({ ok: false, data: {} }));
    if (ok) setUsers(data);
    else setError(status === 403 ? 'Solo el superadmin gestiona usuarios.' : errorText(data, 'No se pudieron cargar los usuarios'));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function create(e) {
    e.preventDefault();
    setBusy(true);
    setMsg({ text: '', kind: 'muted' });
    const { ok, data } = await send('/app/users', 'POST', { email: email.trim(), nombre: nombre.trim() })
      .catch(() => ({ ok: false, data: {} }));
    setBusy(false);
    if (!ok) {
      setMsg({ text: errorText(data, 'No se pudo crear el usuario'), kind: 'err' });
      return;
    }
    setLink({ ...data, title: `Enlace de activación para ${data.email}` });
    setEmail('');
    setNombre('');
    load();
  }

  async function regenerate(u) {
    const verb = u.estado === 'pendiente' ? 'un nuevo enlace de activación' : 'un enlace para restablecer la contraseña';
    if (!window.confirm(`¿Generar ${verb} para ${u.email}? El enlace anterior deja de funcionar.`)) return;
    const { ok, data } = await send(`/app/users/${u.id}/activation-link`, 'POST').catch(() => ({ ok: false, data: {} }));
    if (!ok) {
      setMsg({ text: errorText(data, 'No se pudo generar el enlace'), kind: 'err' });
      return;
    }
    setLink({ ...data, title: `Enlace para ${data.email}` });
    load();
  }

  async function deactivate() {
    const u = deactivating;
    const body = { reasignar_a: target ? Number(target) : null };
    const { ok, data } = await send(`/app/users/${u.id}/deactivate`, 'POST', body).catch(() => ({ ok: false, data: {} }));
    setDeactivating(null);
    setTarget('');
    if (!ok) {
      setMsg({ text: errorText(data, 'No se pudo desactivar'), kind: 'err' });
      return;
    }
    const names = Object.fromEntries((users || []).map((x) => [x.id, x.nombre || x.email]));
    const moved = data.reasignados.map((m) => `«${m.nombre}» → ${names[m.owner_id] || m.owner_id}`).join(' · ');
    setMsg({ text: `${u.email} desactivado.${moved ? ` Proyectos reasignados: ${moved}.` : ''}`, kind: 'ok' });
    load();
  }

  async function reactivate(u) {
    const { ok, data } = await send(`/app/users/${u.id}/activate`, 'POST').catch(() => ({ ok: false, data: {} }));
    if (!ok) setMsg({ text: errorText(data, 'No se pudo reactivar'), kind: 'err' });
    load();
  }

  if (error) return <div className="card"><p className="status err">{error}</p></div>;

  const candidates = (users || []).filter((u) => u.estado === 'activo' && u.id !== deactivating?.id);

  return (
    <div className="stack">
      <div className="card">
        <h1>Usuarios</h1>
        <p className="sub">
          Los administradores crean y gestionan sus proyectos; pueden sumar co-admins a cada uno. Al crear un admin se genera
          un enlace de activación de un solo uso para que elija su contraseña; regenerarlo sirve para restablecerla.
        </p>
        {link && (
          <LinkBox
            title={link.title}
            link={link.link}
            envio={link.envio}
            envioError={link.envio_error}
            email={link.email}
            onClose={() => setLink(null)}
            hint="Solo se muestra ahora. Si se pierde, genera otro."
          />
        )}
        <form className="controls" onSubmit={create} noValidate>
          <input className="inline-input" placeholder="Nombre" value={nombre} onChange={(e) => setNombre(e.target.value)} aria-label="Nombre" maxLength={200} />
          <input type="email" className="inline-input" placeholder="correo@uct.cl" value={email} onChange={(e) => setEmail(e.target.value)} aria-label="Correo" />
          <button type="submit" className="primary" disabled={busy || !email.trim() || !nombre.trim()}>{busy ? 'Creando…' : 'Crear admin'}</button>
        </form>
        {msg.text && <p className={`status ${msg.kind} small`} role="status">{msg.text}</p>}
        {deactivating && (
          <div className="validation" style={{ marginBottom: 12 }}>
            <p className="small" style={{ marginTop: 0 }}>
              <strong>Desactivar a {deactivating.email}.</strong> Pierde el acceso de inmediato.
              {deactivating.proyectos > 0 && ` Sus ${deactivating.proyectos} proyectos pasan a:`}
            </p>
            <div className="controls" style={{ marginBottom: 0 }}>
              {deactivating.proyectos > 0 && (
                <select value={target} onChange={(e) => setTarget(e.target.value)} aria-label="Nuevo dueño">
                  <option value="">Su co-admin más antiguo (o yo, si no tiene)</option>
                  {candidates.map((u) => <option key={u.id} value={u.id}>{u.nombre || u.email}</option>)}
                </select>
              )}
              <button type="button" className="primary" onClick={deactivate}>Desactivar</button>
              <button type="button" onClick={() => { setDeactivating(null); setTarget(''); }}>Cancelar</button>
            </div>
          </div>
        )}
        <div style={{ overflow: 'auto' }}>
          <table>
            <thead><tr><th>Nombre</th><th>Correo</th><th>Rol</th><th>Estado</th><th>Proyectos</th><th>Creado</th><th /></tr></thead>
            <tbody>
              {users === null && <tr><td colSpan="7" className="empty">Cargando…</td></tr>}
              {(users || []).map((u) => (
                <tr key={u.id} className={u.estado === 'inactivo' ? 'row-muted' : undefined}>
                  <td>{u.nombre || '—'}</td>
                  <td>{u.email}</td>
                  <td>{u.rol}</td>
                  <td>
                    <span className={`badge user-${u.estado}`}>{ESTADO_LABELS[u.estado] || u.estado}</span>
                    {u.estado === 'pendiente' && u.activacion_expira_at && (
                      <div className="small muted">enlace vence {fmtDate(u.activacion_expira_at)}</div>
                    )}
                  </td>
                  <td>{u.proyectos}</td>
                  <td className="small">{fmtDate(u.created_at)}</td>
                  <td style={{ whiteSpace: 'nowrap' }}>
                    {u.rol !== 'superadmin' && u.estado !== 'inactivo' && (
                      <>
                        <button type="button" className="btn" onClick={() => regenerate(u)}>
                          {u.estado === 'pendiente' ? 'Nuevo enlace' : 'Restablecer contraseña'}
                        </button>{' '}
                        <button type="button" className="btn" onClick={() => { setDeactivating(u); setTarget(''); }}>Desactivar</button>
                      </>
                    )}
                    {u.estado === 'inactivo' && <button type="button" className="btn" onClick={() => reactivate(u)}>Reactivar</button>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
