import asyncio
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.api.fe as fe_api
from app.api.deps import get_job_store, get_node_selector, get_ssh_pool
from app.core.config import Settings, get_settings
from app.main import app
from app.services.job_store import JobStore
from tests.conftest import TEST_PASSWORD, TEST_USERNAME
from tests.test_fe_input import CC, CE, EE

JOB_ID = "4242"
NOTIFIER_ID = "4243"


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
    def __init__(self) -> None:
        self.node_names = ["cuda1"]
        self.commands: list[str] = []

    async def run_on_node(self, node: str, command: str, timeout: float = 10.0) -> _Completed:
        self.commands.append(command)
        job_id = NOTIFIER_ID if "notifier.sh" in command else JOB_ID
        return _Completed(0, f"Submitted batch job {job_id}\n")


class FakeSelector:
    async def select_best(self) -> _Node:
        return _Node("cuda1")

    async def get_all_status(self) -> list[_Node]:
        return [_Node("cuda1")]


@pytest.fixture
def env(tmp_path: Path, monkeypatch):
    pool = FakePool()
    store = JobStore(str(tmp_path / "jobs.db"))
    uploads: dict = {}
    downloads: list = []
    paths_csv = (
        "From,Through1,To,Count,Mean,SD\n"
        "a,b,e1,1,0.75,0.0\n"
        "b,1,e2,1,0.5,0.0\n"
    )

    async def fake_upload(pool_, node, scripts_wf_dir, request_id, files):
        uploads.update(node=node, request_id=request_id, files=files)
        return f"jobs_inputs/{request_id}"

    async def fake_download(pool_, node, scripts_wf_dir, job_id, orders, dest_dir):
        downloads.append((node, job_id, list(orders)))
        dest_dir.mkdir(parents=True, exist_ok=True)
        for order in orders:
            (dest_dir / f"paths_order_{order}.csv").write_text(paths_csv)

    monkeypatch.setattr(fe_api, "upload_fe_input", fake_upload)
    monkeypatch.setattr(fe_api, "download_fe_results", fake_download)

    settings = Settings(
        auth_username=TEST_USERNAME,
        auth_password=TEST_PASSWORD,
        session_secret_key="test-secret-key-0123456789",
        public_callback_base_url="http://testserver",
        fe_results_dir=str(tmp_path / "fe_results"),
    )
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_job_store] = lambda: store
    app.dependency_overrides[get_ssh_pool] = lambda: pool
    app.dependency_overrides[get_node_selector] = lambda: FakeSelector()
    with TestClient(app) as client:
        res = client.post("/auth/login", json={"username": TEST_USERNAME, "password": TEST_PASSWORD})
        assert res.status_code == 200
        yield {"client": client, "pool": pool, "store": store, "uploads": uploads, "downloads": downloads}
    app.dependency_overrides.clear()


def _execute(client: TestClient, **overrides):
    body = {"cc": CC, "ce": CE, "ee": EE, "thr": 0.5, "maxorder": 3, **overrides}
    return client.post("/app/fe/execute", json=body)


def _callback(client: TestClient, token: str, payload: dict):
    return client.post("/app/job_callback", json=payload, headers={"Authorization": f"Bearer {token}"})


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("post", "/app/fe/validate"),
        ("post", "/app/fe/execute"),
        ("get", f"/app/fe/jobs/{JOB_ID}/paths"),
        ("get", f"/app/fe/jobs/{JOB_ID}/paths_order_2.csv"),
    ],
)
def test_fe_routes_require_auth(client: TestClient, method: str, path: str) -> None:
    assert getattr(client, method)(path).status_code == 401


def test_validate_returns_cell_errors(env) -> None:
    res = env["client"].post("/app/fe/validate", json={"cc": "x,a,b\na,1,\nb,0,1\n", "ce": CE, "ee": EE})

    assert res.status_code == 200
    body = res.json()
    assert body["valid"] is False
    assert {"matrix": "CC", "message": "celda vacía", "row": 0, "col": 1} in body["errors"]
    assert body["previews"]["CC"]["cells"][0] == ["1", ""]


def test_execute_invalid_input_does_not_launch(env) -> None:
    res = _execute(env["client"], ce="x,e1\na,0.1\nb,0.2\n")

    assert res.status_code == 422
    assert res.json()["detail"]["valid"] is False
    assert env["pool"].commands == []
    assert env["uploads"] == {}


def test_execute_uploads_and_chains_notifier(env) -> None:
    res = _execute(env["client"])

    assert res.status_code == 200, res.text
    assert res.json()["job_id"] == JOB_ID
    request_id = env["uploads"]["request_id"]
    assert set(env["uploads"]["files"]) == {"CC.npy", "CE.npy", "EE.npy", "meta.json"}
    fe_cmd, notifier_cmd = env["pool"].commands
    assert f"sbatch fe_job.sh --input-dir jobs_inputs/{request_id}" in fe_cmd
    assert f"--dependency=afterany:{JOB_ID} notifier.sh" in notifier_cmd


def test_full_flow_reports_paths(env) -> None:
    client, store = env["client"], env["store"]
    assert _execute(client).status_code == 200
    record = client.get(f"/app/jobs/{JOB_ID}").json()
    assert record["kind"] == "fe" and record["status"] == "pending"
    assert record["params"]["reps"] == 1 and record["params"]["causes"] == ["a", "b"]

    # Antes del callback no hay resultados.
    assert client.get(f"/app/fe/jobs/{JOB_ID}/paths").status_code == 409

    token = _token(store)
    payload = {
        "kind": "fe", "k": 1, "seed": None, "orders": [2], "rows_per_order": {"2": 2},
        "computation-time(s)": 0.4, "status": "success", "logs": "ok", "job_id": JOB_ID,
    }
    assert _callback(client, "malo", payload).status_code == 401
    assert _callback(client, token, payload).status_code == 200

    record = client.get(f"/app/jobs/{JOB_ID}").json()
    assert record["status"] == "success"
    assert record["result"]["orders"] == [2]
    assert "status" not in record["result"] and "logs" not in record["result"]

    res = client.get(f"/app/fe/jobs/{JOB_ID}/paths")
    assert res.status_code == 200, res.text
    table = res.json()["tables"][0]
    assert table["order"] == 2
    # k = 1: Count y SD se omiten; las etiquetas numéricas siguen siendo texto.
    assert table["columns"] == ["From", "Through1", "To", "Mean"]
    assert table["rows"] == [["a", "b", "e1", 0.75], ["b", "1", "e2", 0.5]]

    # Segunda consulta: se usa la caché local, sin volver a descargar.
    client.get(f"/app/fe/jobs/{JOB_ID}/paths")
    assert env["downloads"] == [("cuda1", JOB_ID, [2])]

    csv_res = client.get(f"/app/fe/jobs/{JOB_ID}/paths_order_2.csv")
    assert csv_res.status_code == 200
    assert csv_res.text.startswith("From,Through1,To,Count,Mean,SD")
    assert client.get(f"/app/fe/jobs/{JOB_ID}/paths_order_3.csv").status_code == 404


def test_error_callback_keeps_logs(env) -> None:
    client, store = env["client"], env["store"]
    assert _execute(client).status_code == 200

    payload = {"status": "error", "logs": "entrada inválida", "job_id": JOB_ID}
    assert _callback(client, _token(store), payload).status_code == 200

    record = client.get(f"/app/jobs/{JOB_ID}").json()
    assert record["status"] == "error" and record["logs"] == "entrada inválida"
    assert client.get(f"/app/fe/jobs/{JOB_ID}/paths").status_code == 409


def test_partial_status_is_accepted(env) -> None:
    client, store = env["client"], env["store"]
    assert _execute(client).status_code == 200

    payload = {"status": "partial", "state": "running", "logs": "", "job_id": JOB_ID}
    assert _callback(client, _token(store), payload).status_code == 200
    assert client.get(f"/app/jobs/{JOB_ID}").json()["status"] == "partial"


def _token(store: JobStore) -> str:
    conn = sqlite3.connect(store._resolve_db_path())
    try:
        return conn.execute("SELECT token FROM jobs WHERE job_id=?", (JOB_ID,)).fetchone()[0]
    finally:
        conn.close()


def test_old_jobs_table_is_migrated(tmp_path: Path) -> None:
    db = tmp_path / "old.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE jobs (job_id TEXT PRIMARY KEY, status TEXT NOT NULL, node TEXT NOT NULL, "
        "token TEXT NOT NULL, effective_order INTEGER, computation_time_s REAL, logs TEXT, "
        "created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
    )
    conn.execute(
        "INSERT INTO jobs VALUES ('1','success','cuda1','t',3,0.1,NULL,"
        "'2026-01-01T00:00:00+00:00','2026-01-01T00:00:00+00:00')"
    )
    conn.commit()
    conn.close()

    record = asyncio.run(JobStore(str(db)).get("1"))

    assert record.kind == "maxmin" and record.effective_order == 3 and record.result is None
