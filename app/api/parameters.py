from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse

from app.api.deps import require_user

router = APIRouter(tags=["parameters"])

_FORM_HTML = """<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Parámetros</title>
</head>
<body>
  <h1>Parámetros</h1>
  <form id="parameters-form">
    <label>Nodos totales
      <input type="number" name="nodos_totales" required />
    </label><br />
    <label>C
      <input type="number" name="c" step="any" required />
    </label><br />
    <label>Threshold
      <input type="number" name="threshold" step="any" required />
    </label><br />
    <button type="submit">Enviar</button>
  </form>
</body>
</html>
"""


@router.get("/parameters", response_class=HTMLResponse)
def parameters_form(user: Annotated[str, Depends(require_user)]) -> str:
    return _FORM_HTML
