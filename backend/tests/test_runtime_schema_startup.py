from types import SimpleNamespace

import app.main as app_main


async def test_lifespan_asserts_runtime_schema_before_serving(monkeypatch):
    asserted_engines = []

    async def fake_assert_runtime_schema(engine):
        asserted_engines.append(engine)

    monkeypatch.setattr(app_main, "assert_runtime_schema", fake_assert_runtime_schema)
    monkeypatch.setattr(app_main, "validate_runtime_settings", lambda: None)
    monkeypatch.setattr(app_main, "register_memoir_editing_job_handlers", lambda: None)
    monkeypatch.setattr(app_main, "get_settings", lambda: SimpleNamespace(memoir_editing_worker_enabled=False))

    async with app_main.lifespan(app_main.app):
        pass

    assert asserted_engines == [app_main.engine]
