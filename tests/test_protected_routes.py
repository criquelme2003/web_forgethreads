import pytest
from fastapi.testclient import TestClient

from tests.conftest import PROTECTED_ROUTES, TEST_PASSWORD, TEST_USERNAME

ROUTE_PATHS = [path for path, _ in PROTECTED_ROUTES]


def _login(client: TestClient) -> None:
    res = client.post(
        "/auth/login",
        json={"username": TEST_USERNAME, "password": TEST_PASSWORD},
    )
    assert res.status_code == 200


@pytest.mark.parametrize("path", ROUTE_PATHS)
def test_protected_route_requires_auth(client: TestClient, path: str) -> None:
    assert client.get(path).status_code == 401


@pytest.mark.parametrize(("path", "markers"), PROTECTED_ROUTES)
def test_protected_route_served_after_login(
    client: TestClient, path: str, markers: tuple[str, ...]
) -> None:
    _login(client)

    res = client.get(path)
    assert res.status_code == 200
    assert "text/html" in res.headers["content-type"]
    for marker in markers:
        assert marker in res.text


@pytest.mark.parametrize("path", ROUTE_PATHS)
def test_protected_route_with_and_without_cookie(client: TestClient, path: str) -> None:
    # Sin cookie de sesión -> 401
    assert client.get(path).status_code == 401

    # Login: fija la cookie de sesión en el client
    _login(client)
    assert "session" in client.cookies

    # Con la cookie -> 200
    assert client.get(path).status_code == 200

    # Cookie inválida/corrupta -> 401
    client.cookies.set("session", "invalid-session-value")
    assert client.get(path).status_code == 401
