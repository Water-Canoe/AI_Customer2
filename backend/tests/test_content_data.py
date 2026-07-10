from __future__ import annotations

from io import BytesIO

import pytest


def prepare_content_db(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_CUSTOMER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AI_CUSTOMER_DB", str(tmp_path / "ai_customer.sqlite3"))
    from app import database

    database.init_db()


def test_content_migration_and_asset_dedup(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    prepare_content_db(tmp_path, monkeypatch)
    from app import database, migrations
    from app.services import content_assets

    first = content_assets.import_asset_file("voice.mp3", BytesIO(b"same-audio"), "audio/mpeg")
    second = content_assets.import_asset_file("copy.mp3", BytesIO(b"same-audio"), "audio/mpeg")

    assert first["id"] == second["id"]
    assert second["duplicate"] is True
    with database.connect() as conn:
        assert migrations.current_version(conn) == 4
        assert conn.execute("SELECT COUNT(*) FROM content_assets").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM video_jobs").fetchone()[0] == 0


def test_asset_delete_is_blocked_while_video_job_is_active(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    prepare_content_db(tmp_path, monkeypatch)
    from app import database
    from app.services import content_assets

    asset = content_assets.import_asset_file("clip.mp4", BytesIO(b"fake-video"), "video/mp4")
    with database.connect() as conn:
        conn.execute("INSERT INTO video_jobs(id, subject, status) VALUES('job-1', 'demo', 'running')")
        conn.execute(
            "INSERT INTO video_job_assets(video_job_id, asset_id, sort_order) VALUES('job-1', ?, 0)",
            (asset["id"],),
        )

    with pytest.raises(RuntimeError, match="运行中的视频任务"):
        content_assets.delete_asset(asset["id"])


def test_asset_paths_cannot_escape_managed_root(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    prepare_content_db(tmp_path, monkeypatch)
    from app.services import content_assets

    with pytest.raises(ValueError, match="超出内容资产目录"):
        content_assets.resolve_asset_path("../outside.mp4")
