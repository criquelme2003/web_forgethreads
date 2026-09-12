from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.api import execute_maxmin, login, parameters,gpu_status
from app.core.config import get_settings
from app.lifespan import global_lifespan

app = FastAPI(title="Login mínimo",lifespan=global_lifespan)

app.add_middleware(
    SessionMiddleware,
    secret_key=get_settings().session_secret_key,
    session_cookie="session",
    https_only=False,  # dev; en prod poner True
    same_site="lax",
)

app.include_router(login.router)
app.include_router(parameters.router)
app.include_router(gpu_status.router)
app.include_router(execute_maxmin.router)

app.mount("/front", StaticFiles(directory="app/static", html=True), name="front")
