from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.api import execute_maxmin, login, parameters, gpu_status, job_callback, meta
from app.core.config import get_settings
from app.lifespan import global_lifespan
import logging;
logging.basicConfig(level=logging.DEBUG)
logging.getLogger("ssh_pool").setLevel(logging.DEBUG)

app = FastAPI(title="Login mínimo",lifespan=global_lifespan)

app.add_middleware(
    SessionMiddleware,
    secret_key=get_settings().session_secret_key,
    session_cookie="session",
    max_age=get_settings().session_max_age_seconds,
    https_only=get_settings().session_cookie_secure,  # True solo si environment=prod (requiere HTTPS real)
    same_site="lax",
)

app.include_router(login.router)
app.include_router(parameters.router)
app.include_router(gpu_status.router)
app.include_router(execute_maxmin.router)
app.include_router(job_callback.router)
app.include_router(meta.router)

# --- Frontend SPA (build de Vite en app/front/dist, ver app/front/package.json) ---
FRONT_DIST = Path(__file__).resolve().parent / "front" / "dist"
FRONT_ASSETS = FRONT_DIST / "assets"
if FRONT_ASSETS.is_dir():
    app.mount("/front/assets", StaticFiles(directory=str(FRONT_ASSETS)), name="front-assets")


@app.get("/front", include_in_schema=False, response_model=None)
@app.get("/front/{full_path:path}", include_in_schema=False, response_model=None)
async def serve_spa(full_path: str = ""):
    """Sirve index.html del SPA para cualquier ruta del front (resuelve react-router en cliente)."""
    index = FRONT_DIST / "index.html"
    if not index.is_file():
        return JSONResponse(
            {"detail": "Frontend no construido: falta app/front/dist. Ejecuta 'npm run build' en app/front."},
            status_code=404,
        )
    return FileResponse(str(index), media_type="text/html")
