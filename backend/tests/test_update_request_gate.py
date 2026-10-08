from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.routers import message_inbox as inbox_router
from app.services import data_management, desktop_update, message_inbox, update_workbench


@pytest.fixture
def update_gate(tmp_path, monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(update_workbench, "install_root", lambda: tmp_path)
    monkeypatch.setattr(update_workbench, "_requests", 0)
    monkeypatch.setattr(update_workbench, "_checked", {})
    monkeypatch.setattr(update_workbench, "_state", {"phase": "idle", "percent": 0, "message": ""})
    monkeypatch.setattr(desktop_update, "prepared_update", lambda _: {"ready": True, "version": "1.3.0"})
    monkeypatch.setattr(desktop_update, "start_prepared_update", lambda *_: calls.append("launch"))
    monkeypatch.setattr(data_management, "ensure_idle", lambda *_args, **_kwargs: calls.append("idle"))
    monkeypatch.setattr(data_management, "maintenance_active", lambda: False)
    monkeypatch.setattr(update_workbench.automation_workbench, "scheduler_running", lambda: False)
    monkeypatch.setattr(update_workbench.automation_workbench, "stop_scheduler", lambda: calls.append("stop"))
    monkeypatch.setattr(update_workbench.automation_workbench, "start_scheduler", lambda: calls.append("resume"))
    monkeypatch.setattr(app.state, "uvicorn_server", SimpleNamespace(should_exit=False), raising=False)
    # 不进入 lifespan；所有更新和私信操作都使用本地假实现。
    return TestClient(app), calls


def test_install_waits_for_request_that_has_not_yet_acquired_send_lock(update_gate, monkeypatch):
    client, calls = update_gate
    entered, release = threading.Event(), threading.Event()
    results: list[object] = []

    def paused_authorization():
        entered.set()
        assert release.wait(3), "测试未及时释放授权检查"

    def fake_send(*_args, **_kwargs):
        assert not update_workbench.installing()
        calls.append("send")
        return {"status": "accepted"}

    def send_request():
        try:
            results.append(client.post('/api/message-inbox/conversations/1/messages', json={"account_id": "fake", "text": "local test"}))
        except Exception as exc:
            results.append(exc)

    monkeypatch.setattr(inbox_router, "require_license", paused_authorization)
    monkeypatch.setattr(message_inbox, "send_message", fake_send)
    sender = threading.Thread(target=send_request)
    sender.start()
    try:
        assert entered.wait(3)
        response = client.post('/api/system/install-update')
        assert response.status_code == 400
        assert "业务请求" in response.json()["detail"]
        assert calls == []
        assert update_workbench._requests == 1
        assert not app.state.uvicorn_server.should_exit
    finally:
        release.set()
        sender.join(3)

    assert not sender.is_alive()
    assert len(results) == 1 and not isinstance(results[0], Exception)
    assert results[0].status_code == 200
    assert update_workbench._requests == 0
    assert calls == ["send"]
    assert client.post('/api/system/install-update').status_code == 200
    assert calls == ["send", "stop", "idle", "launch"]
    assert app.state.uvicorn_server.should_exit
    assert client.get('/api/settings').status_code == 503
    assert client.get('/api/health').status_code == 200


def test_install_refuses_scheduler_that_did_not_stop(update_gate, monkeypatch):
    client, calls = update_gate
    monkeypatch.setattr(update_workbench.automation_workbench, "scheduler_running", lambda: True)
    response = client.post('/api/system/install-update')
    assert response.status_code == 400
    assert "调度尚未停止" in response.json()["detail"]
    assert calls == ["stop", "resume"]
    assert not update_workbench.installing()
    assert not app.state.uvicorn_server.should_exit

    monkeypatch.setattr(update_workbench.automation_workbench, "scheduler_running", lambda: False)
    assert client.post('/api/system/install-update').status_code == 200
    assert calls[-3:] == ["stop", "idle", "launch"]
