from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.services import job_service

router = APIRouter(prefix="/app", tags=["jobs"])


class ResultPayload(BaseModel):
    max_depth: int | float = Field(..., description="Profundidad máxima encontrada")


@router.post("/jobs/{job_id}/result")
async def post_result(
    job_id: str,
    body: ResultPayload,
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
    x_job_token: Annotated[str | None, Header(alias="X-Job-Token")] = None,
):
    """
    Recibe el cálculo una vez terminado. Debe verificar token enviado por el notifier.
    - job_id: SLURM jobId del cálculo
    - body: {max_depth: int|float}
    - auth: Authorization: Bearer <token>  o  X-Job-Token: <token>
    """
    # Extrae token de Authorization Bearer o X-Job-Token
    token = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    elif x_job_token:
        token = x_job_token.strip()
    elif authorization:
        token = authorization.strip()

    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing notifier token")

    if not job_service.verify_token(job_id, token):
        # log sin exponer token completo
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid notifier token")

    job_service.set_result(job_id, body.max_depth)
    return {"message": "Resultado registrado", "job_id": job_id, "max_depth": body.max_depth}


@router.get("/jobs/{job_id}")
async def get_job(job_id: str):
    rec = job_service.get_job(job_id)
    if not rec:
        raise HTTPException(status_code=404, detail="job not found")
    # no exponer token
    safe = {k: v for k, v in rec.items() if k != "token"}
    return {"job_id": job_id, **safe}


@router.get("/jobs")
async def list_jobs():
    # lista sin tokens
    all_jobs = job_service.list_jobs()
    safe = {jid: {k: v for k, v in rec.items() if k != "token"} for jid, rec in all_jobs.items()}
    return safe
