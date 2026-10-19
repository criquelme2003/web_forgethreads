"""Enlaces públicos al SPA que se envían por correo o se muestran para copiar."""

from fastapi import Request

from app.core.config import Settings


def front_link(settings: Settings, request: Request, path: str) -> str:
    """URL absoluta de `/front/<path>`, con la base pública si está configurada."""
    base = (settings.public_callback_base_url or str(request.base_url)).rstrip("/")
    return f"{base}/front/{path.lstrip('/')}"
