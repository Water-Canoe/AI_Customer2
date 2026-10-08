from __future__ import annotations

import importlib.util
import io
import json
import logging
import sys
from pathlib import Path

import pytest


WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(WORKSPACE_ROOT))


def _launcher_module():
    module_path = WORKSPACE_ROOT / "packaging" / "ai_customer_launcher.py"
    spec = importlib.util.spec_from_file_location("ai_customer_launcher", module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_smoke_test_uses_empty_database_without_starting_business_workers(tmp_path, monkeypatch) -> None:
    launcher = _launcher_module()
    monkeypatch.setenv("AI_CUSTOMER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AI_CUSTOMER_DB", str(tmp_path / "empty.sqlite3"))
    monkeypatch.delenv("AI_CUSTOMER_PACKAGED", raising=False)
    from app import database
    from app.services import automation_workbench, job_queue, message_inbox

    def forbidden(*_args, **_kwargs):
        pytest.fail("自检不得启动业务、浏览器或真实账号连接")

    monkeypatch.setattr(automation_workbench, "start_scheduler", forbidden)
    monkeypatch.setattr(job_queue, "start", forbidden)
    monkeypatch.setattr(message_inbox, "auto_connect_accounts", forbidden)
    launcher.smoke_test()
    assert database.schema_version()["current"] == database.schema_version()["latest"]
    with pytest.raises(RuntimeError, match="空库"):
        launcher.smoke_test()


def test_launcher_waits_for_product_health_before_opening_browser(monkeypatch) -> None:
    launcher = _launcher_module()
    responses = iter(["", "http://127.0.0.1:8000"])
    opened: list[str] = []
    monkeypatch.setattr(launcher, "workbench_url_if_ready", lambda *_: next(responses))
    monkeypatch.setattr(launcher.time, "sleep", lambda *_: None)
    monkeypatch.setattr(launcher.webbrowser, "open", opened.append)

    assert launcher.open_browser_when_ready("http://127.0.0.1:8000", 1) is True
    assert opened == ["http://127.0.0.1:8000"]


def test_launcher_health_rejects_other_local_services(monkeypatch) -> None:
    launcher = _launcher_module()
    monkeypatch.setattr(
        launcher.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: io.BytesIO(json.dumps({"status": "ok", "product": "other"}).encode()),
    )

    assert launcher.workbench_url_if_ready(8000) == ""


def test_launcher_health_rejects_development_workbench(monkeypatch) -> None:
    launcher = _launcher_module()
    monkeypatch.setattr(
        launcher.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: io.BytesIO(
            json.dumps({"status": "ok", "product": "ai-customer", "packaged": False}).encode()
        ),
    )

    assert launcher.workbench_url_if_ready(8000) == ""


def test_packaged_launcher_writes_rotating_log(tmp_path: Path) -> None:
    launcher = _launcher_module()
    logger_names = ("", "uvicorn", "uvicorn.error", "uvicorn.access")
    loggers = {name: logging.getLogger(name) for name in logger_names}
    original = {
        name: (list(logger.handlers), logger.level, logger.propagate)
        for name, logger in loggers.items()
    }
    file_handlers: set[logging.Handler] = set()
    try:
        launcher.uvicorn.Config("app.main:app")
        log_path = launcher.configure_persistent_logging(tmp_path)
        file_handlers = {
            handler
            for logger in loggers.values()
            for handler in logger.handlers
            if getattr(handler, "baseFilename", "") == str(log_path)
        }
        assert file_handlers
        for name in ("uvicorn", "uvicorn.access"):
            assert loggers[name].handlers == list(file_handlers)

        logging.getLogger("ai-customer-test").info("application-log-proof")
        logging.getLogger("uvicorn.error").error("server-log-proof")
        logging.getLogger("uvicorn.access").info("access-log-proof")
        for handler in file_handlers:
            handler.flush()
        content = log_path.read_text(encoding="utf-8")
        assert "application-log-proof" in content
        assert "server-log-proof" in content
        assert "access-log-proof" in content
    finally:
        for name, logger in loggers.items():
            handlers, level, propagate = original[name]
            logger.handlers[:] = handlers
            logger.setLevel(level)
            logger.propagate = propagate
        for handler in file_handlers:
            handler.close()
