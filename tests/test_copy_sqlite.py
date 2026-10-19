import asyncio
import sqlite3
from datetime import timedelta
from pathlib import Path

import pytest

from app.db.copy_sqlite import CopyError, copy_database
from app.services.job_store import JobStore
from app.services.project_store import ProjectStore


def _legacy_db(path: Path) -> Path:
    """Base SQLite con el formato que escribía la app antes de SQLAlchemy (texto ISO y JSON)."""
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE jobs (job_id TEXT PRIMARY KEY, status TEXT NOT NULL, node TEXT NOT NULL,
            token TEXT NOT NULL, effective_order INTEGER, computation_time_s REAL, logs TEXT,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            kind TEXT NOT NULL DEFAULT 'maxmin', params TEXT, result TEXT,
            project_id INTEGER, input_id INTEGER);
        CREATE TABLE projects (id INTEGER PRIMARY KEY AUTOINCREMENT, nombre TEXT NOT NULL,
            descripcion TEXT NOT NULL DEFAULT '', tipo TEXT NOT NULL, estado TEXT NOT NULL,
            owner TEXT, causas TEXT, efectos TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE expert_invitations (id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL REFERENCES projects(id), email TEXT NOT NULL,
            token_hash TEXT NOT NULL UNIQUE, expira_at TEXT NOT NULL, usado_at TEXT,
            revocado_at TEXT, enviado_at TEXT, envio_error TEXT, created_at TEXT NOT NULL);
        CREATE TABLE expert_inputs (id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL REFERENCES projects(id),
            invitation_id INTEGER NOT NULL UNIQUE REFERENCES expert_invitations(id),
            cc TEXT NOT NULL, ce TEXT NOT NULL, ee TEXT NOT NULL,
            excluido INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL);
        INSERT INTO projects VALUES (7, 'Expertos', '', 'expertos', 'abierto', 'admin',
            '["a","b"]', '["x"]', '2026-01-01T00:00:00+00:00', '2026-01-02T00:00:00+00:00');
        INSERT INTO expert_invitations VALUES (3, 7, 'e@x.cl', 'hash', '2026-01-15T00:00:00+00:00',
            '2026-01-03T00:00:00+00:00', NULL, NULL, NULL, '2026-01-01T00:00:00+00:00');
        INSERT INTO expert_inputs VALUES (5, 7, 3, '[[1,0.5],[0.2,1]]', '[[0.9],[0.1]]', '[[1]]', 1,
            '2026-01-03T00:00:00+00:00');
        INSERT INTO jobs VALUES ('100', 'success', 'cuda1', 't1', NULL, 1.5, 'log', '2026-01-04T00:00:00+00:00',
            '2026-01-04T00:00:00+00:00', 'fe', '{"thr": 0.5}', '{"orders": [2]}', 7, 5);
        INSERT INTO jobs VALUES ('101', 'success', 'cuda2', 't2', 4, 0.3, NULL, '2025-12-01T00:00:00+00:00',
            '2025-12-01T00:00:00+00:00', 'maxmin', NULL, NULL, NULL, NULL);
        """
    )
    conn.commit()
    conn.close()
    return path


def test_copies_legacy_sqlite(tmp_path: Path, database: str) -> None:
    source = _legacy_db(tmp_path / "old.db")
    before = source.read_bytes()

    copied = asyncio.run(copy_database(source, database))

    assert copied == {"projects": 1, "jobs": 2, "expert_invitations": 1, "expert_inputs": 1}
    assert source.read_bytes() == before  # el origen no se toca

    projects = ProjectStore(database)
    expert = asyncio.run(projects.get_project(7))
    assert expert.causas == ["a", "b"] and expert.efectos == ["x"]
    assert expert.created_at.isoformat() == "2026-01-01T00:00:00+00:00"
    invitation = asyncio.run(projects.get_invitation(7, 3))
    assert invitation.state() == "usada"
    item = asyncio.run(projects.get_input(7, 5))
    assert item.cc == [[1, 0.5], [0.2, 1]] and item.excluido is True

    jobs = JobStore(database)
    fe = asyncio.run(jobs.get("100"))
    assert fe.params == {"thr": 0.5} and fe.result == {"orders": [2]} and fe.input_id == 5
    # El job MaxMin sin proyecto pasa al proyecto de jobs anteriores (RF-07).
    assert asyncio.run(jobs.get("101")).project_id not in (None, 7)

    # Los ids nuevos siguen después de los copiados.
    new = asyncio.run(
        projects.create_project(nombre="Nuevo", descripcion="", tipo="simulacion", owner=None)
    )
    assert new.id > 7
    _, token = asyncio.run(projects.create_invitation(7, "f@x.cl", timedelta(days=1)))
    assert asyncio.run(projects.submit_input(token, [[1]], [[1]], [[1]])).id > 5


def test_refuses_non_empty_target(tmp_path: Path, database: str) -> None:
    asyncio.run(JobStore(database).create("1", node="cuda1"))

    with pytest.raises(CopyError, match="ya tiene datos"):
        asyncio.run(copy_database(_legacy_db(tmp_path / "old.db"), database))
