"""Datos de muestra para probar la app sin crearlos a mano.

Crea, si no existen (se reconocen por el nombre):
- «Demo · Expertos»: proyecto de expertos abierto, con las etiquetas de docs/ejemplos_fe y un
  input ya ingresado por un experto de muestra (su invitación queda usada). Los cálculos se
  lanzan desde la app, como con cualquier input.
- «Demo · Simulación»: proyecto de simulación abierto, sin cálculos.

Se puede correr a mano (`uv run python -m app.services.demo_seed`) o al iniciar la app con
SEED_DEMO_DATA=true; en ambos casos es idempotente.
"""

import asyncio
import logging
from datetime import timedelta

from app.services.fe_input import load_fe_input
from app.services.project_store import ProjectStore

logger = logging.getLogger(__name__)

DEMO_EXPERT_PROJECT = "Demo · Expertos"
DEMO_SIMULATION_PROJECT = "Demo · Simulación"
DEMO_EXPERT_EMAIL = "experto.demo@example.com"

# Mismas matrices que docs/ejemplos_fe (la imagen Docker solo copia app/).
DEMO_CAUSES = ["Precio", "Publicidad", "Capacitación", "Tecnología"]
DEMO_EFFECTS = ["Ventas", "Costos", "Clima laboral"]
DEMO_CC = """,Precio,Publicidad,Capacitación,Tecnología
Precio,1,0.3,0.1,0.2
Publicidad,0.6,1,0.2,0.3
Capacitación,0.2,0.4,1,0.7
Tecnología,0.5,0.6,0.3,1
"""
DEMO_CE = """,Ventas,Costos,Clima laboral
Precio,0.9,0.4,0.1
Publicidad,0.8,0.6,0.2
Capacitación,0.3,0.5,0.8
Tecnología,0.6,0.7,0.4
"""
DEMO_EE = """,Ventas,Costos,Clima laboral
Ventas,1,0.6,0.5
Costos,0.4,1,0.3
Clima laboral,0.7,0.2,1
"""


async def seed_demo(projects: ProjectStore, owner: str | None = None) -> list[str]:
    """Crea los proyectos de muestra que falten. Devuelve los nombres de los que creó."""
    existing = {p.nombre for p in await projects.list_projects()}
    created: list[str] = []

    if DEMO_EXPERT_PROJECT not in existing:
        data = load_fe_input(DEMO_CC, DEMO_CE, DEMO_EE, causes=DEMO_CAUSES, effects=DEMO_EFFECTS)
        if not data.valid:  # no debería pasar: son matrices fijas
            raise ValueError(f"Matrices de muestra inválidas: {[e.message for e in data.errors]}")
        project = await projects.create_project(
            nombre=DEMO_EXPERT_PROJECT,
            descripcion="Proyecto de muestra con un input ya ingresado (matrices de docs/ejemplos_fe).",
            tipo="expertos",
            owner=owner,
            causas=DEMO_CAUSES,
            efectos=DEMO_EFFECTS,
        )
        await projects.update_project(project.id, estado="abierto")
        _, token = await projects.create_invitation(project.id, DEMO_EXPERT_EMAIL, timedelta(days=14))
        await projects.submit_input(token, data.cc.tolist(), data.ce.tolist(), data.ee.tolist())
        created.append(DEMO_EXPERT_PROJECT)

    if DEMO_SIMULATION_PROJECT not in existing:
        await projects.create_project(
            nombre=DEMO_SIMULATION_PROJECT,
            descripcion="Proyecto de muestra para lanzar cálculos MaxMin.",
            tipo="simulacion",
            owner=owner,
        )
        created.append(DEMO_SIMULATION_PROJECT)

    if created:
        logger.info("Datos de muestra creados: %s", ", ".join(created))
    return created


def main() -> None:
    from app.core.config import get_settings

    settings = get_settings()
    created = asyncio.run(seed_demo(ProjectStore(settings.jobs_db_path), settings.auth_username))
    print(f"Creados: {', '.join(created)}" if created else "Los proyectos de muestra ya existían.")


if __name__ == "__main__":
    main()
