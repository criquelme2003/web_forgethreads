# Spec: Login mínimo con FastAPI

## Objective

Pequeña aplicación FastAPI en `app/` que permite autenticarse con una única
credencial de administrador definida en un archivo `.env`. Al iniciar sesión
correctamente se emite una **cookie de sesión HttpOnly**. Además, se sirve el
directorio estático `app/static/` bajo `/front`, de modo que el formulario de
login queda accesible en `/front/login`.

Alcance estrictamente limitado a: la ruta `POST /auth/login`, la lectura de
credenciales desde `.env`, la emisión de la cookie de sesión y el servido del
archivo estático. **Nada más** (sin registro, sin base de datos de usuarios, sin
recuperación de contraseña, sin logout salvo lo indicado abajo, sin refresh
tokens, sin rate limiting).

### Usuario
Un administrador único cuyas credenciales viven en `.env`.

### Éxito
- `GET /front/login` devuelve el HTML del formulario de login.
- `POST /auth/login` con las credenciales correctas → `200` + `Set-Cookie`
  con una cookie de sesión `HttpOnly`.
- `POST /auth/login` con credenciales incorrectas o ausentes → `401`, sin cookie.
- Las credenciales nunca están hardcodeadas; se leen de `.env` vía
  `pydantic-settings`.

## Tech Stack

- Python 3.12+ (probado con 3.14)
- FastAPI
- Uvicorn (servidor ASGI)
- pydantic-settings (lectura de `.env`)
- itsdangerous (firma de la cookie de sesión, vía `SessionMiddleware` de Starlette)
- pytest + httpx (tests, mediante `fastapi.testclient.TestClient`)
- Gestión de dependencias: **uv** + `pyproject.toml`

## Commands

```
Instalar deps:   uv sync
Dev server:      uv run uvicorn app.main:app --reload
Tests:           uv run pytest
Tests + cover:   uv run pytest --cov=app --cov-report=term-missing
Lint:            uv run ruff check app tests
Format:          uv run ruff format app tests
```

## Project Structure

Sigue (de forma mínima) las convenciones del artículo de Auth0
"FastAPI Best Practices": routers en `app/api/`, configuración en `app/core/`.

```
app/
├── __init__.py
├── main.py            → Crea la app FastAPI, añade SessionMiddleware,
│                         monta StaticFiles en /front, incluye el router de login
├── api/
│   ├── __init__.py
│   ├── deps.py        → require_user: dependencia que exige sesión válida (401 si no)
│   ├── login.py       → APIRouter con prefijo /auth; define POST /auth/login
│   └── parameters.py  → GET /parameters (protegida): formulario HTML con los
│                         inputs Nodos totales, C y Threshold
├── core/
│   ├── __init__.py
│   └── config.py      → Settings (pydantic-settings): AUTH_USERNAME,
│                         AUTH_PASSWORD, SESSION_SECRET_KEY
└── static/
    └── login/
        └── index.html → Formulario HTML que hace fetch POST a /auth/login.
                          StaticFiles(html=True) lo sirve en GET /front/login

tests/
├── __init__.py
└── test_login.py      → Casos de /auth/login y de /front/login

.env                   → Credenciales reales (git-ignored)
.env.example           → Plantilla commiteada
pyproject.toml         → Metadatos y dependencias (uv)
```

## Code Style

- Ruff para lint y formato (línea 100).
- Type hints en todas las firmas públicas.
- Nombres de variables y funciones en `snake_case`; clases en `PascalCase`.
- Comparación de contraseña en tiempo constante con `secrets.compare_digest`.
- El router no accede a `Settings` como global: se inyecta con `Depends`.

```python
# app/api/login.py
import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from app.core.config import Settings, get_settings

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    success: bool


@router.post("/login", response_model=LoginResponse)
def login(
    body: LoginRequest,
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> LoginResponse:
    user_ok = secrets.compare_digest(body.username, settings.auth_username)
    pass_ok = secrets.compare_digest(body.password, settings.auth_password)
    if not (user_ok and pass_ok):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )
    request.session["user"] = body.username
    return LoginResponse(success=True)
```

```python
# app/core/config.py
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    auth_username: str
    auth_password: str
    session_secret_key: str


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
```

La cookie de sesión la gestiona `SessionMiddleware` de Starlette (HttpOnly por
defecto, firmada con `session_secret_key`):

```python
# app/main.py (extracto)
app.add_middleware(
    SessionMiddleware,
    secret_key=get_settings().session_secret_key,
    session_cookie="session",
    https_only=False,   # dev; en prod poner True
    same_site="lax",
)
app.mount("/front", StaticFiles(directory="app/static", html=True), name="front")
app.include_router(login.router)
```

## Testing Strategy

- Framework: `pytest` con `fastapi.testclient.TestClient`.
- Ubicación: `tests/`, un archivo `test_login.py`.
- Las credenciales de test se inyectan sobreescribiendo `get_settings` con
  `app.dependency_overrides` (y un `session_secret_key` fijo de test).
- Cobertura objetivo: 100% de `app/api/login.py` y `app/core/config.py`.
- Casos mínimos:
  1. `POST /auth/login` con credenciales correctas → `200`, JSON `{"success": true}`,
     respuesta incluye header `set-cookie` con `session` y atributo `HttpOnly`.
  2. Credenciales incorrectas → `401`, sin `set-cookie`.
  3. Body incompleto (falta `password`) → `422`.
  4. `GET /front/login` → `200` y `content-type` HTML.

## Boundaries

- **Always**:
  - Leer credenciales y secreto de sesión desde `.env` / entorno.
  - `.env` en `.gitignore`; mantener `.env.example` actualizado.
  - Comparar contraseña con `secrets.compare_digest`.
  - Ejecutar `uv run pytest` y `uv run ruff check` antes de commitear.
- **Ask first**:
  - Añadir cualquier dependencia fuera de las listadas en Tech Stack.
  - Añadir endpoints nuevos (p. ej. `/auth/logout`, `/auth/me`) o dependencias
    de "usuario autenticado".
  - Cambiar el mecanismo de cookie (p. ej. pasar a JWT).
- **Never**:
  - Commitear el `.env` real ni credenciales.
  - Persistir usuarios en base de datos o añadir un sistema de registro.
  - Implementar features no pedidas (roles, OAuth, refresh, rate limiting).

## Success Criteria

- [ ] `uv sync` instala el proyecto sin errores.
- [ ] `uv run uvicorn app.main:app` arranca la app.
- [ ] `GET /front/login` sirve `app/static/login.html`.
- [ ] `POST /auth/login` con credenciales de `.env` → `200` + cookie `session` HttpOnly.
- [ ] `POST /auth/login` con credenciales inválidas → `401` sin cookie.
- [ ] `uv run pytest` pasa con los 4 casos y cobertura 100% en los módulos objetivo.
- [ ] No hay credenciales en el código ni en git; `.env.example` está commiteado.

## Open Questions

- ¿Se desea un endpoint `/auth/logout` que limpie la cookie? (Actualmente fuera
  de alcance; se añadirá solo si se pide.)
- ¿El `login.html` debe redirigir a alguna página tras el éxito, o basta con
  mostrar un mensaje? (Asumido: mostrar mensaje, sin redirección.)
