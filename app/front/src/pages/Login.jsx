import { useState } from 'react';
import { useSearchParams } from 'react-router-dom';

export default function Login() {
  const [params] = useSearchParams();
  const next = params.get('next') || '/front/projects';
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [msg, setMsg] = useState('');
  const [kind, setKind] = useState('muted');
  const [busy, setBusy] = useState(false);

  function setStatus(t, k) {
    setMsg(t);
    setKind(k || 'muted');
  }

  async function onSubmit(e) {
    e.preventDefault();
    setStatus('', 'muted');
    setBusy(true);
    try {
      const res = await fetch('/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password }),
      });
      if (res.ok) {
        setStatus('Sesión iniciada. Redirigiendo…', 'ok');
        setTimeout(() => {
          window.location.href = next;
        }, 400);
      } else {
        const body = await res.json().catch(() => ({}));
        setStatus(body.detail || 'Credenciales inválidas.', 'err');
      }
    } catch {
      setStatus('Error de red.', 'err');
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <header className="topbar">
        <div className="topbar-inner">
          <div className="brand">forgethreads <span>· acceso</span></div>
        </div>
      </header>
      <main className="wrap-login">
        <div className="card card-login">
          <h1>Iniciar sesión</h1>
          <p className="sub">Administradores: entra con tu correo.</p>
          <form className="grid-tight" onSubmit={onSubmit} noValidate>
            <div className="field">
              <label htmlFor="username">Correo</label>
              <input
                id="username" type="text" name="username" required autoComplete="username"
                placeholder="correo@uct.cl" value={username} onChange={(e) => setUsername(e.target.value)}
              />
            </div>
            <div className="field">
              <label htmlFor="password">Contraseña</label>
              <input
                id="password" type="password" name="password" required autoComplete="current-password"
                placeholder="••••••••" value={password} onChange={(e) => setPassword(e.target.value)}
              />
            </div>
            <button type="submit" className="btn-primary" disabled={busy}>
              {busy ? 'Entrando…' : 'Entrar'}
            </button>
            <div className={`status ${kind}`} role="status" aria-live="polite">{msg}</div>
          </form>
          <div className="foot">
            <span>¿Olvidaste tu contraseña? Pide un enlace nuevo al superadmin.</span>
          </div>
        </div>
      </main>
    </>
  );
}
