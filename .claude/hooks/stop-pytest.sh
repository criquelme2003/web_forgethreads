#!/usr/bin/env bash
# Stop: si hay .py modificados sin commitear, corre pytest. Si falla, bloquea el
# cierre del turno (exit 2) y le pasa el resumen a Claude para que lo corrija.
input=$(cat)
[[ $(jq -r '.stop_hook_active // false' <<<"$input") == true ]] && exit 0
cd "$CLAUDE_PROJECT_DIR" || exit 0
git status --porcelain -- '*.py' | grep -q . || exit 0

# Mismas credenciales dummy que el CI (.github/workflows/test-login.yml)
export AUTH_USERNAME="${AUTH_USERNAME:-ci-admin}"
export AUTH_PASSWORD="${AUTH_PASSWORD:-ci-password}"
export SESSION_SECRET_KEY="${SESSION_SECRET_KEY:-ci-session-secret-key-0123456789}"

args=(-q -x --no-header -p no:cacheprovider)
# Sin build del front (app/front/dist) este test siempre falla; se omite localmente
[[ -d app/front/dist ]] || args+=(--deselect tests/test_login.py::test_front_login_served)

out=$(uv run --quiet pytest "${args[@]}" 2>&1) && exit 0
{ echo "pytest falla con los cambios actuales:"; tail -n 40 <<<"$out"; } >&2
exit 2
