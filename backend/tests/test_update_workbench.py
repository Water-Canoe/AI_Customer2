from __future__ import annotations

import asyncio
import json
import threading
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import database
from app.main import app
from app.services import data_management, desktop_update, message_inbox, profile_manager, update_workbench


@pytest.fixture
def update_project(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_CUSTOMER_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("AI_CUSTOMER_DB", str(tmp_path / "data" / "ai_customer.sqlite3"))
    monkeypatch.setenv("AI_CUSTOMER_PACKAGED", "1")
    monkeypatch.setenv("AI_CUSTOMER_INSTALL_ROOT", str(tmp_path))
    (tmp_path / "AI_Customer.exe").write_bytes(b"entry")
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "browser.exe").write_bytes(b"fixture")
    (tmp_path / "release-manifest.json").write_text(json.dumps({"environment_version": "1.1.0"}))
    (runtime / "environment-manifest.json").write_text(json.dumps({
        "format": 1, "product": "AI Customer Environment", "version": "1.1.0", "required_paths": ["browser.exe"],
    }))
    database.init_db()
    monkeypatch.setattr(update_workbench, "_checked", {})
    monkeypatch.setattr(update_workbench, "_state", {"phase": "idle", "percent": 0, "message": ""})
    monkeypatch.setattr(update_workbench, "_task", None)
    monkeypatch.setattr(update_workbench, "_requests", 0)
    monkeypatch.setattr(update_workbench, "_stop", threading.Event())
    monkeypatch.setattr(desktop_update, "prepared_update", lambda _: {"ready": False, "reason": "尚未下载更新"})
    monkeypatch.setattr(profile_manager, "status", lambda: {})
    monkeypatch.setattr(message_inbox, "_listeners", {})
    monkeypatch.setattr(message_inbox, "_send_locks", {})
    monkeypatch.setattr(message_inbox, "_profile_locks", {})
    monkeypatch.setattr(app.state, "uvicorn_server", SimpleNamespace(should_exit=False), raising=False)
    return TestClient(app)


def test_check_does_not_stop_application_or_require_disconnecting_receivers(update_project, monkeypatch):
    def cannot_require_idle(*_args, **_kwargs):
        pytest.fail("在线检查和下载不应要求业务或私信接收空闲")

    monkeypatch.setattr(data_management, "ensure_idle", cannot_require_idle)
    monkeypatch.setattr(desktop_update, "check_remote_update", lambda _: {
        "available": True, "can_prepare": True, "version": "1.3.0", "current_version": "1.2.15",
        "_offer": {"downloadUrl": "secret-signed-url", "signature": "signature"},
    })
    response = update_project.post("/api/system/check-update")
    assert response.status_code == 200
    assert response.json()["version"] == "1.3.0"
    assert "secret" not in response.text and "signature" not in response.text
    assert app.state.uvicorn_server.should_exit is False
    monkeypatch.delenv("AI_CUSTOMER_PACKAGED")
    assert update_project.post("/api/system/check-update").status_code == 400
    assert app.state.uvicorn_server.should_exit is False


def test_prepare_runs_in_background_and_shutdown_cancels_before_ready(update_project, monkeypatch):
    started, release = threading.Event(), threading.Event()
    monkeypatch.setattr(update_workbench, "_checked", {"can_prepare": True, "_offer": {}})

    def download(_root, _checked, progress):
        started.set()
        assert release.wait(3)
        progress("下载中", 50)
        pytest.fail("收到退出信号后不得继续写入准备完成状态")

    monkeypatch.setattr(desktop_update, "prepare_remote_update", download)

    async def flow():
        result = await update_workbench.prepare()
        assert result["phase"] == "downloading"
        assert await asyncio.to_thread(started.wait, 3)
        with pytest.raises(ValueError, match="正在执行"):
            await update_workbench.prepare()
        update_workbench._stop.set()
        release.set()
        await update_workbench.shutdown()
        assert update_workbench.status()["phase"] == "failed"
        assert "取消" in update_workbench.status()["message"]

    asyncio.run(flow())


def test_install_refuses_work_in_progress_but_closes_only_receivers_after_confirmation(update_project, monkeypatch):
    calls = []
    monkeypatch.setattr(desktop_update, "prepared_update", lambda _: {"ready": True, "version": "1.3.0", "size": 123})
    monkeypatch.setattr(desktop_update, "start_prepared_update", lambda *_: calls.append("launch"))
    scheduler = update_workbench.automation_workbench
    running = [True]
    monkeypatch.setattr(scheduler, "scheduler_running", lambda: running[0])
    monkeypatch.setattr(scheduler, "stop_scheduler", lambda: (calls.append("stop"), running.__setitem__(0, False)))
    monkeypatch.setattr(scheduler, "start_scheduler", lambda: (calls.append("resume"), running.__setitem__(0, True)))
    monkeypatch.setattr(data_management, "active_jobs", lambda: [{"table": "runtime_jobs", "count": 1}])
    assert update_project.post("/api/system/install-update").status_code == 400
    assert calls == ["stop", "resume"]
    assert app.state.uvicorn_server.should_exit is False

    monkeypatch.setattr(data_management, "active_jobs", lambda: [])
    send_lock = threading.Lock()
    with send_lock:
        message_inbox._send_locks["account"] = send_lock
        assert update_project.post("/api/system/install-update").status_code == 400
        assert "launch" not in calls
    message_inbox._listeners["account"] = SimpleNamespace(state="connected", thread=SimpleNamespace(is_alive=lambda: True))
    assert message_inbox.active_accounts() == ["account"]
    assert message_inbox.busy_accounts() == []
    response = update_project.post("/api/system/install-update")
    assert response.status_code == 200
    assert calls[-2:] == ["stop", "launch"]
    assert app.state.uvicorn_server.should_exit is True
    assert update_project.get("/api/settings").status_code == 503
    assert update_project.get("/api/health").status_code == 200


def test_failed_install_launch_keeps_application_and_scheduler_running(update_project, monkeypatch):
    monkeypatch.setattr(desktop_update, "prepared_update", lambda _: {"ready": True})
    monkeypatch.setattr(data_management, "ensure_idle", lambda *_args, **_kwargs: None)
    scheduler = update_workbench.automation_workbench
    calls = []
    running = [True]
    monkeypatch.setattr(scheduler, "scheduler_running", lambda: running[0])
    monkeypatch.setattr(scheduler, "stop_scheduler", lambda: (calls.append("stop"), running.__setitem__(0, False)))
    monkeypatch.setattr(scheduler, "start_scheduler", lambda: (calls.append("resume"), running.__setitem__(0, True)))

    def cannot_launch(*_args):
        raise OSError("无法启动安装进程")

    monkeypatch.setattr(desktop_update, "start_prepared_update", cannot_launch)
    assert update_project.post("/api/system/install-update").status_code == 400
    assert calls == ["stop", "resume"]
    assert not update_workbench.installing()
    assert app.state.uvicorn_server.should_exit is False


def test_newly_checked_version_does_not_install_an_older_prepared_package(update_project, monkeypatch):
    monkeypatch.setattr(desktop_update, "prepared_update", lambda _: {"ready": True, "version": "1.3.0"})
    monkeypatch.setattr(update_workbench, "_checked", {"can_prepare": True, "version": "1.4.0", "notes": "新版本说明"})
    status = update_workbench.status()
    assert status["phase"] == "idle" and status["version"] == "1.4.0" and not status["ready"]
    response = update_project.post("/api/system/install-update")
    assert response.status_code == 400
    assert app.state.uvicorn_server.should_exit is False
