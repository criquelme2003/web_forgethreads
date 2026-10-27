import asyncio

from app.core.config import Settings
from app.services.demo_seed import (
    DEMO_CAUSES,
    DEMO_EXPERT_EMAIL,
    DEMO_EXPERT_PROJECT,
    DEMO_POSTS,
    DEMO_SIMULATION_PROJECT,
    seed_demo,
    seed_demo_posts,
)
from app.services.job_store import JobStore
from app.services.post_store import PostStore
from app.services.project_store import ProjectStore
from app.services.user_store import UserStore


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


def test_seed_publishes_demo_posts_waiting_for_calculations(database: str, tmp_path) -> None:
    projects, posts = ProjectStore(database), PostStore(database)
    owner = asyncio.run(UserStore(database).ensure_superadmin("admin@example.com", "clave-larga-123"))
    settings = Settings(
        auth_username="admin@example.com", auth_password="clave-larga-123",
        session_secret_key="test-secret-key-0123456789", fe_results_dir=str(tmp_path / "fe"),
    )
    asyncio.run(seed_demo(projects, owner.id))

    def seed():
        return asyncio.run(seed_demo_posts(projects, posts, JobStore(database), owner, settings))

    assert seed() == [p["slug"] for p in DEMO_POSTS]
    assert seed() == []  # idempotente
    for spec in DEMO_POSTS:
        post = asyncio.run(posts.get_published(spec["slug"]))
        figures = post.publicado["figuras"]
        # Sin cálculos todavía: las figuras citadas se publican «en preparación».
        assert figures and all(f["pendiente"] for f in figures)
    expert = asyncio.run(posts.get_published("demo-efectos-olvidados"))
    assert [f["ref"] for f in expert.publicado["figuras"]] == ["f1", "f2"]  # f3 no se cita
