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
