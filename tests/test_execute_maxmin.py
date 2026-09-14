import asyncio
import re
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_job_store
from app.main import app
from app.services.job_store import JobNotFoundError, JobStore
from app.services.node_selector import NodeSelector
from app.services.session_token import verify_auth_token
from app.ssh.models import NodeState, NodeStatus
from app.ssh.pool import SSHConnectionPool
from tests.conftest import TEST_PASSWORD, TEST_USERNAME

SESSION_SECRET_KEY = "test-secret-key-0123456789"

VALID_PAYLOAD = {
    "numero_nodos": 5000,
    "threshold": 0.15,
    "conectividad_promedio": 8,
    "seed": 42,
}


def _login(client: TestClient) -> None:
    res = client.post("/auth/login", json={"username": TEST_USERNAME, "password": TEST_PASSWORD})
    assert res.status_code == 200


@pytest.fixture
def job_store() -> JobStore:
    store = JobStore()
    app.dependency_overrides[get_job_store] = lambda: store
    yield store
    del app.dependency_overrides[get_job_store]


@pytest.fixture
def logged_in_client(client: TestClient, job_store: JobStore):
    _login(client)
    return client


def _available_status(name: str = "cuda3") -> NodeStatus:
    return NodeStatus(
        name=name,
        host="192.192.192",
        state=NodeState.IDLE,
        cpus_total=32,
        cpus_alloc=0,
        cpus_idle=32,
        gpus_total=4,
        gpus_alloc=0,
        mem_total_mb=64000,
        mem_alloc_mb=0,
        reachable=True,
    )


class FakeRunOnNode:
    """Sustituye SSHConnectionPool.run_on_node con respuestas programadas por comando."""

    def __init__(self, responses: list):
        self.responses = list(responses)
        self.calls: list[tuple[str, str]] = []

    async def __call__(self, node_name, command, timeout=10.0):
        self.calls.append((node_name, command))
        if not self.responses:
            raise AssertionError("No hay más respuestas programadas para run_on_node")
        return self.responses.pop(0)


def _sbatch_ok(job_id: str) -> SimpleNamespace:
    return SimpleNamespace(exit_status=0, stdout=f"Submitted batch job {job_id}\n", stderr="")


def _sbatch_fail() -> SimpleNamespace:
    return SimpleNamespace(exit_status=1, stdout="", stderr="sbatch: error: something failed")


def _install_fake_run_on_node(monkeypatch: pytest.MonkeyPatch, responses: list) -> FakeRunOnNode:
    fake = FakeRunOnNode(responses)
    monkeypatch.setattr(SSHConnectionPool, "run_on_node", fake)
    return fake


def _install_available_node_status(monkeypatch: pytest.MonkeyPatch, name: str = "cuda3") -> None:
    """Evita que el chequeo de disponibilidad de nodo dependa de SSH real al cluster."""

    async def fake_get_all_status(self, force_refresh: bool = False):
        return [_available_status(name)]

    monkeypatch.setattr(NodeSelector, "get_all_status", fake_get_all_status)


def test_execute_maxmin_success_full_flow(
    logged_in_client: TestClient, job_store: JobStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_available_node_status(monkeypatch)
    fake = _install_fake_run_on_node(monkeypatch, [_sbatch_ok("937"), _sbatch_ok("938")])

    res = logged_in_client.post("/app/execute_maxmin", json={**VALID_PAYLOAD, "nodo": "cuda3"})
    assert res.status_code == 200
    body = res.json()
    assert body == {"job_id": "937", "status": "pending"}

    assert len(fake.calls) == 2
    node1, new_job_cmd = fake.calls[0]
    node2, notifier_cmd = fake.calls[1]
    assert node1 == "cuda3"
    assert node2 == "cuda3"
    assert "new_job.sh" in new_job_cmd
    assert "--nodos 5000" in new_job_cmd
    assert "--thr 0.15" in new_job_cmd
    assert "--conectividad 8" in new_job_cmd
    assert "--seed 42" in new_job_cmd

    assert "--dependency=afterok:937" in notifier_cmd
    assert "--job-id 937" in notifier_cmd
    assert "--callback-url http://testserver/app/job_callback" in notifier_cmd

    # el auth-token usado en ambos comandos debe ser el mismo y verificar contra auth_password
    token_match = re.search(r"--auth-token (\S+)", new_job_cmd)
    assert token_match is not None
    token = token_match.group(1)
    verify_auth_token(token, TEST_PASSWORD, SESSION_SECRET_KEY, max_age=14 * 24 * 60 * 60)
    assert f"--auth-token {token}" in notifier_cmd

    record = asyncio.run(job_store.get("937"))
    assert record.status == "pending"
    assert record.node == "cuda3"


def test_execute_maxmin_first_sbatch_fails(
    logged_in_client: TestClient, job_store: JobStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_available_node_status(monkeypatch)
    fake = _install_fake_run_on_node(monkeypatch, [_sbatch_fail()])

    res = logged_in_client.post("/app/execute_maxmin", json={**VALID_PAYLOAD, "nodo": "cuda3"})
    assert res.status_code == 502
    assert len(fake.calls) == 1  # nunca se intenta el notifier

    with pytest.raises(JobNotFoundError):
        asyncio.run(job_store.get("937"))


def test_execute_maxmin_second_sbatch_fails(
    logged_in_client: TestClient, job_store: JobStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_available_node_status(monkeypatch)
    _install_fake_run_on_node(monkeypatch, [_sbatch_ok("937"), _sbatch_fail()])

    res = logged_in_client.post("/app/execute_maxmin", json={**VALID_PAYLOAD, "nodo": "cuda3"})
    assert res.status_code == 502

    record = asyncio.run(job_store.get("937"))
    assert record.status == "error"
    assert "937" in record.logs


def test_execute_maxmin_invalid_node_returns_400_without_ssh_calls(
    logged_in_client: TestClient, job_store: JobStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_available_node_status(monkeypatch)
    fake = _install_fake_run_on_node(monkeypatch, [])

    res = logged_in_client.post(
        "/app/execute_maxmin", json={**VALID_PAYLOAD, "nodo": "does-not-exist"}
    )
    assert res.status_code == 400
    assert len(fake.calls) == 0


def test_execute_maxmin_without_node_uses_auto_select(
    logged_in_client: TestClient, job_store: JobStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_available_node_status(monkeypatch)
    fake = _install_fake_run_on_node(monkeypatch, [_sbatch_ok("939"), _sbatch_ok("940")])

    res = logged_in_client.post("/app/execute_maxmin", json=VALID_PAYLOAD)
    assert res.status_code == 200
    assert res.json() == {"job_id": "939", "status": "pending"}
    assert len(fake.calls) == 2
    assert fake.calls[0][0] == "cuda3"
