"""Proyectos por defecto: los trabajos de referencia del grupo.

Crea, si no existen (se reconocen por el nombre):
- «forgethreads · Aceleración CUDA»: proyecto de simulación abierto para replicar los MaxMin y los
  barridos de N y c del trabajo de forgethreads (feCUDA/comprobacion_empirica).
- «Tesis Claudio Araya · Valdivia»: proyecto de expertos cerrado con los 10 informantes clave del
  estudio de contaminación atmosférica de Valdivia que trae forgeffects (`load_test_data`), con
  16 incentivos (a1…a16) y 4 conductas (b1…b4), los mismos nombres que usa forgeffects.

A diferencia de los datos de muestra, se crean siempre al iniciar (SEED_DEFAULT_PROJECTS=true por
defecto) y quedan a nombre del superadmin. Si se borra uno, vuelve a crearse al reiniciar.
También se puede correr a mano: `uv run python -m app.services.default_projects`.
"""

import asyncio
import json
import logging
from datetime import timedelta
from pathlib import Path

from app.services.fe_input import load_fe_input
from app.services.project_store import ProjectStore

logger = logging.getLogger(__name__)

FORGETHREADS_PROJECT = "forgethreads · Aceleración CUDA"
THESIS_PROJECT = "Tesis Claudio Araya · Valdivia"

FORGETHREADS_DESCRIPTION = (
    "Aceleración de búsqueda de efectos olvidados en grafos utilizando CUDA (Carlos Riquelme Ávila, "
    "Universidad Católica de Temuco). forgethreads paraleliza en GPU la convolución max-min sobre las "
    "celdas de la matriz de adyacencia y logra entre 5,7× y 17× frente a forgeffects, con un límite "
    "práctico de 2000 nodos. Este proyecto replica la comprobación empírica: MaxMin sobre redes "
    "aleatorias y barridos de N y c para medir el orden efectivo."
)
THESIS_DESCRIPTION = (
    "Tesis de Claudio Araya Toro (Universidad Católica de Temuco), autor de forgeffects con Julio Rojas "
    "Mora. Datos del estudio «Application of the Forgotten Effects Theory for Assessing the Public Policy "
    "on Air Pollution of the Commune of Valdivia, Chile» (Manna, Rojas-Mora y Mondaca-Marino, 2018): "
    "10 informantes clave evaluaron 16 incentivos (a1…a16) y 4 conductas (b1…b4). Son las matrices que "
    "trae forgeffects (load_test_data); el cálculo agregado reproduce el ejemplo de su README."
)

THESIS_DATA = Path(__file__).parent / "default_data" / "valdivia.json"
THESIS_CAUSES = [f"a{i}" for i in range(1, 17)]
THESIS_EFFECTS = [f"b{i}" for i in range(1, 5)]


def thesis_inputs() -> list[dict[str, list[list[float]]]]:
    """Matrices CC, CE y EE de cada informante, en el orden del dataset."""
    return json.loads(THESIS_DATA.read_text(encoding="utf-8"))["informantes"]


def _csv(labels_rows: list[str], labels_cols: list[str], matrix: list[list[float]]) -> str:
    rows = ["," + ",".join(labels_cols)]
    rows += [f"{label}," + ",".join(str(v) for v in row) for label, row in zip(labels_rows, matrix)]
    return "\n".join(rows) + "\n"


async def seed_default_projects(projects: ProjectStore, owner_id: int | None = None) -> list[str]:
    """Crea los proyectos por defecto que falten. Devuelve los nombres de los que creó."""
    existing = {p.nombre for p in await projects.list_projects()}
    created: list[str] = []

    if FORGETHREADS_PROJECT not in existing:
        await projects.create_project(
            nombre=FORGETHREADS_PROJECT,
            descripcion=FORGETHREADS_DESCRIPTION,
            tipo="simulacion",
            owner_id=owner_id,
        )
        created.append(FORGETHREADS_PROJECT)

    if THESIS_PROJECT not in existing:
        # Se validan todas las matrices antes de crear el proyecto, para no dejarlo a medias.
        parsed = []
        for i, item in enumerate(thesis_inputs(), start=1):
            data = load_fe_input(
                _csv(THESIS_CAUSES, THESIS_CAUSES, item["cc"]),
                _csv(THESIS_CAUSES, THESIS_EFFECTS, item["ce"]),
                _csv(THESIS_EFFECTS, THESIS_EFFECTS, item["ee"]),
                causes=THESIS_CAUSES,
                effects=THESIS_EFFECTS,
            )
            if not data.valid:  # no debería pasar: son matrices fijas
                raise ValueError(f"Matrices del informante {i} inválidas: {[e.message for e in data.errors]}")
            parsed.append(data)
        project = await projects.create_project(
            nombre=THESIS_PROJECT,
            descripcion=THESIS_DESCRIPTION,
            tipo="expertos",
            owner_id=owner_id,
            causas=THESIS_CAUSES,
            efectos=THESIS_EFFECTS,
        )
        await projects.update_project(project.id, estado="abierto")
        for i, data in enumerate(parsed, start=1):
            _, token = await projects.create_invitation(
                project.id, f"informante{i}@valdivia.example", timedelta(days=1), nombre=f"Informante {i}"
            )
            await projects.submit_input(token, data.cc.tolist(), data.ce.tolist(), data.ee.tolist())
        # Los datos del estudio están completos: no se esperan más respuestas.
        await projects.update_project(project.id, estado="cerrado")
        created.append(THESIS_PROJECT)

    if created:
        logger.info("Proyectos por defecto creados: %s", ", ".join(created))
    return created


def main() -> None:
    from app.core.config import get_settings
    from app.services.user_store import UserStore

    settings = get_settings()

    async def run() -> list[str]:
        database = settings.effective_database_url
        owner = await UserStore(database).ensure_superadmin(settings.auth_username, settings.auth_password)
        return await seed_default_projects(ProjectStore(database), owner.id)

    created = asyncio.run(run())
    print(f"Creados: {', '.join(created)}" if created else "Los proyectos por defecto ya existían.")


if __name__ == "__main__":
    main()
