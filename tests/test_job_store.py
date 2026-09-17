import asyncio

import pytest

from app.services.job_store import JobNotFoundError, JobStore


@pytest.fixture
def store() -> JobStore:
    return JobStore()


def test_create_sets_pending_status(store: JobStore) -> None:
    record = asyncio.run(store.create("123", node="cuda1"))
    assert record.job_id == "123"
    assert record.status == "pending"
    assert record.node == "cuda1"
    assert record.created_at == record.updated_at


def test_get_unknown_job_raises(store: JobStore) -> None:
    with pytest.raises(JobNotFoundError):
        asyncio.run(store.get("does-not-exist"))


def test_update_from_callback_unknown_job_raises(store: JobStore) -> None:
    with pytest.raises(JobNotFoundError):
        asyncio.run(store.update_from_callback("does-not-exist", status="success"))


def test_update_from_callback_success_updates_fields(store: JobStore) -> None:
    async def _run():
        created = await store.create("123", node="cuda1")
        updated = await store.update_from_callback(
            "123", status="success", effective_order=3, computation_time_s=0.42, logs="log contents"
        )
        fetched = await store.get("123")
        return created, updated, fetched

    created, updated, fetched = asyncio.run(_run())
    assert updated.status == "success"
    assert updated.effective_order == 3
    assert updated.computation_time_s == 0.42
    assert updated.logs == "log contents"
    assert updated.updated_at >= created.updated_at
    assert fetched is updated


def test_update_from_callback_error_status(store: JobStore) -> None:
    async def _run():
        await store.create("123", node="cuda1")
        return await store.update_from_callback("123", status="error", logs=None)

    updated = asyncio.run(_run())
    assert updated.status == "error"
    assert updated.effective_order is None
    assert updated.logs is None


def test_mark_error_creates_error_record(store: JobStore) -> None:
    async def _run():
        record = await store.mark_error("123", node="cuda1", logs="notifier no se pudo lanzar")
        fetched = await store.get("123")
        return record, fetched

    record, fetched = asyncio.run(_run())
    assert record.status == "error"
    assert record.logs == "notifier no se pudo lanzar"
    assert fetched.status == "error"


def test_concurrent_creates_do_not_corrupt_state(store: JobStore) -> None:
    async def _run():
        await asyncio.gather(*[store.create(str(i), node="cuda1") for i in range(20)])
        return [await store.get(str(i)) for i in range(20)]

    records = asyncio.run(_run())
    for i, record in enumerate(records):
        assert record.job_id == str(i)
