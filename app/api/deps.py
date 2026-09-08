from fastapi import HTTPException, Request, status


def require_user(request: Request) -> str:
    """Devuelve el usuario de la sesión o lanza 401 si no hay sesión válida."""
    user = request.session.get("user")
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )
    return user
