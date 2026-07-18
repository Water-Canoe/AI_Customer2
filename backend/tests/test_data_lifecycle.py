from __future__ import annotations

import json
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
    backup_dir = next((tmp_path / "backups").iterdir())
    manifest = json.loads((backup_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["media_crawler_database"] is None
    assert manifest["file_count"] == 0


def test_backup_restore_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _, raw_db = prepare_database(tmp_path, monkeypatch)
    from app import database
    from app.services import data_management

    files = {
        database.get_backup_data_roots()["traffic_images"] / "comment.png": b"old-image",
        database.get_backup_data_roots()["content_assets"] / "originals" / "asset.mp4": b"old-asset",
        database.get_backup_data_roots()["video_tasks"] / "job-1" / "final.mp4": b"old-video",
        database.get_backup_data_roots()["social_publish"] / "accounts" / "account.json": b"old-login",
    }
    for path, content in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    with database.connect() as conn:
        database.set_setting(conn, "ai_model", "before-backup")
    backup = data_management.create_backup("test_round_trip")
    assert backup["file_count"] == 4
    assert backup["media_crawler_database"]["file"] == "media_crawler.sqlite3"
    assert all(item["tree_sha256"] for item in backup["data_directories"].values())
    with database.connect() as conn:
        database.set_setting(conn, "ai_model", "after-backup")
    for path in files:
        path.write_bytes(b"changed")
    extra_file = database.get_backup_data_roots()["content_assets"] / "created-after-backup.txt"
    extra_file.write_text("new", encoding="utf-8")
    with sqlite3.connect(raw_db) as raw_conn:
        raw_conn.execute("UPDATE raw_items SET value = 'changed'")

    result = data_management.restore_backup(str(backup["id"]), "恢复备份")

    assert result["ok"] is True
    assert result["safety_backup"]["reason"].startswith("pre_restore_")
    assert result["restored"]["restored_files"] == {
        "traffic_images": 1,
        "content_assets": 1,
        "video_tasks": 1,
        "social_publish": 1,
    }
    for path, content in files.items():
        assert path.read_bytes() == content
    assert not extra_file.exists()
    with sqlite3.connect(raw_db) as raw_conn:
        assert raw_conn.execute("SELECT value FROM raw_items").fetchone()[0] == "test"
    with database.connect() as conn:
        assert database.get_setting(conn, "ai_model") == "before-backup"


def test_restore_rejects_corrupt_media_crawler_backup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _, raw_db = prepare_database(tmp_path, monkeypatch)
    from app.services import data_management

    backup = data_management.create_backup("corrupt_media")
    backup_path = tmp_path / "backups" / str(backup["id"]) / "media_crawler.sqlite3"
    backup_path.write_bytes(b"corrupt")

    with pytest.raises(ValueError, match="MyCrawler 数据库校验失败"):
        data_management.restore_backup(str(backup["id"]), data_management.RESTORE_CONFIRM_TEXT)

    with sqlite3.connect(raw_db) as raw_conn:
        assert raw_conn.execute("SELECT value FROM raw_items").fetchone()[0] == "test"


def test_active_jobs_covers_runtime_content_and_pending_work(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    prepare_database(tmp_path, monkeypatch)
    from app import database
    from app.services import data_management

    with database.connect() as conn:
        conn.execute("INSERT INTO runtime_jobs(id, kind) VALUES('runtime-1', 'video_generation')")
        conn.execute("INSERT INTO video_jobs(id, subject) VALUES('video-1', '测试视频')")
        conn.execute("INSERT INTO publish_accounts(id, platform, name, auth_relative_path) VALUES('account-1', 'dy', '测试账号', 'platform_accounts/dy/account-1/profile')")
        conn.execute("INSERT INTO account_feature_bindings(account_id, feature, is_default) VALUES('account-1', 'publish', 1)")
        conn.execute("INSERT INTO publish_tasks(id, batch_id, account_id, source_type, content_type, title) VALUES('publish-1', 'batch-1', 'account-1', 'asset_video', 'video', '测试发布')")
        conn.execute("INSERT INTO analysis_jobs(id, target_type, target_id, status) VALUES('analysis-1', 'lead', 1, 'running')")

    assert {item["table"] for item in data_management.active_jobs()} >= {
        "runtime_jobs",
        "video_jobs",
        "publish_tasks",
        "analysis_jobs",
    }
    with pytest.raises(ValueError, match="不能创建备份"):
        data_management.create_backup("active_jobs")
    assert data_management.maintenance_active() is False


def test_pending_analysis_without_runtime_job_does_not_block_backup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    prepare_database(tmp_path, monkeypatch)
    from app import database
    from app.services import data_management

    with database.connect() as conn:
        conn.execute("INSERT INTO analysis_jobs(id, target_type, target_id) VALUES('orphan-analysis', 'lead', 1)")

    assert data_management.active_jobs() == []
    assert data_management.create_backup("orphan_analysis")["reason"] == "orphan_analysis"


def test_backup_can_be_deleted_safely(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    prepare_database(tmp_path, monkeypatch)
    from app.services import data_management

    backup = data_management.create_backup("delete_test")
    backup_dir = tmp_path / "backups" / str(backup["id"])

    assert backup_dir.is_dir()
    assert data_management.delete_backup(str(backup["id"])) == {"ok": True, "id": backup["id"]}
    assert not backup_dir.exists()
    with pytest.raises(ValueError, match="备份标识不合法"):
        data_management.delete_backup("../outside")


def test_maintenance_window_pauses_scheduler_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    prepare_database(tmp_path, monkeypatch)
    from app.services import automation_workbench, data_management

    events: list[str] = []
    monkeypatch.setattr(automation_workbench, "scheduler_running", lambda: True)
    monkeypatch.setattr(automation_workbench, "stop_scheduler", lambda: events.append("stop"))
    monkeypatch.setattr(automation_workbench, "start_scheduler", lambda: events.append("start"))

    with data_management.maintenance_window("测试维护"):
        assert data_management.maintenance_active() is True
        with data_management.maintenance_window("嵌套维护"):
            assert data_management.maintenance_active() is True

    assert data_management.maintenance_active() is False
    assert events == ["stop", "start"]


def test_clear_all_data_includes_all_business_tables(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _, raw_db = prepare_database(tmp_path, monkeypatch)
    from app import database
    from app.services import maintenance

    with database.connect() as conn:
        conn.execute("INSERT INTO message_batches(id, status) VALUES('batch-1', 'completed')")
        conn.execute("INSERT INTO traffic_plans(id, name) VALUES('plan-1', '测试计划')")
        conn.execute("INSERT INTO analysis_jobs(id, target_type, target_id, status) VALUES('job-1', 'lead', 1, 'succeeded')")
        conn.execute("INSERT INTO content_assets(id, name, asset_type, relative_path, file_size, sha256) VALUES('asset-1', '素材', 'video', 'originals/asset-1.mp4', 1, 'hash-1')")
        conn.execute("INSERT INTO video_jobs(id, subject, status) VALUES('video-1', '测试视频', 'succeeded')")
        conn.execute("INSERT INTO video_job_assets(video_job_id, asset_id) VALUES('video-1', 'asset-1')")
        conn.execute("INSERT INTO publish_accounts(id, platform, name, auth_relative_path) VALUES('account-1', 'dy', '测试账号', 'platform_accounts/dy/account-1/profile')")
        conn.execute("INSERT INTO account_feature_bindings(account_id, feature, is_default) VALUES('account-1', 'publish', 1)")
        conn.execute("INSERT INTO publish_tasks(id, batch_id, account_id, source_type, content_type, title, status) VALUES('publish-1', 'publish-batch-1', 'account-1', 'asset_video', 'video', '测试发布', 'succeeded')")
        conn.execute("INSERT INTO publish_task_assets(task_id, asset_id) VALUES('publish-1', 'asset-1')")

    result = maintenance.clear_all_data(
        "清空业务记录",
        create_backup=False,
        include_crawler=True,
    )

    assert result["project"]["tables"]["message_batches"] == 1
    assert result["project"]["tables"]["traffic_plans"] == 1
    with database.connect() as conn:
        for table in (
            "message_batches",
            "traffic_plans",
            "analysis_jobs",
            "content_assets",
            "video_jobs",
            "video_job_assets",
            "publish_tasks",
            "publish_task_assets",
        ):
            assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM publish_accounts").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM account_feature_bindings").fetchone()[0] == 1
    raw_conn = sqlite3.connect(raw_db)
    try:
        assert raw_conn.execute("SELECT COUNT(*) FROM raw_items").fetchone()[0] == 0
    finally:
        raw_conn.close()
