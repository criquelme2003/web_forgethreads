import secrets
import time
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from app.api.deps import get_user_store, require_user
from app.core.config import Settings, get_settings
from app.services.user_store import User, UserStore, normalize_email

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str  # correo del admin, o AUTH_USERNAME para el superadmin inicial
    password: str


class LoginResponse(BaseModel):
    success: bool


class MeResponse(BaseModel):
    id: int
    email: str
    nombre: str
    rol: str


async def _check_credentials(
    body: LoginRequest, settings: Settings, users: UserStore
) -> User | None:
    """El superadmin se valida contra la configuración (RF-02); los admins, contra su hash."""
    user_ok = secrets.compare_digest(
        normalize_email(body.username).encode(), normalize_email(settings.auth_username).encode()
    )
    if user_ok:
        if not secrets.compare_digest(body.password.encode(), settings.auth_password.encode()):
            return None
        return await users.ensure_superadmin(settings.auth_username, settings.auth_password)
    return await users.authenticate(body.username, body.password)


@router.post("/login", response_model=LoginResponse)
async def login(
    body: LoginRequest,
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    users: Annotated[UserStore, Depends(get_user_store)],
) -> LoginResponse:
    user = await _check_credentials(body, settings, users)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )
    request.session.clear()
    request.session["user_id"] = user.id
    request.session["last_seen"] = time.time()
    return LoginResponse(success=True)


@router.post("/logout", response_model=LoginResponse)
def logout(request: Request) -> LoginResponse:
    request.session.clear()
    return LoginResponse(success=True)


@router.get("/me", response_model=MeResponse)
async def me(user: Annotated[User, Depends(require_user)]) -> MeResponse:
    """Usuario de la sesión, para que el SPA muestre las opciones según el rol."""
    return MeResponse(id=user.id, email=user.email, nombre=user.nombre, rol=user.rol)
