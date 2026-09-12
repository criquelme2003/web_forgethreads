import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings
from app.main import app

TEST_USERNAME = "testadmin"
TEST_PASSWORD = "testpass"

# Rutas protegidas: requieren cookie de sesión válida.
# (path, marcadores que deben aparecer en el HTML devuelto tras autenticarse)
PROTECTED_ROUTES = [
    (
        "/front/parameters",
        ('name="numero_nodos"', 'name="threshold"', 'name="conectividad_promedio"', 'name="seed"'),
    ),
    (
        "/front/execute_maxmin",
        ('name="numero_nodos"', 'name="threshold"', 'name="conectividad_promedio"', 'name="seed"'),
    ),
]


def _override_settings() -> Settings:
    return Settings(
        auth_username=TEST_USERNAME,
        auth_password=TEST_PASSWORD,
        session_secret_key="test-secret-key-0123456789",
        cuda3_password="1234",
        cuda3_username="test_user",
        cuda3_ip="192.192.192"
    )


@pytest.fixture
def client() -> TestClient:
    app.dependency_overrides[get_settings] = _override_settings
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
