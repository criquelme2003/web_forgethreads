import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.api.sweeps as sweeps_api
from app.api.deps import (
    get_job_store,
    get_node_selector,
    get_project_store,
    get_ssh_pool,
    get_user_store,
)
from app.core.config import get_settings
from app.main import app
from app.services.job_store import JobStore
from app.services.project_store import ProjectStore
from app.services.sweep import combination_stats, parse_raw_csv, plan_sweep, slurm_time, stats_csv
from app.services.user_store import UserStore
from tests.conftest import TEST_PASSWORD, TEST_USERNAME
from tests.test_projects_api import FakePool, FakeSelector, _settings

RAW_CSV = (
    "c,n,repeticion,orden_efectivo\n"
    "0.5,100,0,3\n0.5,100,1,5\n0.5,100,2,4\n"
    "2.5,100,0,40\n"
)


@pytest.fixture
def env(tmp_path: Path, database: str, monkeypatch):
    pool = FakePool()
    store = JobStore(database)
    projects = ProjectStore(database)
    users = UserStore(database)
    downloads: list[str] = []

    async def fake_download(pool_, node, scripts_wf_dir, job_id, dest):
        downloads.append(job_id)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(RAW_CSV)

    monkeypatch.setattr(sweeps_api, "download_sweep_results", fake_download)
    state = {"settings": _settings(tmp_path, sweep_results_dir=str(tmp_path / "sweeps"))}
    app.dependency_overrides[get_settings] = lambda: state["settings"]
    app.dependency_overrides[get_job_store] = lambda: store
    app.dependency_overrides[get_project_store] = lambda: projects
    app.dependency_overrides[get_user_store] = lambda: users
    app.dependency_overrides[get_ssh_pool] = lambda: pool
    app.dependency_overrides[get_node_selector] = lambda: FakeSelector()
    with TestClient(app) as client:
        res = client.post("/auth/login", json={"username": TEST_USERNAME, "password": TEST_PASSWORD})
        assert res.status_code == 200
        res = client.post("/app/projects", json={"nombre": "Sim", "tipo": "simulacion"})
        assert res.status_code == 201
        yield {
            "client": client, "pool": pool, "store": store, "downloads": downloads,
            "state": state, "tmp_path": tmp_path, "pid": res.json()["id"],
        }
    app.dependency_overrides.clear()


SWEEP = {"ns": [100, 101, 6], "cs": [0.5, 2.5], "reps": 3, "thr": 0.5}


def _launch(env, **overrides):
    return env["client"].post(f"/app/projects/{env['pid']}/sweeps", json={**SWEEP, **overrides})


def _callback(env, job_id: str, payload: dict):
    token = asyncio.run(env["store"].get(job_id)).token
    return env["client"].post(
        "/app/job_callback",
        json={"job_id": job_id, "logs": "", **payload},
        headers={"Authorization": f"Bearer {token}"},
    )


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("post", "/app/projects/1/sweeps/preview"),
        ("post", "/app/projects/1/sweeps"),
        ("get", "/app/sweeps/1"),
        ("get", "/app/sweeps/1/crudo.csv"),
        ("get", "/app/sweeps/1/agregado.csv"),
    ],
)
def test_sweep_routes_require_auth(client: TestClient, method: str, path: str) -> None:
    assert getattr(client, method)(path).status_code == 401


# --- servicio --------------------------------------------------------------------------------


def test_plan_validates_combinations_in_job_order() -> None:
    plan = plan_sweep(
        [100, 101, 6], [0.5, 2.5], 3, run_base_s=1, run_s_per_kn3=0, margin=2, overhead_s=10
    )
    assert [(x.c, x.n) for x in plan.valid] == [(0.5, 100), (0.5, 6), (2.5, 100)]
    assert [(x.n, x.c, x.reason) for x in plan.skipped] == [
        (101, 0.5, "N impar"), (101, 2.5, "N impar"), (6, 2.5, "c > N/2 − 1"),
    ]
    assert plan.total_runs == 9
    assert plan.estimated_s == 9 and plan.time_limit_s == 28
    # c = N/2 − 1 es válido (regla de matrix_construction.py).
    assert plan_sweep([6], [2], 1, run_base_s=1, run_s_per_kn3=0, margin=1, overhead_s=0).valid


def test_slurm_time_format() -> None:
    assert slurm_time(5) == "00:01:00"
    assert slurm_time(3661) == "01:01:01"
    assert slurm_time(90061) == "1-01:01:01"


def test_stats_per_combination() -> None:
    stats = combination_stats(parse_raw_csv(RAW_CSV + "x,bad,row,1\n"))
    assert [(s.n, s.c, s.count) for s in stats] == [(100, 0.5, 3), (100, 2.5, 1)]
    first = stats[0]
    assert first.mean == 4 and first.sd == 1 and first.min == 3 and first.max == 5
    assert first.sem == pytest.approx(1 / 3**0.5)
    assert stats[1].sd is None and stats[1].sem is None
    lines = stats_csv(stats).splitlines()
    assert lines[0] == "n,c,media,desviacion_estandar,error_estandar,minimo,maximo,repeticiones"
    assert lines[2] == "100,2.5,40.0,,,40,40,1"


# --- API -------------------------------------------------------------------------------------


def test_preview_lists_valid_and_skipped(env) -> None:
    res = env["client"].post(f"/app/projects/{env['pid']}/sweeps/preview", json=SWEEP)
    assert res.status_code == 200, res.text
    body = res.json()
    assert [(x["n"], x["c"]) for x in body["validas"]] == [(100, 0.5), (6, 0.5), (100, 2.5)]
    assert len(body["omitidas"]) == 3 and body["ejecuciones"] == 9
    assert body["excede"] is False and body["time_limit"]
    assert env["pool"].commands == []


@pytest.mark.parametrize(
    "overrides",
    [
        {"ns": []},
        {"cs": [0]},
        {"ns": [0]},
        {"reps": 0},
        {"thr": 1.5},
        {"seed_base": -1},
        {"reps": 999_999, "ns": [10_000]},  # R × 1000 + max(N) ≥ 10⁹
    ],
)
def test_invalid_sweep_is_rejected(env, overrides: dict) -> None:
    res = env["client"].post(f"/app/projects/{env['pid']}/sweeps/preview", json={**SWEEP, **overrides})
    assert res.status_code == 422


def test_launch_runs_single_job_with_time_limit(env) -> None:
    res = _launch(env, seed_base=2)
    assert res.status_code == 200, res.text
    job_id = res.json()["job_id"]
    sweep_cmd, notifier_cmd = [c for c in env["pool"].commands if "sbatch" in c]
    time_limit = res.json()["preview"]["time_limit"]
    assert f"sbatch --time={time_limit} sweep_job.sh --ns 100,101,6 --cs 0.5,2.5 --reps 3 --thr 0.5 --seed-base 2" in sweep_cmd
    assert f"--dependency=afterany:{job_id} notifier.sh" in notifier_cmd

    job = env["client"].get(f"/app/jobs/{job_id}").json()
    assert job["kind"] == "sweep" and job["project_id"] == env["pid"]
    assert job["params"]["ns"] == [100, 101, 6] and job["params"]["combinaciones"] == 3


def test_launch_rejections(env) -> None:
    client, pid = env["client"], env["pid"]
    assert _launch(env, ns=[101]).status_code == 400  # ninguna combinación válida

    env["state"]["settings"] = _settings(env["tmp_path"], sweep_max_hours=0.01)
    res = _launch(env)
    assert res.status_code == 400 and "máximo" in res.json()["detail"]
    env["state"]["settings"] = _settings(env["tmp_path"])

    expert = client.post(
        "/app/projects",
        json={"nombre": "E", "tipo": "expertos", "causas": ["a"], "efectos": ["b"]},
    ).json()
    assert client.post(f"/app/projects/{expert['id']}/sweeps", json=SWEEP).status_code == 400
    assert client.post("/app/projects/999/sweeps", json=SWEEP).status_code == 404
    client.post(f"/app/projects/{pid}/estado", json={"estado": "cerrado"})
    assert _launch(env).status_code == 409
    assert env["pool"].commands == []


def test_results_after_callback(env) -> None:
    client = env["client"]
    job_id = _launch(env).json()["job_id"]

    res = client.get(f"/app/sweeps/{job_id}")
    assert res.status_code == 200 and res.json()["estadisticas"] == []
    assert client.get(f"/app/sweeps/{job_id}/crudo.csv").status_code == 409

    failures = [{"c": 2.5, "n": 100, "rep": 1, "error": "CUDA out of memory"}]
    res = _callback(env, job_id, {
        "status": "partial", "kind": "sweep", "state": "running", "failures": failures,
        "completed_combinations": 2, "total_combinations": 3,
    })
    assert res.status_code == 200

    body = client.get(f"/app/sweeps/{job_id}").json()
    assert body["job"]["status"] == "partial"
    assert [(s["n"], s["c"], s["repeticiones"]) for s in body["estadisticas"]] == [(100, 0.5, 3), (100, 2.5, 1)]
    assert body["fallas"] == [{"n": 100, "c": 2.5, "rep": 1, "error": "CUDA out of memory"}]
    assert body["sin_datos"] == [{"n": 6, "c": 0.5}]  # el barrido se cortó antes
    assert len(body["omitidas"]) == 3

    raw = client.get(f"/app/sweeps/{job_id}/crudo.csv")
    assert raw.status_code == 200 and raw.text == RAW_CSV
    agg = client.get(f"/app/sweeps/{job_id}/agregado.csv")
    assert agg.status_code == 200 and agg.text.startswith("n,c,media")
    assert env["downloads"] == [job_id]  # se descarga una vez y queda en caché


def test_sweep_endpoint_only_for_sweeps(env) -> None:
    client = env["client"]
    res = client.post("/app/execute_maxmin", json={
        "project_id": env["pid"], "numero_nodos": 100, "threshold": 0.5,
        "conectividad_promedio": 2, "seed": 1,
    })
    assert client.get(f"/app/sweeps/{res.json()['job_id']}").status_code == 404
    assert client.get("/app/sweeps/abc").status_code == 404
