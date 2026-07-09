from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest


BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))


def prepare_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    project_db = tmp_path / "ai_customer.sqlite3"
    raw_db = tmp_path / "sqlite_tables.db"
    monkeypatch.setenv("AI_CUSTOMER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AI_CUSTOMER_DB", str(project_db))

    from app import database

    database.init_db()
    raw_conn = sqlite3.connect(raw_db)
    try:
        raw_conn.execute("CREATE TABLE raw_items(id INTEGER PRIMARY KEY, value TEXT)")
        raw_conn.execute("INSERT INTO raw_items(value) VALUES('test')")
        raw_conn.commit()
    finally:
        raw_conn.close()
    with database.connect() as conn:
        database.set_setting(conn, "media_crawler_db_path", str(raw_db))
    return project_db, raw_db


def test_schema_migrations_drop_removed_agent_tables(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project_db, _ = prepare_database(tmp_path, monkeypatch)
    from app import database, migrations

    with database.connect(project_db) as conn:
        conn.execute("CREATE TABLE agent_runs(id TEXT PRIMARY KEY)")
        conn.execute("CREATE TABLE agent_run_events(id INTEGER PRIMARY KEY, run_id TEXT)")
        conn.execute("INSERT INTO agent_runs(id) VALUES('legacy')")
        conn.execute("DELETE FROM schema_migrations WHERE version = 2")

    database.init_db()

    with database.connect(project_db) as conn:
        tables = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert "agent_runs" not in tables
        assert "agent_run_events" not in tables
        assert migrations.current_version(conn) == migrations.latest_version()
        assert conn.execute("PRAGMA user_version").fetchone()[0] == migrations.latest_version()
    assert any((tmp_path / "backups").iterdir())


def test_backup_restore_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    prepare_database(tmp_path, monkeypatch)
    from app import database
    from app.services import data_management

    with database.connect() as conn:
        database.set_setting(conn, "ai_model", "before-backup")
    backup = data_management.create_backup("test_round_trip")
    with database.connect() as conn:
        database.set_setting(conn, "ai_model", "after-backup")

    result = data_management.restore_backup(str(backup["id"]), "恢复备份")

    assert result["ok"] is True
    assert result["safety_backup"]["reason"].startswith("pre_restore_")
    with database.connect() as conn:
        assert database.get_setting(conn, "ai_model") == "before-backup"


def test_clear_all_data_includes_message_and_traffic_tables(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _, raw_db = prepare_database(tmp_path, monkeypatch)
    from app import database
    from app.services import maintenance

    with database.connect() as conn:
        conn.execute("INSERT INTO message_batches(id) VALUES('batch-1')")
        conn.execute("INSERT INTO traffic_plans(id, name) VALUES('plan-1', '测试计划')")
        conn.execute("INSERT INTO analysis_jobs(id, target_type, target_id) VALUES('job-1', 'lead', 1)")

    result = maintenance.clear_all_data(
        "清空所有数据",
        create_backup=False,
        include_crawler=True,
    )

    assert result["project"]["tables"]["message_batches"] == 1
    assert result["project"]["tables"]["traffic_plans"] == 1
    with database.connect() as conn:
        for table in ("message_batches", "traffic_plans", "analysis_jobs"):
            assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    raw_conn = sqlite3.connect(raw_db)
    try:
        assert raw_conn.execute("SELECT COUNT(*) FROM raw_items").fetchone()[0] == 0
    finally:
        raw_conn.close()
