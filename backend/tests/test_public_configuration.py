from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient


BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))


def test_public_settings_mask_secrets_and_reject_internal_overrides(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AI_CUSTOMER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AI_CUSTOMER_DB", str(tmp_path / "ai_customer.sqlite3"))
    from app import database
    from app.main import app

    database.init_db()
    with database.connect() as conn:
        database.set_setting(conn, "ai_api_key", "private-key")
        database.set_setting(conn, "media_crawler_path", "C:/internal/collector")
        database.set_setting(conn, "media_crawler_db_path", "C:/internal/collector/raw.sqlite3")
        database.set_setting(conn, "license_server_url", "https://should-not-be-used.invalid")

    client = TestClient(app)
    health = client.get("/api/health").json()
    assert health["product"] == "ai-customer"
    assert health["packaged"] is False
    settings = client.get("/api/settings").json()
    assert settings["ai_api_key"] == ""
    assert settings["ai_api_key_configured"] is True
    assert "media_crawler_path" not in settings
    assert "media_crawler_db_path" not in settings
    assert "license_server_url" not in settings

    response = client.put(
        "/api/settings",
        json={
            "values": {
                "ai_api_key": "",
                "media_crawler_path": "D:/tampered",
                "license_server_url": "https://tampered.invalid",
            }
        },
    )
    assert response.status_code == 200
    with database.connect() as conn:
        assert database.get_setting(conn, "ai_api_key") == "private-key"
        assert database.get_setting(conn, "media_crawler_path") == "C:/internal/collector"
        assert database.get_setting(conn, "license_server_url") == "https://should-not-be-used.invalid"

    from app.routers import system

    server = SimpleNamespace(should_exit=False)
    app.state.uvicorn_server = server
    response = client.post("/api/system/exit")
    assert response.status_code == 200
    assert server.should_exit is True
    del app.state.shutdown_requested
    server.should_exit = False
    started: list[bool] = []
    monkeypatch.setattr(system, "_start_manual_update_launcher", lambda: started.append(True))
    response = client.post("/api/system/check-update")
    assert response.status_code == 200
    assert started == [True]
    assert server.should_exit is True
    del app.state.uvicorn_server
    del app.state.update_restart_requested
