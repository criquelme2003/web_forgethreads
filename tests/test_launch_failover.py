"""Prevención al enviar (RF-46): estado real de los nodos y failover entre nodos."""

import asyncio
from dataclasses import dataclass, field

import pytest
from fastapi import HTTPException

from app.api.launch import submit_with_failover
from app.core.config import Settings
from app.repositories.slurm import parse_node_state, parse_scontrol_output
from app.services.job_store import JobStore
from app.services.node_selector import NodeSelector
from app.services.slurm_submit import blocking_reason, parse_job_state
from app.ssh.models import NodeState, NodeStatus

# --- estado de los nodos ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("IDLE", NodeState.IDLE),
        ("MIXED", NodeState.MIX),
        ("IDLE+DRAIN", NodeState.DRAIN),
        ("MIXED+DRAIN", NodeState.DRAIN),
        ("ALLOCATED+DRAIN", NodeState.DRAIN),
        ("IDLE+RESERVED", NodeState.DRAIN),
        ("IDLE+MAINT", NodeState.DRAIN),
        ("IDLE*", NodeState.DOWN),
        ("IDLE+NOT_RESPONDING", NodeState.DOWN),
        ("DOWN*+DRAIN", NodeState.DOWN),
        ("IDLE+COMPLETING", NodeState.IDLE),
    ],
)
def test_state_flags_are_considered(raw: str, expected: NodeState) -> None:
    assert parse_node_state(raw)[0] == expected


def test_drained_node_is_not_available() -> None:
    raw = (
        "NodeName=cuda1 Arch=x86_64 CoresPerSocket=8\n"
        "   CPUAlloc=0 CPUTot=16 State=IDLE+DRAIN ThreadsPerCore=1\n"
        "   Gres=gpu:1 RealMemory=64000 AllocMem=0\n"
        "   Reason=mantencion GPU [root@2026-10-13T10:00:00] Partitions=RunCuda\n"
    )
    status = parse_scontrol_output(raw, "cuda1", "host")
    assert status.state == NodeState.DRAIN and not status.is_available
    assert status.state_flags == ("DRAIN",)
    assert status.reason == "mantencion GPU [root@2026-10-13T10:00:00]"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("idle*", NodeState.DOWN), ("drained", NodeState.DRAIN), ("maint", NodeState.DRAIN), ("mixed", NodeState.MIX)],
)
def test_sinfo_states(raw: str, expected: NodeState) -> None:
    assert NodeState.from_slurm(raw) == expected


@pytest.mark.parametrize(
    ("stdout", "blocked"),
    [
        ("PENDING|ReqNodeNotAvail, UnavailableNodes:cuda1\n", True),
        ("PENDING|Nodes required for job are DOWN, DRAINED or reserved for jobs in higher priority partitions", True),
        ("PENDING|PartitionDown", True),
        ("PENDING|Resources", False),
        ("PENDING|Priority", False),
        ("RUNNING|None", False),
    ],
)
def test_blocking_reasons(stdout: str, blocked: bool) -> None:
    parsed = parse_job_state(stdout)
    assert parsed is not None
    assert (blocking_reason(*parsed) is not None) is blocked


def test_job_not_in_queue() -> None:
    assert parse_job_state("") is None


def _status(name: str, state: NodeState = NodeState.IDLE, idle_gpu: bool = False, gpus: int = 1) -> NodeStatus:
    return NodeStatus(
        name=name, host=name, state=state, cpus_total=16, cpus_alloc=0, cpus_idle=16,
        gpus_total=gpus, gpus_alloc=0, mem_total_mb=0, mem_alloc_mb=0, has_idle_gpu=idle_gpu,
    )


def test_ranked_candidates_prefers_idle_gpu_and_skips_drained(monkeypatch) -> None:
    selector = NodeSelector(pool=None)  # type: ignore[arg-type]
    statuses = [
        _status("cuda1", NodeState.IDLE, idle_gpu=False, gpus=2),
        _status("cuda2", NodeState.MIX, idle_gpu=True),
        _status("cuda3", NodeState.DRAIN, idle_gpu=True),
    ]
    calls = {}

    async def fake_all(force_refresh: bool = False):
        calls["force_refresh"] = force_refresh
        return statuses

    async def fake_idle(status, threshold=5):
        return status

    monkeypatch.setattr(selector, "get_all_status", fake_all)
    monkeypatch.setattr(selector, "_check_idle_for_status", fake_idle)

    ranked, all_statuses = asyncio.run(selector.ranked_candidates())

    assert [s.name for s in ranked] == ["cuda2", "cuda1"]
    assert len(all_statuses) == 3 and calls["force_refresh"] is True


# --- failover --------------------------------------------------------------------------------


@dataclass
class _Completed:
    exit_status: int
    stdout: str = ""
    stderr: str = ""


@dataclass
class FakePool:
    """Simula sbatch/squeue/scancel por nodo. `sbatch_fails` y `blocked` son conjuntos de nodos."""

    sbatch_fails: set = field(default_factory=set)
    blocked: set = field(default_factory=set)
    commands: list = field(default_factory=list)
    node_names: list = field(default_factory=lambda: ["cuda1", "cuda2", "cuda3"])
    next_id: int = 500

    async def run_on_node(self, node: str, command: str, timeout: float = 10.0):
        self.commands.append((node, command))
        if command.startswith("squeue"):
            reason = "ReqNodeNotAvail, UnavailableNodes:" + node if node in self.blocked else "Resources"
            return _Completed(0, f"PENDING|{reason}\n")
        if command.startswith("scancel"):
            return _Completed(0)
        if "notifier.sh" not in command and node in self.sbatch_fails:
            return _Completed(1, stderr="sbatch: error: Batch job submission failed")
        self.next_id += 1
        return _Completed(0, f"Submitted batch job {self.next_id}\n")

    def sbatch_nodes(self) -> list[str]:
        return [n for n, c in self.commands if "sbatch" in c and "notifier.sh" not in c]


class FakeSelector:
    def __init__(self, statuses: list[NodeStatus]) -> None:
        self.statuses = statuses

    async def get_all_status(self, force_refresh: bool = False):
        return self.statuses

    async def ranked_candidates(self, force_refresh: bool = True, gpu_idle_threshold: int = 5):
        return [s for s in self.statuses if s.is_available], self.statuses


@pytest.fixture
def ctx(database: str):
    settings = Settings(
        auth_username="u", auth_password="p", session_secret_key="s" * 20,
        public_callback_base_url="http://testserver", submit_check_delay_s=0,
    )
    store = JobStore(database)
    selector = FakeSelector([_status("cuda1"), _status("cuda2"), _status("cuda3", NodeState.DRAIN)])
    return settings, store, selector


def _submit(ctx, pool: FakePool, nodo: str | None = None, prepare=None):
    settings, store, selector = ctx

    async def default_prepare(node: str) -> str:
        return "cd scripts_wf && sbatch new_job.sh"

    return asyncio.run(
        submit_with_failover(
            nodo=nodo, pool=pool, selector=selector, store=store, settings=settings,
            prepare=prepare or default_prepare, script="new_job.sh", kind="maxmin",
        )
    )


def test_first_node_ok(ctx) -> None:
    pool = FakePool()
    job_id, node = _submit(ctx, pool)
    assert node == "cuda1"
    assert asyncio.run(ctx[1].get(job_id)).node == "cuda1"
    assert any(n == "cuda1" and "notifier.sh" in c for n, c in pool.commands)


def test_sbatch_failure_moves_to_next_node(ctx) -> None:
    pool = FakePool(sbatch_fails={"cuda1"})
    job_id, node = _submit(ctx, pool)
    assert node == "cuda2" and pool.sbatch_nodes() == ["cuda1", "cuda2"]
    assert asyncio.run(ctx[1].get(job_id)).node == "cuda2"


def test_blocked_job_is_cancelled_and_resubmitted(ctx) -> None:
    pool = FakePool(blocked={"cuda1"})
    job_id, node = _submit(ctx, pool)

    assert node == "cuda2"
    assert ("cuda1", "scancel 501") in pool.commands
    # El job cancelado nunca queda registrado ni con notifier.
    assert not any(n == "cuda1" and "notifier.sh" in c for n, c in pool.commands)
    assert job_id == "502"


def test_upload_failure_moves_to_next_node(ctx) -> None:
    pool = FakePool()
    prepared = []

    async def prepare(node: str) -> str:
        prepared.append(node)
        if node == "cuda1":
            raise HTTPException(502, "No se pudo subir el input al nodo cuda1")
        return "sbatch fe_job.sh"

    _, node = _submit(ctx, pool, prepare=prepare)
    assert node == "cuda2" and prepared == ["cuda1", "cuda2"]


def test_all_nodes_fail(ctx) -> None:
    pool = FakePool(sbatch_fails={"cuda1"}, blocked={"cuda2"})
    with pytest.raises(HTTPException) as exc:
        _submit(ctx, pool)
    assert exc.value.status_code == 502
    assert "cuda1: sbatch falló" in exc.value.detail
    assert "cuda2: job 501 bloqueado: ReqNodeNotAvail" in exc.value.detail
    assert "cuda3" not in pool.sbatch_nodes()  # drenado: ni se intenta


def test_explicit_node_has_no_failover(ctx) -> None:
    pool = FakePool(blocked={"cuda1"})
    with pytest.raises(HTTPException) as exc:
        _submit(ctx, pool, nodo="cuda1")
    assert exc.value.status_code == 502 and pool.sbatch_nodes() == ["cuda1"]


def test_explicit_unavailable_node_is_rejected(ctx) -> None:
    ctx[2].statuses[2].reason = "mantencion"
    with pytest.raises(HTTPException) as exc:
        _submit(ctx, FakePool(), nodo="cuda3")
    assert exc.value.status_code == 400 and "drain" in exc.value.detail and "mantencion" in exc.value.detail


def test_no_candidates(ctx) -> None:
    ctx[2].statuses[:] = [_status("cuda1", NodeState.DOWN), _status("cuda2", NodeState.DRAIN)]
    with pytest.raises(HTTPException) as exc:
        _submit(ctx, FakePool())
    assert exc.value.status_code == 502 and "cuda1: down" in exc.value.detail


def test_cluster_status_shows_slurm_reason(client, monkeypatch) -> None:
    from app.api.deps import get_node_selector
    from app.main import app
    from tests.conftest import TEST_PASSWORD, TEST_USERNAME

    drained = _status("cuda1", NodeState.DRAIN)
    drained.reason = "mantencion GPU [root@2026-10-13T10:00:00]"

    class Selector:
        async def get_all_status(self, force_refresh: bool = False):
            return [drained, _status("cuda2")]

        def score(self, s):
            return NodeSelector.score(NodeSelector(pool=None), s)  # type: ignore[arg-type]

    app.dependency_overrides[get_node_selector] = lambda: Selector()
    client.post("/auth/login", json={"username": TEST_USERNAME, "password": TEST_PASSWORD})
    rows = {r["name"]: r for r in client.get("/app/cluster_status").json()["rows"]}

    assert rows["cuda1"]["reason"] == "mantencion GPU [root@2026-10-13T10:00:00]"
    assert rows["cuda1"]["state"] == "drain" and rows["cuda2"]["reason"] is None
