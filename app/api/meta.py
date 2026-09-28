from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.deps import require_user
from app.core.config import Settings, get_settings

router = APIRouter(tags=["meta"], prefix="/app")


class UiMetaResponse(BaseModel):
    jobs_db_path: str
    gpu_idle_threshold: int


@router.get("/ui-meta", response_model=UiMetaResponse)
async def ui_meta(
    user: Annotated[str, Depends(require_user)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> UiMetaResponse:
    """Valores de configuración que el SPA muestra en pantalla (antes interpolados por Jinja)."""
    return UiMetaResponse(jobs_db_path=settings.jobs_db_path, gpu_idle_threshold=settings.gpu_idle_threshold)
