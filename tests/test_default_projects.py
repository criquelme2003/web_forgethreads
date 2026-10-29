import asyncio

from app.core.config import Settings
from app.services.default_projects import (
    DEFAULT_POSTS,
    FORGETHREADS_PROJECT,
    THESIS_CAUSES,
    THESIS_EFFECTS,
    THESIS_PROJECT,
    seed_default_posts,
    seed_default_projects,
    thesis_inputs,
)
from app.services.job_store import JobStore
from app.services.post_store import PostStore
from app.services.project_store import ProjectStore
from app.services.user_store import UserStore


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


def test_seed_publishes_default_posts_waiting_for_calculations(database: str, tmp_path) -> None:
    projects, posts = ProjectStore(database), PostStore(database)
    owner = asyncio.run(UserStore(database).ensure_superadmin("admin@example.com", "clave-larga-123"))
    settings = Settings(
        auth_username="admin@example.com", auth_password="clave-larga-123",
        session_secret_key="test-secret-key-0123456789", fe_results_dir=str(tmp_path / "fe"),
    )
    asyncio.run(seed_default_projects(projects, owner.id))

    def seed():
        return asyncio.run(seed_default_posts(projects, posts, JobStore(database), owner, settings))

    assert seed() == [p["slug"] for p in DEFAULT_POSTS]
    assert seed() == []  # idempotente
    by_project = {p.id: p.nombre for p in asyncio.run(projects.list_projects())}
    for spec in DEFAULT_POSTS:
        post = asyncio.run(posts.get_published(spec["slug"]))
        assert by_project[post.project_id] == spec["project"]
        figures = post.publicado["figuras"]
        # Los proyectos no traen cálculos: todas las figuras citadas quedan «en preparación».
        assert [f["ref"] for f in figures] == [f["ref"] for f in spec["figuras"]]
        assert all(f["pendiente"] for f in figures)
