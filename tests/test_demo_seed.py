import asyncio

from app.services.demo_seed import (
    DEMO_CAUSES,
    DEMO_EXPERT_EMAIL,
    DEMO_EXPERT_PROJECT,
    DEMO_SIMULATION_PROJECT,
    seed_demo,
)
from app.services.project_store import ProjectStore


def test_seed_creates_demo_projects_once(database: str) -> None:
    store = ProjectStore(database)

    created = asyncio.run(seed_demo(store, owner_id=None))
    assert created == [DEMO_EXPERT_PROJECT, DEMO_SIMULATION_PROJECT]
    # Idempotente: una segunda vez no crea nada.
    assert asyncio.run(seed_demo(store, owner_id=None)) == []

    projects = {p.nombre: p for p in asyncio.run(store.list_projects())}
    expert, sim = projects[DEMO_EXPERT_PROJECT], projects[DEMO_SIMULATION_PROJECT]
    assert expert.tipo == "expertos" and expert.estado == "abierto" and expert.causas == DEMO_CAUSES
    assert sim.tipo == "simulacion" and sim.estado == "abierto"

    inputs = asyncio.run(store.list_inputs(expert.id))
    assert len(inputs) == 1
    item = inputs[0]
    assert len(item.cc) == 4 and len(item.ce[0]) == 3 and item.ee[2][2] == 1.0
    assert abs(item.ce[0][0] - 0.9) < 1e-6  # Precio → Ventas
    invitation = asyncio.run(store.list_invitations(expert.id))[0]
    assert invitation.email == DEMO_EXPERT_EMAIL and invitation.state() == "usada"


def test_seed_only_adds_missing(database: str) -> None:
    store = ProjectStore(database)
    asyncio.run(store.create_project(nombre=DEMO_SIMULATION_PROJECT, descripcion="", tipo="simulacion", owner_id=None))
    assert asyncio.run(seed_demo(store)) == [DEMO_EXPERT_PROJECT]
