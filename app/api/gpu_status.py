from typing import Annotated

from fastapi import APIRouter, Depends,Request
from fastapi.responses import HTMLResponse
from asyncssh import SSHClientConnection
from app.api.deps import require_user

router = APIRouter(tags=["parameters"],prefix="/front")

def generate_page(input:str) -> str: 
  return f"""<!DOCTYPE html>
  <html lang="es">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Parámetros</title>
  </head>
  <body>
    <h1>NVIDIA-SMI Result</h1>
    <pre>{input}</pre>
  </body>
  </html>
  """


@router.get("/gpu_status", response_class=HTMLResponse)
async def parameters_form(user: Annotated[str, Depends(require_user)],req:Request) -> str:
    ssh_conn:SSHClientConnection = req.state.ssh_conn
    
    result = await ssh_conn.run("nvidia-smi")
    
    return generate_page(str(result.stdout))
