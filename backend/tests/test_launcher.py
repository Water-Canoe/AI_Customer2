from __future__ import annotations

import importlib.util
import io
import json
import logging
import sys
from pathlib import Path


WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(WORKSPACE_ROOT))


def _launcher_module():
    module_path = WORKSPACE_ROOT / "packaging" / "ai_customer_launcher.py"
    spec = importlib.util.spec_from_file_location("ai_customer_launcher", module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
    log_path = launcher.configure_persistent_logging(tmp_path)
    handlers = [
        handler
        for handler in logging.getLogger().handlers
        if getattr(handler, "baseFilename", "") == str(log_path)
    ]
    try:
        logging.getLogger("ai-customer-test").warning("persistent-log-proof")
        for handler in handlers:
            handler.flush()
        assert "persistent-log-proof" in log_path.read_text(encoding="utf-8")
    finally:
        for logger in (logging.getLogger(), logging.getLogger("uvicorn.error"), logging.getLogger("uvicorn.access")):
            for handler in handlers:
                logger.removeHandler(handler)
        for handler in handlers:
            handler.close()
