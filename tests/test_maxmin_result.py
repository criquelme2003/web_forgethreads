import asyncio

from fastapi.testclient import TestClient

from app.api.deps import get_job_store
from app.services.maxmin_log import parse_maxmin_log
from tests.conftest import TEST_PASSWORD, TEST_USERNAME

COUNTS = [
    20000, 39911, 79416, 155181, 293549, 522350, 840732, 1163251, 1331393, 1243698, 962816,
    635221, 369987, 196402, 96361, 43849, 18216, 6913, 2448, 854, 318, 107, 45, 16, 3, 1,
]
LOG = (
    "[MAXMIN C++] M: 5000B: 1\n[MAXMIN C++] ORDER-100\n"
    + "".join(f"[MAXMIN C++] Efectos encontrados en orden {i} : {c}\n" for i, c in enumerate(COUNTS, 1))
    + "[MAXMIN C++] Convergencia en step 27\n"
)


def test_parse_real_log() -> None:
    parsed = parse_maxmin_log(LOG)
    assert parsed.effects_by_order == list(enumerate(COUNTS, 1))
    assert parsed.convergence_step == 27
    assert (parsed.m, parsed.b, parsed.order_limit) == (5000, 1, 100)


def test_parse_empty_or_noisy_log() -> None:
    assert parse_maxmin_log(None).effects_by_order == []
    parsed = parse_maxmin_log("slurmstepd: error\n[MAXMIN C++] Efectos encontrados en orden 2 : 7\notra cosa")
    assert parsed.effects_by_order == [(2, 7)] and parsed.convergence_step is None


def test_maxmin_result_endpoint(client: TestClient) -> None:
    store = client.app.dependency_overrides[get_job_store]()
    asyncio.run(store.create("680", node="cuda2", params={"numero_nodos": 5000}, project_id=2))
    asyncio.run(store.update_from_callback("680", status="success", effective_order=26, logs=LOG))
    asyncio.run(store.create("681", node="cuda2", kind="fe"))

    assert client.get("/app/maxmin/jobs/680").status_code == 401
    client.post("/auth/login", json={"username": TEST_USERNAME, "password": TEST_PASSWORD})

    body = client.get("/app/maxmin/jobs/680").json()
    assert body["job"]["effective_order"] == 26 and body["job"]["project_id"] == 2
    assert body["effects_by_order"][8] == {"order": 9, "count": 1331393}
    assert len(body["effects_by_order"]) == 26 and body["convergence_step"] == 27
    assert client.get("/app/maxmin/jobs/681").status_code == 404
    assert client.get("/app/maxmin/jobs/999").status_code == 404
