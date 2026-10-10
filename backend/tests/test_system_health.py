from __future__ import annotations

import json

import pytest

from app import database
from app.routers.system import health
from app.services import desktop_update


@pytest.mark.parametrize("packaged", [False, True])
def test_health_reports_actual_environment_and_database(tmp_path, monkeypatch, packaged):
    monkeypatch.setenv("AI_CUSTOMER_DB", str(tmp_path / "test.sqlite3"))
    monkeypatch.setenv("AI_CUSTOMER_PACKAGED", "1" if packaged else "0")
    monkeypatch.setenv("AI_CUSTOMER_INSTALL_ROOT", str(tmp_path))
    # 使用非源码版本，证明健康检查读取实际库和环境，而非固定版本常量。
    with database.connect() as connection:
        connection.execute("PRAGMA user_version = 99")
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "browser.exe").write_bytes(b"fixture")
    (tmp_path / "release-manifest.json").write_text(json.dumps({"environment_version": "2.0.0"}))
    (runtime / "environment-manifest.json").write_text(json.dumps({
        "format": 1, "product": "AI Customer Environment", "version": "2.0.0", "required_paths": ["browser.exe"],
    }))

    status = health()
    assert status["schema_version"] == 99
    assert status["environment_version"] == ("2.0.0" if packaged else "")
    assert status["updater_version"] == desktop_update.UPDATER_VERSION


def test_packaged_health_refuses_missing_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_CUSTOMER_PACKAGED", "1")
    monkeypatch.setenv("AI_CUSTOMER_INSTALL_ROOT", str(tmp_path))
    (tmp_path / "release-manifest.json").write_text(json.dumps({"environment_version": "2.0.0"}))
    with pytest.raises(RuntimeError, match="核心运行环境缺失"):
        health()
