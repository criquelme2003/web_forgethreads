from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.api import login
from app.core.config import get_settings

app = FastAPI(title="Login mínimo")

app.add_middleware(
    SessionMiddleware,
    secret_key=get_settings().session_secret_key,
    session_cookie="session",
    https_only=False,  # dev; en prod poner True
    same_site="lax",
)

app.include_router(login.router)
app.mount("/front", StaticFiles(directory="app/static", html=True), name="front")
