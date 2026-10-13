import asyncio
import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

import app.api.fe as fe_api
import app.api.projects as projects_api
from app.api.deps import get_job_store, get_node_selector, get_project_store, get_ssh_pool
from app.core.config import Settings, get_settings
from app.main import app
from app.services.job_store import JobStore
from app.services.mailer import MailError
from app.services.project_store import MIGRATED_PROJECT_NAME, ProjectStore, hash_token
from tests.conftest import TEST_PASSWORD, TEST_USERNAME
from tests.test_fe_input import CC, CE, EE

EXPERT_PROJECT = {
    "nombre": "Retail",
    "descripcion": "Efectos olvidados en retail",
    "tipo": "expertos",
    "causas": ["a", "b"],
    "efectos": ["e1", "e2", "e3"],
    "thr": 0.5,
    "maxorder": 3,
}


@dataclass
class _Completed:
    exit_status: int
    stdout: str
    stderr: str = ""


@dataclass
class _Node:
    name: str
    is_available: bool = True


class FakePool:
    """sbatch simulado: JOBID incremental; `fail` hace fallar el próximo sbatch del cómputo."""

    def __init__(self) -> None:
        self.node_names = ["cuda1"]
        self.commands: list[str] = []
        self.next_id = 100
        self.fail = False

    async def run_on_node(self, node: str, command: str, timeout: float = 10.0) -> _Completed:
        self.commands.append(command)
        if self.fail and "notifier.sh" not in command:
            self.fail = False
            return _Completed(1, "", "sbatch: error")
        self.next_id += 1
        return _Completed(0, f"Submitted batch job {self.next_id}\n")


class FakeSelector:
    async def select_best(self) -> _Node:
        return _Node("cuda1")

    async def get_all_status(self) -> list[_Node]:
        return [_Node("cuda1")]


def _settings(tmp_path: Path, **overrides) -> Settings:
    return Settings(
        auth_username=TEST_USERNAME,
        auth_password=TEST_PASSWORD,
        session_secret_key="test-secret-key-0123456789",
        public_callback_base_url="http://testserver",
        fe_results_dir=str(tmp_path / "fe_results"),
        **overrides,
    )


@pytest.fixture
def env(tmp_path: Path, monkeypatch):
    pool = FakePool()
    db = str(tmp_path / "jobs.db")
    store = JobStore(db)
    projects = ProjectStore(db)
    uploads: list[dict] = []
    mails: list = []

    async def fake_upload(pool_, node, scripts_wf_dir, request_id, files):
        uploads.append({"node": node, "request_id": request_id, "files": files})
        return f"jobs_inputs/{request_id}"

    async def fake_send(settings, msg):
        mails.append(msg)
        if "falla" in msg["To"]:
            raise MailError("No se pudo enviar el correo: rechazado")

    monkeypatch.setattr(fe_api, "upload_fe_input", fake_upload)
    monkeypatch.setattr(projects_api, "send_mail", fake_send)

    state = {"settings": _settings(tmp_path)}
    app.dependency_overrides[get_settings] = lambda: state["settings"]
    app.dependency_overrides[get_job_store] = lambda: store
    app.dependency_overrides[get_project_store] = lambda: projects
    app.dependency_overrides[get_ssh_pool] = lambda: pool
    app.dependency_overrides[get_node_selector] = lambda: FakeSelector()
    with TestClient(app) as client:
        res = client.post("/auth/login", json={"username": TEST_USERNAME, "password": TEST_PASSWORD})
        assert res.status_code == 200
        yield {
            "client": client, "pool": pool, "store": store, "projects": projects, "db": db,
            "uploads": uploads, "mails": mails, "state": state, "tmp_path": tmp_path,
        }
    app.dependency_overrides.clear()


def _create(client: TestClient, **overrides) -> dict:
    res = client.post("/app/projects", json={**EXPERT_PROJECT, **overrides})
    assert res.status_code == 201, res.text
    return res.json()


def _open_project(client: TestClient) -> dict:
    project = _create(client)
    res = client.post(f"/app/projects/{project['id']}/estado", json={"estado": "abierto"})
    assert res.status_code == 200, res.text
    return res.json()


def _invite(client: TestClient, project_id: int, email: str = "experta@example.com") -> tuple[dict, str]:
    res = client.post(f"/app/projects/{project_id}/invitations", json={"email": email})
    assert res.status_code == 201, res.text
    body = res.json()
    return body, body["link"].rsplit("/", 1)[1]


def _anon(client: TestClient) -> TestClient:
    """Mismo cliente sin cookie de sesión: así entra el experto."""
    client.cookies.clear()
    return client


# --- autenticación ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/app/projects"),
        ("post", "/app/projects"),
        ("get", "/app/projects/1"),
        ("patch", "/app/projects/1"),
        ("delete", "/app/projects/1"),
        ("post", "/app/projects/1/estado"),
        ("get", "/app/projects/1/jobs"),
        ("get", "/app/projects/1/invitations"),
        ("post", "/app/projects/1/invitations"),
        ("post", "/app/projects/1/invitations/1/revoke"),
        ("post", "/app/projects/1/invitations/1/regenerate"),
        ("get", "/app/projects/1/inputs"),
        ("patch", "/app/projects/1/inputs/1"),
        ("post", "/app/projects/1/inputs/1/retry"),
    ],
)
def test_project_routes_require_auth(client: TestClient, method: str, path: str) -> None:
    assert getattr(client, method)(path).status_code == 401


# --- proyectos -------------------------------------------------------------------------------


def test_create_and_list_projects(env) -> None:
    client = env["client"]
    expert = _create(client)
    sim = _create(client, nombre="Barridos", tipo="simulacion", causas=None, efectos=None, thr=None, maxorder=None)

    assert expert["estado"] == "borrador" and expert["transiciones"] == ["abierto"]
    assert expert["owner"] == TEST_USERNAME
    assert sim["estado"] == "abierto" and sim["causas"] == [] and sim["thr"] is None

    listed = client.get("/app/projects").json()
    assert [p["id"] for p in listed] == [sim["id"], expert["id"]]


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"causas": None}, "requiere: causas"),
        ({"causas": ["a", "a"]}, "repetidas: a"),
        ({"efectos": ["a", "e2"]}, "causa y efecto: a"),
        ({"causas": ["a;b"]}, "no puede contener ';'"),
        ({"causas": [" "]}, "vacía"),
        ({"thr": 1.5}, "less than or equal"),
        ({"maxorder": 1}, "greater than or equal"),
        ({"tipo": "otro"}, "expertos"),
    ],
)
def test_create_expert_project_validation(env, overrides: dict, message: str) -> None:
    res = env["client"].post("/app/projects", json={**EXPERT_PROJECT, **overrides})
    assert res.status_code == 422
    assert message in json.dumps(res.json(), ensure_ascii=False)


def test_labels_editable_only_in_draft(env) -> None:
    client = env["client"]
    project = _create(client)
    pid = project["id"]

    res = client.patch(f"/app/projects/{pid}", json={"causas": ["c1", "c2"], "thr": 0.3})
    assert res.status_code == 200
    assert res.json()["causas"] == ["c1", "c2"] and res.json()["thr"] == 0.3
    assert client.patch(f"/app/projects/{pid}", json={"efectos": ["c1"]}).status_code == 422

    client.post(f"/app/projects/{pid}/estado", json={"estado": "abierto"})
    assert client.patch(f"/app/projects/{pid}", json={"maxorder": 4}).status_code == 409
    res = client.patch(f"/app/projects/{pid}", json={"nombre": "Nuevo", "descripcion": "otra"})
    assert res.status_code == 200 and res.json()["nombre"] == "Nuevo"


def test_state_transitions(env) -> None:
    client = env["client"]
    pid = _create(client)["id"]

    def move(estado: str) -> int:
        return client.post(f"/app/projects/{pid}/estado", json={"estado": estado}).status_code

    assert move("cerrado") == 409
    assert move("abierto") == 200
    assert move("borrador") == 409
    assert move("cerrado") == 200
    assert move("abierto") == 200


def test_delete_only_empty_projects(env) -> None:
    client = env["client"]
    pid = _create(client)["id"]
    _invite(client, pid)
    assert client.delete(f"/app/projects/{pid}").status_code == 204
    assert client.get(f"/app/projects/{pid}").status_code == 404

    sim = _create(client, tipo="simulacion")
    asyncio.run(env["store"].create("77", node="cuda1", project_id=sim["id"]))
    assert client.delete(f"/app/projects/{sim['id']}").status_code == 409


def test_unknown_project_is_404(env) -> None:
    assert env["client"].get("/app/projects/999").status_code == 404
    assert env["client"].get("/app/projects/999/invitations").status_code == 404


# --- invitaciones ----------------------------------------------------------------------------


def test_invitation_stores_only_token_hash(env) -> None:
    client = env["client"]
    pid = _create(client)["id"]
    body, token = _invite(client, pid, "Experta@Example.com")

    assert body["link"] == f"http://testserver/front/invite/{token}"
    assert body["estado"] == "pendiente" and body["envio"] == "manual"
    assert body["email"] == "experta@example.com"
    assert env["mails"] == []

    conn = sqlite3.connect(env["db"])
    stored = conn.execute("SELECT token_hash FROM expert_invitations").fetchone()[0]
    conn.close()
    assert stored == hash_token(token) and token not in stored

    listed = client.get(f"/app/projects/{pid}/invitations").json()
    assert "link" not in listed[0] and listed[0]["estado"] == "pendiente"


def test_invitations_not_for_simulation_or_closed(env) -> None:
    client = env["client"]
    sim = _create(client, tipo="simulacion")
    assert client.post(f"/app/projects/{sim['id']}/invitations", json={"email": "a@b.cl"}).status_code == 400

    pid = _open_project(client)["id"]
    client.post(f"/app/projects/{pid}/estado", json={"estado": "cerrado"})
    assert client.post(f"/app/projects/{pid}/invitations", json={"email": "a@b.cl"}).status_code == 409
    assert client.post(f"/app/projects/{pid}/invitations", json={"email": "no-es-correo"}).status_code == 422


def test_smtp_delivery(env) -> None:
    client = env["client"]
    env["state"]["settings"] = _settings(
        env["tmp_path"], invitation_delivery="smtp", smtp_host="smtp.example.com", smtp_from="ft@example.com"
    )
    pid = _create(client)["id"]

    body, _ = _invite(client, pid, "ok@example.com")
    assert body["envio"] == "smtp" and body["enviado_at"] and body["envio_error"] is None
    msg = env["mails"][0]
    assert msg["To"] == "ok@example.com" and msg["From"] == "ft@example.com"
    assert body["link"] in msg.get_content()

    # Si el correo falla, la invitación igual se crea y el admin recibe el enlace para enviarlo a mano.
    body, _ = _invite(client, pid, "falla@example.com")
    assert body["enviado_at"] is None and "rechazado" in body["envio_error"]
    assert body["link"]


def test_revoke_and_regenerate(env) -> None:
    client = env["client"]
    pid = _open_project(client)["id"]
    inv, token = _invite(client, pid)

    res = client.post(f"/app/projects/{pid}/invitations/{inv['id']}/revoke")
    assert res.status_code == 200 and res.json()["estado"] == "revocada"
    assert client.get(f"/app/invite/{token}").status_code == 410

    res = client.post(f"/app/projects/{pid}/invitations/{inv['id']}/regenerate")
    assert res.status_code == 200 and res.json()["estado"] == "pendiente"
    new_token = res.json()["link"].rsplit("/", 1)[1]
    assert new_token != token
    assert client.get(f"/app/invite/{token}").status_code == 404
    assert client.get(f"/app/invite/{new_token}").status_code == 200


def test_expired_invitation(env) -> None:
    client = env["client"]
    pid = _open_project(client)["id"]
    _, token = _invite(client, pid)
    conn = sqlite3.connect(env["db"])
    conn.execute("UPDATE expert_invitations SET expira_at=?", ((datetime.now(UTC) - timedelta(seconds=1)).isoformat(),))
    conn.commit()
    conn.close()

    res = _anon(client).get(f"/app/invite/{token}")
    assert res.status_code == 410 and "expiró" in res.json()["detail"]


# --- interfaz del experto --------------------------------------------------------------------


def test_invite_only_while_project_open(env) -> None:
    client = env["client"]
    pid = _create(client)["id"]
    _, token = _invite(client, pid)

    assert client.get(f"/app/invite/{token}").status_code == 409  # borrador
    client.post(f"/app/projects/{pid}/estado", json={"estado": "abierto"})
    info = _anon(client).get(f"/app/invite/{token}")
    assert info.status_code == 200
    assert info.json()["causas"] == ["a", "b"] and info.json()["email"] == "experta@example.com"
    assert _anon(client).get("/app/invite/token-inventado").status_code == 404


def test_validate_against_project_labels(env) -> None:
    client = env["client"]
    pid = _open_project(client)["id"]
    _, token = _invite(client, pid)
    anon = _anon(client)

    ok = anon.post(f"/app/invite/{token}/validate", json={"cc": CC, "ce": CE, "ee": EE}).json()
    assert ok["valid"] is True and ok["causes"] == ["a", "b"]

    # Matrices coherentes entre sí, pero con etiquetas distintas a las del proyecto.
    cc = "x,a,z\na,1,0.2\nz,0.3,1\n"
    ce = "x,e1,e2,e3\na,0.1,0.2,0.3\nz,0.4,0.5,0.6\n"
    bad = anon.post(f"/app/invite/{token}/validate", json={"cc": cc, "ce": ce, "ee": EE}).json()
    assert bad["valid"] is False
    assert any("faltan b" in e["message"] and "sobran z" in e["message"] for e in bad["errors"])


def _submit(client: TestClient, token: str, **overrides):
    return client.post(f"/app/invite/{token}/submit", json={"cc": CC, "ce": CE, "ee": EE, **overrides})


def test_expert_submission_launches_computation(env) -> None:
    client = env["client"]
    pid = _open_project(client)["id"]
    inv, token = _invite(client, pid)

    # Etiquetas de CC en otro orden: se reordenan al orden del proyecto.
    cc = "x,b,a\nb,1,0.3\na,0.2,1\n"
    res = _submit(_anon(client), token, cc=cc)
    assert res.status_code == 200, res.text
    assert res.json() == {"received": True}  # el experto no ve resultados
    assert _submit(client, token).status_code == 410  # token de un solo uso

    meta = json.loads(env["uploads"][0]["files"]["meta.json"])
    assert meta == {"causes": ["a", "b"], "effects": ["e1", "e2", "e3"], "thr": 0.5, "maxorder": 3, "reps": 1}
    sent_cc = np.load(__import__("io").BytesIO(env["uploads"][0]["files"]["CC.npy"]))
    assert sent_cc.shape == (1, 2, 2) and sent_cc[0, 0, 1] == np.float32(0.2)

    client.post("/auth/login", json={"username": TEST_USERNAME, "password": TEST_PASSWORD})
    inputs = client.get(f"/app/projects/{pid}/inputs").json()
    assert len(inputs) == 1
    item = inputs[0]
    assert item["email"] == "experta@example.com" and item["invitation_id"] == inv["id"]
    assert item["launch_error"] is None and item["excluido"] is False
    job_id = item["calculos"][0]["job_id"]
    assert item["calculos"][0]["status"] == "pending"

    job = client.get(f"/app/jobs/{job_id}").json()
    assert job["project_id"] == pid and job["input_id"] == item["id"] and job["kind"] == "fe"
    assert [j["job_id"] for j in client.get(f"/app/projects/{pid}/jobs").json()] == [job_id]
    invitations = client.get(f"/app/projects/{pid}/invitations").json()
    assert invitations[0]["estado"] == "usada"

    # Un cálculo pendiente no se reintenta.
    assert client.post(f"/app/projects/{pid}/inputs/{item['id']}/retry").status_code == 409


def test_invalid_submission_keeps_token(env) -> None:
    client = env["client"]
    pid = _open_project(client)["id"]
    _, token = _invite(client, pid)

    res = _submit(_anon(client), token, ce="x,e1\na,0.1\nb,0.2\n")
    assert res.status_code == 422 and res.json()["detail"]["valid"] is False
    assert env["pool"].commands == []
    assert _anon(client).get(f"/app/invite/{token}").status_code == 200


def test_launch_failure_is_saved_and_retried(env) -> None:
    client = env["client"]
    pid = _open_project(client)["id"]
    _, token = _invite(client, pid)

    env["pool"].fail = True
    assert _submit(_anon(client), token).status_code == 200  # el envío del experto se guarda igual

    client.post("/auth/login", json={"username": TEST_USERNAME, "password": TEST_PASSWORD})
    item = client.get(f"/app/projects/{pid}/inputs").json()[0]
    assert item["calculos"] == [] and "No se pudo lanzar fe_job.sh" in item["launch_error"]

    res = client.post(f"/app/projects/{pid}/inputs/{item['id']}/retry")
    assert res.status_code == 200, res.text
    item = client.get(f"/app/projects/{pid}/inputs").json()[0]
    assert item["launch_error"] is None
    assert [c["job_id"] for c in item["calculos"]] == [res.json()["job_id"]]

    # Tras un error reportado por el cluster se puede reintentar: crea un cálculo nuevo.
    asyncio.run(env["store"].update_from_callback(res.json()["job_id"], status="error", logs="boom"))
    res2 = client.post(f"/app/projects/{pid}/inputs/{item['id']}/retry")
    assert res2.status_code == 200
    item = client.get(f"/app/projects/{pid}/inputs").json()[0]
    assert [c["job_id"] for c in item["calculos"]] == [res2.json()["job_id"], res.json()["job_id"]]


def test_exclude_input(env) -> None:
    client = env["client"]
    pid = _open_project(client)["id"]
    _, token = _invite(client, pid)
    _submit(_anon(client), token)
    client.post("/auth/login", json={"username": TEST_USERNAME, "password": TEST_PASSWORD})
    input_id = client.get(f"/app/projects/{pid}/inputs").json()[0]["id"]

    res = client.patch(f"/app/projects/{pid}/inputs/{input_id}", json={"excluido": True})
    assert res.status_code == 200 and res.json()["excluido"] is True
    assert client.get(f"/app/projects/{pid}/inputs").json()[0]["excluido"] is True
    assert client.patch(f"/app/projects/{pid}/inputs/999", json={"excluido": True}).status_code == 404


# --- simulación ------------------------------------------------------------------------------


def _maxmin(client: TestClient, project_id: int, **overrides):
    body = {"project_id": project_id, "numero_nodos": 100, "threshold": 0.5,
            "conectividad_promedio": 2.5, "seed": 1, **overrides}
    return client.post("/app/execute_maxmin", json=body)


def test_maxmin_belongs_to_simulation_project(env) -> None:
    client = env["client"]
    sim = _create(client, tipo="simulacion")
    expert = _create(client)

    assert _maxmin(client, 999).status_code == 404
    assert _maxmin(client, expert["id"]).status_code == 400
    assert _maxmin(client, sim["id"], numero_nodos=101).status_code == 422
    assert env["pool"].commands == []

    res = _maxmin(client, sim["id"])
    assert res.status_code == 200, res.text
    assert "--conectividad 2.5 " in env["pool"].commands[0]
    job = client.get(f"/app/jobs/{res.json()['job_id']}").json()
    assert job["project_id"] == sim["id"] and "project_id" not in job["params"]
    assert client.get(f"/app/projects/{sim['id']}").json()["conteos"]["calculos"] == 1

    client.post(f"/app/projects/{sim['id']}/estado", json={"estado": "cerrado"})
    assert _maxmin(client, sim["id"]).status_code == 409


def test_old_maxmin_jobs_are_migrated(tmp_path: Path) -> None:
    db = str(tmp_path / "jobs.db")
    store = JobStore(db)
    asyncio.run(store.create("1", node="cuda1"))
    asyncio.run(store.create("2", node="cuda1", kind="fe"))

    projects = ProjectStore(db)
    listed = asyncio.run(projects.list_projects())

    assert [(p.nombre, p.tipo) for p in listed] == [(MIGRATED_PROJECT_NAME, "simulacion")]
    assert asyncio.run(store.get("1")).project_id == listed[0].id
    assert asyncio.run(store.get("2")).project_id is None
    # Idempotente: una segunda instancia no crea otro proyecto.
    assert len(asyncio.run(ProjectStore(db).list_projects())) == 1
