import pytest
from fastapi.testclient import TestClient
from http.cookies import SimpleCookie
from tests.conftest import PROTECTED_ROUTES, TEST_PASSWORD, TEST_USERNAME

# Todos los endpoints JSON bajo /app/* requieren sesión (401 sin cookie).
# Solo los que no hacen sondeo SSH se prueban con sesión iniciada.
AUTH_REQUIRED_ROUTES = [
    "/app/nodes/available",
    "/app/cluster_status",
    "/app/gpu_status",
    "/app/jobs",
    "/app/ui-meta",
]


def _login(client: TestClient) -> None:
    res = client.post(
        "/auth/login",
        json={"username": TEST_USERNAME, "password": TEST_PASSWORD},
    )

    assert res.status_code == 200


@pytest.mark.parametrize("path", AUTH_REQUIRED_ROUTES)
def test_protected_route_requires_auth(client: TestClient, path: str) -> None:
    assert client.get(path).status_code == 401


@pytest.mark.parametrize(("path", "markers"), PROTECTED_ROUTES)
def test_protected_route_served_after_login(
    client: TestClient, path: str, markers: tuple[str, ...]
) -> None:
    _login(client)

    res = client.get(path)

    assert res.status_code == 200
    assert "application/json" in res.headers["content-type"]
    for marker in markers:
        assert marker in res.text


def test_protected_route_with_and_without_cookie(client: TestClient) -> None:
    # Sin cookie de sesión -> 401
    assert client.get("/app/jobs").status_code == 401

    # Login: fija la cookie de sesión en el client
    _login(client)
    assert "session" in client.cookies
    
    
    # Con la cookie -> 200
    assert client.get("/app/jobs").status_code == 200

    # Cookie inválida/corrupta -> 401
    client.cookies.set("session", "invalid-session-value")
    assert client.get("/app/jobs").status_code == 401


def test_gpu_status_without_nodes_returns_503(client: TestClient) -> None:
    # En CI no hay nodos alcanzables: el endpoint responde 503 JSON (antes HTML).
    _login(client)
    res = client.get("/app/gpu_status")
    assert res.status_code == 503
    assert "detail" in res.json()
