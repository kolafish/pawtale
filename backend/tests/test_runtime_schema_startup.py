import asyncio
from types import SimpleNamespace

import app.main as app_main


async def test_lifespan_asserts_runtime_schema_before_serving(monkeypatch):
    asserted_engines = []

    async def fake_assert_runtime_schema(engine):
        asserted_engines.append(engine)

    monkeypatch.setattr(app_main, "assert_runtime_schema", fake_assert_runtime_schema)
    monkeypatch.setattr(app_main, "validate_runtime_settings", lambda: None)
    monkeypatch.setattr(app_main, "register_memoir_editing_job_handlers", lambda: None)
    monkeypatch.setattr(
        app_main,
        "get_settings",
        lambda: SimpleNamespace(
            memoir_editing_worker_enabled=False,
            memoir_share_video_worker_enabled=False,
        ),
    )

    async with app_main.lifespan(app_main.app):
        pass

    assert asserted_engines == [app_main.engine]


async def test_lifespan_runs_both_persistent_job_workers(monkeypatch):
    started_workers: list[str] = []

    async def fake_assert_runtime_schema(_engine):
        return None

    async def fake_worker(name, stop_event):
        started_workers.append(name)
        await stop_event.wait()

    monkeypatch.setattr(app_main, "assert_runtime_schema", fake_assert_runtime_schema)
    monkeypatch.setattr(app_main, "validate_runtime_settings", lambda: None)
    monkeypatch.setattr(app_main, "register_memoir_editing_job_handlers", lambda: None)
    monkeypatch.setattr(
        app_main,
        "editing_worker_loop",
        lambda stop_event: fake_worker("editing", stop_event),
    )
    monkeypatch.setattr(
        app_main,
        "share_video_worker_loop",
        lambda stop_event: fake_worker("share_video", stop_event),
    )
    monkeypatch.setattr(
        app_main,
        "get_settings",
        lambda: SimpleNamespace(
            memoir_editing_worker_enabled=True,
            memoir_share_video_worker_enabled=True,
        ),
    )

    async with app_main.lifespan(app_main.app):
        await asyncio.sleep(0)
        assert set(started_workers) == {"editing", "share_video"}
