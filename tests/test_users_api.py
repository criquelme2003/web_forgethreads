"""Roles (superadmin / admin), alta de admins, co-admins y filtro por membresía (RF-01..RF-04, RF-48, RF-49)."""

import asyncio

from fastapi.testclient import TestClient

import app.api.users as users_api
from app.api.deps import get_job_store
from app.core.config import Settings, get_settings
from app.main import app
from tests.conftest import TEST_PASSWORD, TEST_USERNAME, _override_settings

PASSWORD = "clave-segura-123"
EXPERT_PROJECT = {"nombre": "Retail", "tipo": "expertos", "causas": ["a", "b"], "efectos": ["e1"]}


def _login(client: TestClient, username: str = TEST_USERNAME, password: str = TEST_PASSWORD) -> int:
    return client.post("/auth/login", json={"username": username, "password": password}).status_code


def _new_admin(client: TestClient, email: str, nombre: str = "Admin") -> dict:
    """Crea el admin como superadmin y lo activa. Deja la sesión del superadmin abierta."""
    assert _login(client) == 200
    res = client.post("/app/users", json={"email": email, "nombre": nombre})
    assert res.status_code == 201, res.text
    body = res.json()
    token = body["link"].rsplit("/", 1)[1]
    assert client.post(f"/app/activate/{token}", json={"password": PASSWORD}).status_code == 200
    return body


def _project(client: TestClient, **overrides) -> dict:
    res = client.post("/app/projects", json={**EXPERT_PROJECT, **overrides})
    assert res.status_code == 201, res.text
    return res.json()


def test_admin_routes_require_auth(client: TestClient) -> None:
    for method, path in [
        ("get", "/app/users"), ("post", "/app/users"), ("get", "/app/users/directory"),
        ("post", "/app/users/1/deactivate"), ("post", "/app/users/1/activate"),
        ("post", "/app/users/1/activation-link"), ("get", "/auth/me"),
    ]:
        assert getattr(client, method)(path).status_code == 401, path


def test_superadmin_comes_from_settings(client: TestClient) -> None:
    assert _login(client) == 200
    me = client.get("/auth/me").json()
    assert me["email"] == TEST_USERNAME and me["rol"] == "superadmin"
    assert _login(client, password="otra") == 401

    # Si cambia AUTH_PASSWORD, el superadmin entra con la nueva y no con la anterior.
    app.dependency_overrides[get_settings] = lambda: Settings(
        **{**_override_settings().model_dump(), "auth_password": "nueva-clave"}
    )
    assert _login(client) == 401
    assert _login(client, password="nueva-clave") == 200


def test_activation_flow(client: TestClient) -> None:
    assert _login(client) == 200
    res = client.post("/app/users", json={"email": "Ana@Example.com", "nombre": "Ana"})
    assert res.status_code == 201
    body = res.json()
    assert body["email"] == "ana@example.com" and body["estado"] == "pendiente" and body["envio"] == "manual"
    token = body["link"].rsplit("/", 1)[1]
    assert body["link"] == f"http://testserver/front/activate/{token}"
    assert client.post("/app/users", json={"email": "ana@example.com", "nombre": "x"}).status_code == 409
    assert client.post("/app/users", json={"email": TEST_USERNAME + "@x.cl", "nombre": "x"}).status_code == 201

    client.cookies.clear()
    assert _login(client, "ana@example.com", "") == 401  # sin contraseña aún
    assert client.get(f"/app/activate/{token}").json() == {"email": "ana@example.com", "nombre": "Ana"}
    assert client.post(f"/app/activate/{token}", json={"password": "corta"}).status_code == 422
    assert client.post(f"/app/activate/{token}", json={"password": PASSWORD}).status_code == 200
    assert client.get(f"/app/activate/{token}").status_code == 404  # un solo uso

    assert _login(client, "ANA@example.com", PASSWORD) == 200
    assert client.get("/auth/me").json()["rol"] == "admin"


def test_activation_link_by_smtp(client: TestClient, monkeypatch) -> None:
    sent = []

    async def fake_send(settings, msg):
        sent.append(msg)

    monkeypatch.setattr(users_api, "send_mail", fake_send)
    app.dependency_overrides[get_settings] = lambda: Settings(
        **{**_override_settings().model_dump(), "invitation_delivery": "smtp", "smtp_host": "smtp.example.com"}
    )
    assert _login(client) == 200
    body = client.post("/app/users", json={"email": "bea@example.com", "nombre": "Bea"}).json()
    assert body["envio"] == "smtp" and body["envio_error"] is None
    assert sent[0]["To"] == "bea@example.com" and body["link"] in sent[0].get_content()


def test_admin_cannot_manage_users(client: TestClient) -> None:
    _new_admin(client, "ana@example.com")
    assert _login(client, "ana@example.com", PASSWORD) == 200
    assert client.get("/app/users").status_code == 403
    assert client.post("/app/users", json={"email": "x@example.com", "nombre": "X"}).status_code == 403
    assert client.post("/app/users/1/deactivate", json={}).status_code == 403
    # Sí ve el directorio de admins activos, para elegir co-admins.
    emails = [u["email"] for u in client.get("/app/users/directory").json()]
    assert "ana@example.com" in emails and TEST_USERNAME in emails


def test_admin_only_sees_own_projects(client: TestClient) -> None:
    _new_admin(client, "ana@example.com")
    _new_admin(client, "beto@example.com")
    assert _login(client, "ana@example.com", PASSWORD) == 200
    mine = _project(client)
    sim = _project(client, nombre="Sim", tipo="simulacion", causas=None, efectos=None)

    assert _login(client, "beto@example.com", PASSWORD) == 200
    assert client.get("/app/projects").json() == []
    pid = mine["id"]
    for method, path in [
        ("get", f"/app/projects/{pid}"), ("patch", f"/app/projects/{pid}"),
        ("get", f"/app/projects/{pid}/invitations"), ("get", f"/app/projects/{pid}/inputs"),
        ("get", f"/app/projects/{pid}/jobs"), ("delete", f"/app/projects/{pid}"),
    ]:
        kwargs = {"json": {}} if method == "patch" else {}
        assert getattr(client, method)(path, **kwargs).status_code == 404, path
    res = client.post("/app/execute_maxmin", json={
        "project_id": sim["id"], "numero_nodos": 100, "threshold": 0.5, "conectividad_promedio": 2.5, "seed": 1,
    })
    assert res.status_code == 404

    assert _login(client) == 200  # el superadmin ve todo
    assert {p["id"] for p in client.get("/app/projects").json()} == {mine["id"], sim["id"]}


def test_jobs_are_filtered_by_project(client: TestClient) -> None:
    store = app.dependency_overrides[get_job_store]()
    _new_admin(client, "ana@example.com")
    _new_admin(client, "beto@example.com")
    assert _login(client, "ana@example.com", PASSWORD) == 200
    sim = _project(client, nombre="Sim", tipo="simulacion", causas=None, efectos=None)
    ana = client.get("/auth/me").json()["id"]
    asyncio.run(store.create("501", node="cuda1", project_id=sim["id"]))
    asyncio.run(store.create("502", node="cuda1", kind="fe", user_id=ana))  # suelto, lanzado por Ana
    asyncio.run(store.create("503", node="cuda1", kind="fe"))  # suelto anterior a los usuarios

    assert sorted(j["job_id"] for j in client.get("/app/jobs").json()) == ["501", "502"]
    assert client.get("/app/jobs/503").status_code == 404

    assert _login(client, "beto@example.com", PASSWORD) == 200
    assert client.get("/app/jobs").json() == []
    assert client.get("/app/jobs/501").status_code == 404
    assert client.get("/app/maxmin/jobs/501").status_code == 404
    assert client.get("/app/fe/jobs/502/paths").status_code == 404

    assert _login(client) == 200
    assert len(client.get("/app/jobs").json()) == 3


def test_co_admins(client: TestClient) -> None:
    _new_admin(client, "ana@example.com", "Ana")
    beto = _new_admin(client, "beto@example.com", "Beto")
    pending = client.post("/app/users", json={"email": "pendiente@example.com", "nombre": "P"}).json()
    assert _login(client, "ana@example.com", PASSWORD) == 200
    pid = _project(client)["id"]

    assert client.post(f"/app/projects/{pid}/admins", json={"user_id": pending["id"]}).status_code == 400
    res = client.post(f"/app/projects/{pid}/admins", json={"user_id": beto["id"]})
    assert res.status_code == 200 and [a["email"] for a in res.json()["admins"]] == ["beto@example.com"]

    assert _login(client, "beto@example.com", PASSWORD) == 200
    project = client.get(f"/app/projects/{pid}").json()
    assert project["owner"]["nombre"] == "Ana" and project["puede_gestionar"] is False
    assert client.patch(f"/app/projects/{pid}", json={"nombre": "Retail 2"}).status_code == 200
    assert client.post(f"/app/projects/{pid}/invitations", json={"email": "e@x.cl"}).status_code == 201
    assert client.delete(f"/app/projects/{pid}").status_code == 403
    assert client.post(f"/app/projects/{pid}/admins", json={"user_id": 1}).status_code == 403
    # Un co-admin puede salir del proyecto.
    assert client.delete(f"/app/projects/{pid}/admins/{beto['id']}").status_code == 200
    assert client.get(f"/app/projects/{pid}").status_code == 404


def test_owner_transfer(client: TestClient) -> None:
    ana = _new_admin(client, "ana@example.com")
    beto = _new_admin(client, "beto@example.com")
    assert _login(client, "ana@example.com", PASSWORD) == 200
    pid = _project(client)["id"]

    assert _login(client) == 200
    res = client.post(f"/app/projects/{pid}/owner", json={"user_id": beto["id"]}).json()
    assert res["owner"]["id"] == beto["id"] and [a["id"] for a in res["admins"]] == [ana["id"]]


def test_deactivate_reassigns_and_cuts_access(client: TestClient) -> None:
    ana = _new_admin(client, "ana@example.com")
    beto = _new_admin(client, "beto@example.com")
    assert _login(client, "ana@example.com", PASSWORD) == 200
    shared = _project(client, nombre="Compartido")["id"]
    alone = _project(client, nombre="Solo")["id"]
    client.post(f"/app/projects/{shared}/admins", json={"user_id": beto["id"]})

    ana_client = TestClient(app)
    assert _login(ana_client, "ana@example.com", PASSWORD) == 200

    assert _login(client) == 200
    superadmin = client.get("/auth/me").json()["id"]
    assert client.post(f"/app/users/{superadmin}/deactivate", json={}).status_code == 400
    res = client.post(f"/app/users/{ana['id']}/deactivate", json={})
    assert res.status_code == 200, res.text
    moves = {m["project_id"]: m["owner_id"] for m in res.json()["reasignados"]}
    assert moves == {shared: beto["id"], alone: superadmin}  # co-admin si lo hay; si no, quien desactiva
    assert res.json()["usuario"]["estado"] == "inactivo"

    assert ana_client.get("/app/projects").status_code == 401  # pierde la sesión de inmediato
    assert _login(ana_client, "ana@example.com", PASSWORD) == 401
    assert client.post(f"/app/users/{ana['id']}/activation-link").status_code == 409

    assert client.post(f"/app/users/{ana['id']}/activate").json()["estado"] == "activo"
    assert _login(ana_client, "ana@example.com", PASSWORD) == 200
    assert ana_client.get("/app/projects").json() == []  # sus proyectos quedaron reasignados


def test_deactivate_with_explicit_target(client: TestClient) -> None:
    ana = _new_admin(client, "ana@example.com")
    beto = _new_admin(client, "beto@example.com")
    assert _login(client, "ana@example.com", PASSWORD) == 200
    pid = _project(client)["id"]
    assert _login(client) == 200
    assert client.post(f"/app/users/{ana['id']}/deactivate", json={"reasignar_a": ana["id"]}).status_code == 400
    res = client.post(f"/app/users/{ana['id']}/deactivate", json={"reasignar_a": beto["id"]}).json()
    assert res["reasignados"] == [{"project_id": pid, "nombre": "Retail", "owner_id": beto["id"]}]
    listed = {u["email"]: u for u in client.get("/app/users").json()}
    assert listed["beto@example.com"]["proyectos"] == 1 and listed["ana@example.com"]["estado"] == "inactivo"


def test_password_reset_link(client: TestClient) -> None:
    ana = _new_admin(client, "ana@example.com")
    body = client.post(f"/app/users/{ana['id']}/activation-link").json()
    token = body["link"].rsplit("/", 1)[1]
    assert client.post(f"/app/activate/{token}", json={"password": "otra-clave-larga"}).status_code == 200
    assert _login(client, "ana@example.com", PASSWORD) == 401
    assert _login(client, "ana@example.com", "otra-clave-larga") == 200

    assert _login(client) == 200
    superadmin = client.get("/auth/me").json()["id"]
    assert client.post(f"/app/users/{superadmin}/activation-link").status_code == 400
