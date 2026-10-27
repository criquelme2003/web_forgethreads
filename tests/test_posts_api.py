"""Blog: publicaciones con cálculos del proyecto, instantánea pública y acceso (RF-56 a RF-58)."""

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.api.fe as fe_api
import app.api.sweeps as sweeps_api
from app.api.deps import (
    get_job_store,
    get_node_selector,
    get_post_store,
    get_project_store,
    get_ssh_pool,
    get_user_store,
)
from app.core.config import get_settings
from app.main import app
from app.services.job_store import JobStore
from app.services.post_store import PostStore, slugify
from app.services.project_store import ProjectStore
from app.services.user_store import UserStore
from tests.conftest import TEST_PASSWORD, TEST_USERNAME
from tests.test_projects_api import EXPERT_PROJECT, FakePool, FakeSelector, _settings
from tests.test_users_api import PASSWORD

PATHS_CSV = (
    "From,Through1,To,Count,Mean,SD\n"
    "a,b,e1,1,0.5,0.0\n"
    "b,a,e2,1,0.75,0.0\n"
    "a,b,e3,1,0.9,0.0\n"
)
RAW_SWEEP = "c,n,repeticion,orden_efectivo\n0.5,100,0,3\n0.5,100,1,5\n"


@pytest.fixture
def env(tmp_path: Path, database: str, monkeypatch):
    pool = FakePool()
    store, projects = JobStore(database), ProjectStore(database)
    users, posts = UserStore(database), PostStore(database)
    downloads: list[str] = []

    async def fake_fe_download(pool_, node, scripts_wf_dir, job_id, orders, dest_dir):
        downloads.append(job_id)
        dest_dir.mkdir(parents=True, exist_ok=True)
        for order in orders:
            (dest_dir / f"paths_order_{order}.csv").write_text(PATHS_CSV)

    async def fake_sweep_download(pool_, node, scripts_wf_dir, job_id, dest):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(RAW_SWEEP)

    monkeypatch.setattr(fe_api, "download_fe_results", fake_fe_download)
    monkeypatch.setattr(sweeps_api, "download_sweep_results", fake_sweep_download)
    settings = _settings(tmp_path, sweep_results_dir=str(tmp_path / "sweeps"))
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_job_store] = lambda: store
    app.dependency_overrides[get_project_store] = lambda: projects
    app.dependency_overrides[get_user_store] = lambda: users
    app.dependency_overrides[get_post_store] = lambda: posts
    app.dependency_overrides[get_ssh_pool] = lambda: pool
    app.dependency_overrides[get_node_selector] = lambda: FakeSelector()
    with TestClient(app) as client:
        assert _login(client) == 200
        yield {"client": client, "store": store, "projects": projects, "downloads": downloads}
    app.dependency_overrides.clear()


def _login(client: TestClient, username: str = TEST_USERNAME, password: str = TEST_PASSWORD) -> int:
    return client.post("/auth/login", json={"username": username, "password": password}).status_code


def _expert_project(env) -> tuple[dict, int]:
    """Proyecto de expertos abierto con un input de «Ana Pérez» y un cálculo terminado."""
    client = env["client"]
    project = client.post("/app/projects", json=EXPERT_PROJECT).json()
    client.post(f"/app/projects/{project['id']}/estado", json={"estado": "abierto"})
    res = client.post(
        f"/app/projects/{project['id']}/invitations", json={"email": "ana@example.com", "nombre": "Ana Pérez"}
    )
    token = res.json()["link"].rsplit("/", 1)[1]
    matrix = [[0.0, 0.5], [0.5, 0.0]]
    item = asyncio.run(env["projects"].submit_input(token, matrix, [[0.5] * 3] * 2, [[0.0] * 3] * 3))
    _finished_job(
        env, "501", "fe", project["id"], input_id=item.id,
        params={"thr": 0.5, "maxorder": 3, "reps": 1, "causes": ["a", "b"], "effects": ["e1", "e2", "e3"]},
        result={"k": 1, "orders": [2], "rows_per_order": {"2": 3}},
    )
    return project, item.id


def _finished_job(env, job_id: str, kind: str, project_id: int, status: str = "success", **fields) -> None:
    store = env["store"]
    result = fields.pop("result", None)
    logs = fields.pop("logs", "")
    asyncio.run(store.create(job_id, "cuda1", kind=kind, project_id=project_id, **fields))
    asyncio.run(store.update_from_callback(job_id, status=status, logs=logs, result=result, effective_order=4))


def _fig(ref: str, job_id: str, vista: str, publico: bool = True, **extra) -> dict:
    return {"ref": ref, "job_id": job_id, "vista": vista, "publico": publico, **extra}


def _text(markdown: str) -> dict:
    return {"tipo": "texto", "markdown": markdown}


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/app/posts"),
        ("post", "/app/posts"),
        ("get", "/app/posts/1"),
        ("patch", "/app/posts/1"),
        ("delete", "/app/posts/1"),
        ("get", "/app/posts/1/preview"),
        ("post", "/app/posts/1/publish"),
        ("post", "/app/posts/1/unpublish"),
    ],
)
def test_post_routes_require_auth(client: TestClient, method: str, path: str) -> None:
    assert getattr(client, method)(path).status_code == 401


def test_slugify() -> None:
    assert slugify("Efectos olvidados en Retail: ¿qué aprendimos?") == "efectos-olvidados-en-retail-que-aprendimos"
    assert slugify("¡¡!!") == "publicacion"


def test_template_is_a_scientific_article(env) -> None:
    client = env["client"]
    project, _ = _expert_project(env)
    res = client.post("/app/posts", json={"titulo": "Retail", "project_id": project["id"]})
    assert res.status_code == 201, res.text
    post = res.json()
    assert post["slug"] == "retail" and post["estado"] == "borrador"
    assert post["proyecto"] == {"id": project["id"], "nombre": "Retail", "tipo": "expertos"}
    headings = [b["markdown"].split("\n", 1)[0] for b in post["bloques"]]
    assert headings == ["## Introducción", "## Metodología", "## Resultados", "## Discusión",
                        "## Conclusiones", "## Referencias"]
    assert post["bloques"][0]["markdown"].endswith(EXPERT_PROJECT["descripcion"])
    assert "{{fig:f1}}" in post["bloques"][2]["markdown"]
    # El último cálculo queda como figura citada, pero sin permiso de publicación.
    assert post["figuras"] == [{
        "ref": "f1", "job_id": "501", "vista": "sankey", "top": 50, "escala": "lineal", "pie": "",
        "publico": False, "descargar_entradas": False, "descargar_resultados": False, "en_cuerpo": False,
    }]
    assert post["url"] == "http://testserver/front/blog/retail"
    res = client.post(f"/app/posts/{post['id']}/publish")
    assert res.status_code == 422 and "permiso" in res.json()["detail"]

    # Varias publicaciones por proyecto; la dirección no choca.
    second = client.post("/app/posts", json={"titulo": "Retail", "project_id": project["id"]}).json()
    assert second["slug"] == "retail-2"
    assert len(client.get(f"/app/posts?project_id={project['id']}").json()) == 2


def test_publish_snapshot_and_public_read(env) -> None:
    client = env["client"]
    project, _ = _expert_project(env)
    post = client.post("/app/posts", json={"titulo": "Hallazgos", "project_id": project["id"], "plantilla": False}).json()
    body = {
        "resumen": "Lo que encontramos",
        "autores": "A. Pérez, B. Soto",
        "palabras_clave": ["efectos olvidados", " retail ", "retail"],
        "bloques": [
            _text("## Resultados\n\nEl camino **a → b → e3** domina ({{fig:hive}}); ver también {{fig:sk}}."),
            _text("Repetimos la cita {{fig:hive}}."),
        ],
        "figuras": [
            _fig("sk", "501", "sankey", orden=2, origen="a", top=5, pie="Desde la causa a",
                 descargar_resultados=True, descargar_entradas=True),
            _fig("hive", "501", "hive", en_cuerpo=True),
            _fig("oculta", "501", "tabla"),  # con permiso pero sin cita: no se publica
            _fig("privada", "501", "tabla", publico=False),
        ],
    }
    res = client.patch(f"/app/posts/{post['id']}", json=body)
    assert res.status_code == 200, res.text
    assert res.json()["palabras_clave"] == ["efectos olvidados", "retail"]

    # Borrador: no es público.
    client.cookies.clear()
    assert client.get("/app/blog").json() == []
    assert client.get("/app/blog/hallazgos").status_code == 404
    assert client.get("/app/blog/hallazgos/figuras/1/resultados.csv").status_code == 404
    assert _login(client) == 200

    preview = client.get(f"/app/posts/{post['id']}/preview").json()
    assert any("«oculta» no se cita" in a for a in preview["avisos"])
    res = client.post(f"/app/posts/{post['id']}/publish")
    assert res.status_code == 200, res.text
    assert res.json()["estado"] == "publicado" and not res.json()["cambios_sin_publicar"]

    client.cookies.clear()
    listing = client.get("/app/blog").json()
    assert [(p["slug"], p["graficos"], p["autores"], p["proyecto"]) for p in listing] == [
        ("hallazgos", 2, "A. Pérez, B. Soto", {"nombre": "Retail", "tipo": "expertos"})
    ]
    public = client.get("/app/blog/hallazgos")
    assert public.status_code == 200
    data = public.json()
    assert data["parametros"] == {"thr": [0.5], "maxorder": [3], "reps": [], "expertos": 1}
    assert data["ficha"]["expertos"] == 1 and data["ficha"]["causas"] == ["a", "b"]
    assert data["avisos"] == [] and "_privado" not in data
    # Numeradas por orden de primera cita; solo las citadas y con permiso.
    hive, sankey = data["figuras"]
    assert (hive["numero"], hive["ref"], sankey["numero"], sankey["ref"]) == (1, "hive", 2, "sk")
    assert hive["descargas"] == [] and sankey["descargas"] == ["entradas", "resultados"]
    assert hive["en_cuerpo"] is True and sankey["en_cuerpo"] is False
    # El gráfico recibe solo lo que dibuja: origen filtrado, ordenado por Mean, sin Count ni SD.
    table = sankey["datos"]["tables"][0]
    assert sankey["orden"] == 2 and sankey["origen"] == "a" and sankey["pie"] == "Desde la causa a"
    assert table["columns"] == ["From", "Through1", "To", "Mean"]
    assert table["rows"] == [["a", "b", "e3", 0.9], ["a", "b", "e1", 0.5]] and table["total"] == 2
    assert len(hive["datos"]["tables"][0]["rows"]) == 3
    # Expertos anonimizados por defecto: ni nombre ni correo en lo público (RF-58), ni el job.
    assert sankey["meta"]["expertos"] == ["Experto 1"]
    assert "ana@example.com" not in public.text and "Ana" not in public.text
    assert "501" not in public.text

    # Descargas: solo las permitidas.
    res = client.get("/app/blog/hallazgos/figuras/2/resultados.csv")
    assert res.status_code == 200, res.text
    assert res.text.splitlines() == [
        "orden,origen,intermedios,destino,Mean", "2,a,b,e1,0.5", "2,b,a,e2,0.75", "2,a,b,e3,0.9",
    ]
    entradas = client.get("/app/blog/hallazgos/figuras/2/entradas.csv").text.splitlines()
    assert entradas[0] == "experto,matriz,fila,columna,valor"
    assert entradas[1] == "Experto 1,CC,a,a,0.0" and len(entradas) == 1 + 4 + 6 + 9
    assert client.get("/app/blog/hallazgos/figuras/1/resultados.csv").status_code == 404
    assert client.get("/app/blog/hallazgos/figuras/9/entradas.csv").status_code == 404

    # Editar el borrador no cambia lo publicado hasta volver a publicar (RF-57).
    assert _login(client) == 200
    res = client.patch(f"/app/posts/{post['id']}", json={"titulo": "Otro título", "anonimizar": False})
    assert res.json()["cambios_sin_publicar"]
    assert client.get("/app/blog/hallazgos").json()["titulo"] == "Hallazgos"
    client.post(f"/app/posts/{post['id']}/publish")
    data = client.get("/app/blog/hallazgos").json()
    assert data["titulo"] == "Otro título"
    assert data["figuras"][1]["meta"]["expertos"] == ["Ana Pérez"]
    assert "ana@example.com" not in str(data)
    assert client.get("/app/blog/hallazgos/figuras/2/entradas.csv").text.splitlines()[1].startswith("Ana Pérez,")

    # Retirarla corta el acceso público, también a las descargas.
    assert client.post(f"/app/posts/{post['id']}/unpublish").json()["estado"] == "borrador"
    assert client.get("/app/blog/hallazgos").status_code == 404
    assert client.get("/app/blog/hallazgos/figuras/2/resultados.csv").status_code == 404
    assert env["downloads"] == ["501"]  # los caminos se descargaron del nodo una sola vez


def test_maxmin_and_sweep_figures(env) -> None:
    client = env["client"]
    sim = client.post("/app/projects", json={"nombre": "Sim", "tipo": "simulacion"}).json()
    log = "[MAXMIN C++] Efectos encontrados en orden 1 : 20\n[MAXMIN C++] Efectos encontrados en orden 2 : 7\n"
    _finished_job(env, "601", "maxmin", sim["id"], logs=log,
                  params={"numero_nodos": 100, "conectividad_promedio": 2.5, "threshold": 0.4})
    _finished_job(env, "602", "sweep", sim["id"], status="partial",
                  params={"ns": [100], "cs": [0.5], "reps": 2, "thr": 0.5})
    post = client.post("/app/posts", json={"titulo": "Simulaciones", "project_id": sim["id"]}).json()
    # La plantilla usa el barrido (resume más simulaciones que un MaxMin suelto).
    assert [f["job_id"] for f in post["figuras"]] == ["602"]

    body = {
        "bloques": [_text("Ver {{fig:m}} y {{fig:s}}.")],
        "figuras": [
            _fig("m", "601", "efectos", escala="log", descargar_entradas=True, descargar_resultados=True),
            _fig("s", "602", "por_c", descargar_resultados=True),
        ],
    }
    assert client.patch(f"/app/posts/{post['id']}", json=body).status_code == 200
    preview = client.get(f"/app/posts/{post['id']}/preview").json()
    efectos, barrido = preview["figuras"]
    assert efectos["datos"]["effects_by_order"] == [{"order": 1, "count": 20}, {"order": 2, "count": 7}]
    assert efectos["escala"] == "log" and efectos["meta"]["n"] == 100
    stats = barrido["datos"]["estadisticas"]
    assert [(s["n"], s["c"], s["media"], s["repeticiones"]) for s in stats] == [(100, 0.5, 4.0, 2)]
    assert barrido["meta"]["parcial"] is True
    assert preview["parametros"]["thr"] == [0.4, 0.5]

    assert client.post(f"/app/posts/{post['id']}/publish").status_code == 200
    client.cookies.clear()
    base = "/app/blog/simulaciones/figuras"
    assert client.get(f"{base}/1/resultados.csv").text == "orden,efectos\n1,20\n2,7\n"
    assert "numero_nodos,100" in client.get(f"{base}/1/entradas.csv").text
    assert client.get(f"{base}/2/resultados.csv").text == RAW_SWEEP
    assert client.get(f"{base}/2/entradas.csv").status_code == 404


def test_invalid_figures(env) -> None:
    client = env["client"]
    project, _ = _expert_project(env)
    post = client.post("/app/posts", json={"titulo": "P", "project_id": project["id"], "plantilla": False}).json()
    client.post("/app/posts", json={"titulo": "Suelta", "plantilla": False})

    def patch(**body):
        return client.patch(f"/app/posts/{post['id']}", json=body)

    assert patch(figuras=[_fig("a", "999", "sankey")]).status_code == 422
    res = patch(figuras=[_fig("a", "501", "efectos")])
    assert res.status_code == 422 and res.json()["detail"].startswith("Figura «a»")
    assert patch(figuras=[_fig("a", "501", "sankey"), _fig("a", "501", "hive")]).status_code == 422
    assert patch(figuras=[_fig("Mal Ref", "501", "sankey")]).status_code == 422
    assert patch(slug="No Vale").status_code == 422
    assert patch(slug="suelta").status_code == 409

    # Citar una figura que no existe, o un orden que el cálculo no tiene, se detecta al publicar.
    assert patch(bloques=[_text("Ver {{fig:x}}")]).status_code == 200
    res = client.post(f"/app/posts/{post['id']}/publish")
    assert res.status_code == 422 and "no hay una figura «x»" in res.json()["detail"]
    assert patch(bloques=[_text("Ver {{fig:t}}")], figuras=[_fig("t", "501", "tabla", orden=7)]).status_code == 200
    res = client.post(f"/app/posts/{post['id']}/publish")
    assert res.status_code == 422 and "orden 7" in res.json()["detail"]
    assert client.get(f"/app/posts/{post['id']}").json()["estado"] == "borrador"


def test_access_follows_project_membership(env) -> None:
    client = env["client"]
    project, _ = _expert_project(env)
    post = client.post("/app/posts", json={"titulo": "Del proyecto", "project_id": project["id"]}).json()
    res = client.post("/app/users", json={"email": "beto@example.com", "nombre": "Beto"})
    token = res.json()["link"].rsplit("/", 1)[1]
    assert client.post(f"/app/activate/{token}", json={"password": PASSWORD}).status_code == 200
    beto_id = res.json()["id"]

    assert _login(client, "beto@example.com", PASSWORD) == 200
    assert client.get("/app/posts").json() == []
    assert client.get(f"/app/posts/{post['id']}").status_code == 404
    assert client.post("/app/posts", json={"titulo": "x", "project_id": project["id"]}).status_code == 404
    # Tampoco puede usar cálculos de un proyecto ajeno en su propia publicación.
    own = client.post("/app/posts", json={"titulo": "Mía"}).json()
    assert own["autores"] == "Beto"
    res = client.patch(f"/app/posts/{own['id']}", json={"figuras": [_fig("a", "501", "sankey")]})
    assert res.status_code == 422

    # Como co-admin del proyecto la edita y crea más publicaciones del proyecto.
    assert _login(client) == 200
    client.post(f"/app/projects/{project['id']}/admins", json={"user_id": beto_id})
    assert _login(client, "beto@example.com", PASSWORD) == 200
    assert client.post("/app/posts", json={"titulo": "Otra", "project_id": project["id"]}).status_code == 201
    assert {p["titulo"] for p in client.get("/app/posts").json()} == {"Del proyecto", "Mía", "Otra"}
    assert client.delete(f"/app/posts/{post['id']}").status_code == 204


def test_figure_preview_in_editor(env) -> None:
    client = env["client"]
    project, _ = _expert_project(env)
    post = client.post("/app/posts", json={"titulo": "P", "project_id": project["id"], "plantilla": False}).json()
    # La figura aún no está guardada ni tiene permiso: el editor la previsualiza igual.
    res = client.post(f"/app/posts/{post['id']}/figure-preview", json=_fig("x", "501", "tabla", publico=False, top=5))
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["kind"] == "fe" and body["datos"]["tables"][0]["total"] == 3
    assert client.post(f"/app/posts/{post['id']}/figure-preview", json=_fig("x", "999", "tabla")).status_code == 422
    client.cookies.clear()
    assert client.post(f"/app/posts/{post['id']}/figure-preview", json=_fig("x", "501", "tabla")).status_code == 401
