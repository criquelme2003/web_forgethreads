import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings
from app.main import app

TEST_USERNAME = "testadmin"
TEST_PASSWORD = "testpass"


def _override_settings() -> Settings:
    return Settings(
        auth_username=TEST_USERNAME,
        auth_password=TEST_PASSWORD,
        session_secret_key="test-secret-key-0123456789",
    )


@pytest.fixture
def client() -> TestClient:
    app.dependency_overrides[get_settings] = _override_settings
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_login_success_sets_httponly_cookie(client: TestClient) -> None:
    res = client.post(
        "/auth/login",
        json={"username": TEST_USERNAME, "password": TEST_PASSWORD},
    )
    assert res.status_code == 200
    assert res.json() == {"success": True}
    set_cookie = res.headers["set-cookie"]
    assert "session=" in set_cookie
    assert "httponly" in set_cookie.lower()


def test_login_invalid_credentials(client: TestClient) -> None:
    res = client.post(
        "/auth/login",
        json={"username": TEST_USERNAME, "password": "wrong"},
    )
    assert res.status_code == 401
    assert "set-cookie" not in res.headers


def test_login_incomplete_body(client: TestClient) -> None:
    res = client.post("/auth/login", json={"username": TEST_USERNAME})
    assert res.status_code == 422


def test_front_login_served(client: TestClient) -> None:
    res = client.get("/front/login")
    assert res.status_code == 200
    assert "text/html" in res.headers["content-type"]


def test_parameters_requires_auth(client: TestClient) -> None:
    res = client.get("/parameters")
    assert res.status_code == 401


def test_parameters_served_after_login(client: TestClient) -> None:
    login_res = client.post(
        "/auth/login",
        json={"username": TEST_USERNAME, "password": TEST_PASSWORD},
    )
    assert login_res.status_code == 200

    res = client.get("/parameters")
    assert res.status_code == 200
    assert "text/html" in res.headers["content-type"]
    body = res.text
    assert 'name="nodos_totales"' in body
    assert 'name="c"' in body
    assert 'name="threshold"' in body
