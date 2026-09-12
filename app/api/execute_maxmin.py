from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field, ValidationError

from app.api.deps import require_user

router = APIRouter(tags=["execute"])


class ExecuteMaxMinRequest(BaseModel):
    numero_nodos: int = Field(..., gt=0, le=100000, description="Número de nodos", alias="numero_nodos")
    threshold: float = Field(..., ge=0, le=1_000_000, description="Threshold")
    conectividad_promedio: int = Field(..., ge=0, le=100000, description="Conectividad promedio")
    seed: int = Field(..., description="Seed")

    model_config = {"populate_by_name": True, "extra": "ignore"}


class ExecuteMaxMinResponse(BaseModel):
    message: str = "Tarea ingresada correctamente"
    data: ExecuteMaxMinRequest


# Soporta alias comunes del front (numero_nodos, nodos_totales, etc)
def _normalize_payload(payload: dict) -> dict:
    mapping = {
        "numero_nodos": "numero_nodos",
        "nodos_totales": "numero_nodos",
        "numeroDeNodos": "numero_nodos",
        "num_nodos": "numero_nodos",
        "conectividad_promedio": "conectividad_promedio",
        "conectividad": "conectividad_promedio",
        "c": "conectividad_promedio",
        "threshold": "threshold",
        "seed": "seed",
    }
    normalized = {}
    for k, v in payload.items():
        key = mapping.get(k, k)
        # keep last wins
        normalized[key] = v
    return normalized


@router.post("/app/execute_maxmin", response_model=ExecuteMaxMinResponse)
@router.post("/execute_maxmin", response_model=ExecuteMaxMinResponse, include_in_schema=False)
async def execute_maxmin(
    payload: dict,
    user: Annotated[str, Depends(require_user)],
):
    """
    Endpoint básico para ingresar parámetros MaxMin.
    Ruta requerida: app/execute_maxmin
    Parámetros: numero_nodos (int), threshold (float), conectividad_promedio (int), seed (int)
    Retorna: {"message": "Tarea ingresada correctamente"}
    """
    # Normaliza alias y valida con pydantic
    normalized = _normalize_payload(payload)
    try:
        data = ExecuteMaxMinRequest.model_validate(normalized)
    except ValidationError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=e.errors())
    # Aquí iría encolado real (SLURM/cola). Ahora solo validación + ack.
    return ExecuteMaxMinResponse(message="Tarea ingresada correctamente", data=data)


# Front opcional JSON? El HTML se sirve desde /front/execute_maxmin
# Para que POST desde form html funcione con application/json, el front usará fetch.
