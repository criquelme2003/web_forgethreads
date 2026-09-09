from fastapi.testclient import TestClient

from tests.conftest import TEST_PASSWORD, TEST_USERNAME


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
