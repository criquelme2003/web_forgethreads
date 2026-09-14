import asyncio

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_job_store
from app.main import app
from app.services.job_store import JobStore
from app.services.session_token import issue_auth_token
from tests.conftest import TEST_PASSWORD, TEST_USERNAME

SESSION_SECRET_KEY = "test-secret-key-0123456789"


def _login(client: TestClient) -> None:
    res = client.post("/auth/login", json={"username": TEST_USERNAME, "password": TEST_PASSWORD})
    assert res.status_code == 200


def _valid_bearer_token() -> str:
    return issue_auth_token(TEST_PASSWORD, SESSION_SECRET_KEY)


def _seed_job(store: JobStore, job_id: str, node: str = "cuda1") -> None:
    asyncio.run(store.create(job_id, node=node))


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


def test_callback_without_bearer_returns_401(logged_in_client: TestClient, job_store: JobStore) -> None:
    _seed_job(job_store, "1001")
    res = logged_in_client.post(
        "/app/job_callback",
        json={"job_id": "1001", "status": "success", "effective-order": 3, "computation-time(s)": 0.1},
    )
    assert res.status_code == 401


def test_callback_with_invalid_bearer_returns_401(logged_in_client: TestClient, job_store: JobStore) -> None:
    _seed_job(job_store, "1002")
    res = logged_in_client.post(
        "/app/job_callback",
        json={"job_id": "1002", "status": "success"},
        headers={"Authorization": "Bearer not-a-valid-token"},
    )
    assert res.status_code == 401


def test_callback_invalid_bearer_does_not_mutate_store(
    logged_in_client: TestClient, job_store: JobStore
) -> None:
    _seed_job(job_store, "1003")
    logged_in_client.post(
        "/app/job_callback",
        json={"job_id": "1003", "status": "success", "effective-order": 99},
        headers={"Authorization": "Bearer garbage"},
    )
    res = logged_in_client.get("/app/jobs/1003")
    assert res.status_code == 200
    assert res.json()["status"] == "pending"


def test_callback_valid_bearer_unknown_job_returns_404(logged_in_client: TestClient) -> None:
    res = logged_in_client.post(
        "/app/job_callback",
        json={"job_id": "does-not-exist", "status": "success"},
        headers={"Authorization": f"Bearer {_valid_bearer_token()}"},
    )
    assert res.status_code == 404


def test_callback_success_updates_store(logged_in_client: TestClient, job_store: JobStore) -> None:
    _seed_job(job_store, "1004")
    res = logged_in_client.post(
        "/app/job_callback",
        json={
            "job_id": "1004",
            "status": "success",
            "effective-order": 3,
            "computation-time(s)": 0.42,
            "logs": "log completo",
        },
        headers={"Authorization": f"Bearer {_valid_bearer_token()}"},
    )
    assert res.status_code == 200
    assert res.json() == {"received": True}

    job_res = logged_in_client.get("/app/jobs/1004")
    body = job_res.json()
    assert body["status"] == "success"
    assert body["effective_order"] == 3
    assert body["computation_time_s"] == 0.42
    assert body["logs"] == "log completo"


def test_callback_error_updates_store(logged_in_client: TestClient, job_store: JobStore) -> None:
    _seed_job(job_store, "1005")
    res = logged_in_client.post(
        "/app/job_callback",
        json={"job_id": "1005", "status": "error", "logs": None},
        headers={"Authorization": f"Bearer {_valid_bearer_token()}"},
    )
    assert res.status_code == 200

    job_res = logged_in_client.get("/app/jobs/1005")
    body = job_res.json()
    assert body["status"] == "error"
    assert body["logs"] is None


def test_get_job_requires_session(client: TestClient) -> None:
    res = client.get("/app/jobs/1004")
    assert res.status_code == 401


def test_get_job_unknown_returns_404(logged_in_client: TestClient) -> None:
    res = logged_in_client.get("/app/jobs/unknown-job")
    assert res.status_code == 404
