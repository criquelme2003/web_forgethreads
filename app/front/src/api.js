/** Cliente HTTP mínimo: usa la cookie de sesión y ante 401 redirige al login con ?next=. */

export function loginRedirect() {
  const next = encodeURIComponent(window.location.pathname + window.location.search);
  window.location.href = `/front/login?next=${next}`;
}

/** fetch con cookies; ante 401 redirige al login (no usar en el propio POST de login). */
export async function apiFetch(path, options = {}) {
  const res = await fetch(path, { credentials: 'include', ...options });
  if (res.status === 401) {
    loginRedirect();
    throw new Error('Sesión expirada');
  }
  return res;
}

export async function apiJson(path, options = {}) {
  const res = await apiFetch(path, options);
  return res.json().catch(() => ({}));
}

let mePromise = null;

/** Usuario de la sesión ({id, email, nombre, rol}); se pide una vez por carga de página. */
export function fetchMe() {
  if (!mePromise) {
    mePromise = apiJson('/auth/me').catch((e) => {
      mePromise = null;
      throw e;
    });
  }
  return mePromise;
}

export async function logout() {
  await fetch('/auth/logout', { method: 'POST', credentials: 'include' }).catch(() => {});
  mePromise = null;
  window.location.href = '/front/login';
}

/** POST/PATCH/DELETE con JSON; devuelve {ok, status, data}. */
export async function send(path, method, body) {
  const res = await apiFetch(path, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = res.status === 204 ? {} : await res.json().catch(() => ({}));
  return { ok: res.ok, status: res.status, data };
}
