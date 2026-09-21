import secrets
import time
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
    request.session["last_seen"] = time.time()
    return LoginResponse(success=True)


@router.post("/logout", response_model=LoginResponse)
def logout(request: Request) -> LoginResponse:
    request.session.clear()
    return LoginResponse(success=True)
