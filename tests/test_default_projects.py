import asyncio

from app.services.default_projects import (
    FORGETHREADS_PROJECT,
    THESIS_CAUSES,
    THESIS_EFFECTS,
    THESIS_PROJECT,
    seed_default_projects,
    thesis_inputs,
)
from app.services.project_store import ProjectStore


def test_seed_creates_default_projects_once(database: str) -> None:
    store = ProjectStore(database)

    assert asyncio.run(seed_default_projects(store, owner_id=None)) == [FORGETHREADS_PROJECT, THESIS_PROJECT]
    assert asyncio.run(seed_default_projects(store, owner_id=None)) == []

    projects = {p.nombre: p for p in asyncio.run(store.list_projects())}
    sim, thesis = projects[FORGETHREADS_PROJECT], projects[THESIS_PROJECT]
    assert sim.tipo == "simulacion" and sim.estado == "abierto"
    assert thesis.tipo == "expertos" and thesis.estado == "cerrado"
    assert thesis.causas == THESIS_CAUSES and thesis.efectos == THESIS_EFFECTS

    inputs = asyncio.run(store.list_inputs(thesis.id))
    assert len(inputs) == 10
    raw = thesis_inputs()
    by_order = sorted(inputs, key=lambda i: i.id)
    for item, expected in zip(by_order, raw):
        assert len(item.cc) == 16 and len(item.ce[0]) == 4 and len(item.ee) == 4
        assert abs(item.ce[3][2] - expected["ce"][3][2]) < 1e-6
    invitations = asyncio.run(store.list_invitations(thesis.id))
    assert {i.state() for i in invitations} == {"usada"}
    assert {i.nombre for i in invitations} == {f"Informante {n}" for n in range(1, 11)}


def test_seed_only_adds_missing_default_projects(database: str) -> None:
    store = ProjectStore(database)
    asyncio.run(store.create_project(nombre=THESIS_PROJECT, descripcion="", tipo="expertos", owner_id=None))
    assert asyncio.run(seed_default_projects(store)) == [FORGETHREADS_PROJECT]
