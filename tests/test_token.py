from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.testclient import TestClient

from app.api.deps import require_api_token
from app.main import app
from tests.conftest import TEST_PASSWORD, TEST_USERNAME

# Endpoint de prueba para validar require_api_token
test_router = APIRouter(prefix="/test", tags=["test"])


@test_router.get("/protected-by-token")
def protected_endpoint(token: Annotated[str, Depends(require_api_token)]) -> dict:
    """Endpoint protegido que solo acepta Bearer tokens."""
    return {"message": "success", "token": token[:10] + "..."}


# Registra el router de prueba
app.include_router(test_router)


def test_token_endpoint_requires_valid_credentials(client: TestClient) -> None:
    """POST /auth/token con credenciales inválidas retorna 401."""
    res = client.post(
        "/auth/token",
        json={"username": TEST_USERNAME, "password": "wrong"},
    )
    assert res.status_code == 401
    assert res.json()["detail"] == "Invalid credentials"


def test_token_endpoint_success_returns_bearer_token(client: TestClient) -> None:
    """POST /auth/token con credenciales válidas retorna access_token."""
    res = client.post(
        "/auth/token",
        json={"username": TEST_USERNAME, "password": TEST_PASSWORD},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["token_type"] == "bearer"
    assert "access_token" in body
    assert isinstance(body["access_token"], str)
    assert len(body["access_token"]) > 20  # token_urlsafe genera string largo
    assert body["expires_in"] == 86400  # 24 horas


def test_token_endpoint_incomplete_body(client: TestClient) -> None:
    """POST /auth/token sin contraseña retorna 422."""
    res = client.post(
        "/auth/token",
        json={"username": TEST_USERNAME},
    )
    assert res.status_code == 422


def test_token_endpoint_empty_credentials(client: TestClient) -> None:
    """POST /auth/token con credenciales vacías retorna 401."""
    res = client.post(
        "/auth/token",
        json={"username": "", "password": ""},
    )
    assert res.status_code == 401


def test_require_api_token_with_valid_token(client: TestClient) -> None:
    """Endpoint protegido acepta request con Bearer token válido."""
    # Obtener token
    token_res = client.post(
        "/auth/token",
        json={"username": TEST_USERNAME, "password": TEST_PASSWORD},
    )
    assert token_res.status_code == 200
    token = token_res.json()["access_token"]

    # Usar token en endpoint protegido
    res = client.get(
        "/test/protected-by-token",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["message"] == "success"
    assert token[:10] in body["token"]


def test_require_api_token_missing_header(client: TestClient) -> None:
    """Endpoint protegido rechaza request sin Authorization header."""
    res = client.get("/test/protected-by-token")
    assert res.status_code == 401
    assert res.json()["detail"] == "Missing or invalid Authorization header"


def test_require_api_token_invalid_format(client: TestClient) -> None:
    """Endpoint protegido rechaza Authorization header sin 'Bearer ' prefix."""
    res = client.get(
        "/test/protected-by-token",
        headers={"Authorization": "InvalidFormat xyz123"},
    )
    assert res.status_code == 401
    assert res.json()["detail"] == "Missing or invalid Authorization header"


def test_require_api_token_empty_token(client: TestClient) -> None:
    """Endpoint protegido rechaza 'Authorization: Bearer ' sin token."""
    res = client.get(
        "/test/protected-by-token",
        headers={"Authorization": "Bearer "},
    )
    assert res.status_code == 401
    assert res.json()["detail"] == "Invalid token"


def test_require_api_token_wrong_bearer_case(client: TestClient) -> None:
    """Endpoint protegido rechaza 'authorization: bearer' con caso diferente."""
    # El header es case-insensitive en HTTP, pero nuestro check de "Bearer " es case-sensitive
    res = client.get(
        "/test/protected-by-token",
        headers={"Authorization": "bearer token123"},
    )
    assert res.status_code == 401
    assert res.json()["detail"] == "Missing or invalid Authorization header"
