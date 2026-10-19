import { useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import { errorText } from '../components/MatricesInput.jsx';

const MIN_PASSWORD = 10;

/** Pública: el admin elige su contraseña con el enlace de activación (RF-48). */
export default function Activate() {
  const { token } = useParams();
  const [info, setInfo] = useState(null);
  const [error, setError] = useState('');
  const [password, setPassword] = useState('');
  const [repeat, setRepeat] = useState('');
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState('');
  const [done, setDone] = useState(false);
  const base = `/app/activate/${encodeURIComponent(token)}`;

  useEffect(() => {
    fetch(base)
      .then(async (res) => {
        const body = await res.json().catch(() => ({}));
        if (res.ok) setInfo(body);
        else setError(errorText(body, 'El enlace no es válido.'));
      })
      .catch(() => setError('Error de red. Intenta nuevamente.'));
  }, [base]);

  async function onSubmit(e) {
    e.preventDefault();
    if (password.length < MIN_PASSWORD) {
      setMsg(`La contraseña debe tener al menos ${MIN_PASSWORD} caracteres.`);
      return;
    }
    if (password !== repeat) {
      setMsg('Las contraseñas no coinciden.');
      return;
    }
    setBusy(true);
    setMsg('');
    try {
      const res = await fetch(base, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ password }),
      });
      const body = await res.json().catch(() => ({}));
      if (res.ok) setDone(true);
      else setMsg(errorText(body, 'No se pudo guardar la contraseña.'));
    } catch {
      setMsg('Error de red.');
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <header className="topbar">
        <div className="topbar-inner">
          <div className="brand">forgethreads <span>· activación</span></div>
        </div>
      </header>
      <main className="wrap-login">
        <div className="card card-login">
          {error && (
            <>
              <h1>Enlace no disponible</h1>
              <p className="status err">{error}</p>
            </>
          )}
          {!error && !info && <div className="empty">Cargando…</div>}
          {done && (
            <>
              <h1>Contraseña guardada</h1>
              <p className="sub">Ya puedes entrar con tu correo <strong>{info.email}</strong>.</p>
              <a className="btn primary" href="/front/login">Iniciar sesión</a>
            </>
          )}
          {info && !done && (
            <>
              <h1>Elige tu contraseña</h1>
              <p className="sub">Cuenta de administrador de <strong>{info.nombre || info.email}</strong> ({info.email}).</p>
              <form className="grid-tight" onSubmit={onSubmit} noValidate>
                <div className="field">
                  <label htmlFor="password">Contraseña</label>
                  <input id="password" type="password" autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} />
                  <div className="hint">Al menos {MIN_PASSWORD} caracteres.</div>
                </div>
                <div className="field">
                  <label htmlFor="repeat">Repite la contraseña</label>
                  <input id="repeat" type="password" autoComplete="new-password" value={repeat} onChange={(e) => setRepeat(e.target.value)} />
                </div>
                <button type="submit" className="btn-primary" disabled={busy}>{busy ? 'Guardando…' : 'Guardar contraseña'}</button>
                <div className="status err" role="status" aria-live="polite">{msg}</div>
              </form>
            </>
          )}
        </div>
      </main>
    </>
  );
}
