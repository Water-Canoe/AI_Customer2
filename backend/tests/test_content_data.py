from __future__ import annotations

from io import BytesIO
import wave

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

    audio = _wav_bytes()
    first = content_assets.import_asset_file("voice.wav", BytesIO(audio), "audio/wav")
    second = content_assets.import_asset_file("copy.wav", BytesIO(audio), "audio/wav")

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


def test_video_job_keeps_local_asset_order_and_can_cancel(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    prepare_content_db(tmp_path, monkeypatch)
    from app import database
    from app.services import content_assets, content_workbench

    first = content_assets.import_asset_file("first.mp4", BytesIO(b"first"), "video/mp4")
    second = content_assets.import_asset_file("second.mp4", BytesIO(b"second"), "video/mp4")
    job = content_workbench.create_video_job(
        {"video_subject": "测试主题", "video_source": "local"},
        [first["id"], second["id"]],
    )

    assert [item["id"] for item in job["assets"]] == [first["id"], second["id"]]
    with database.connect() as conn:
        resources = conn.execute(
            "SELECT resource FROM runtime_jobs WHERE kind = 'video_generation' AND entity_id = ?",
            (job["id"],),
        ).fetchone()
    assert resources["resource"] == "video"
    assert content_workbench.cancel_video_job(job["id"])["status"] == "cancelled"


def test_content_settings_mask_and_preserve_secrets(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    prepare_content_db(tmp_path, monkeypatch)
    from app.services import content_workbench

    saved = content_workbench.update_settings(
        {"app": {"openai_api_key": "secret", "pexels_api_keys": ["pexels-secret"]}}
    )
    assert saved["app"]["openai_api_key"] == content_workbench.MASKED_SECRET
    assert saved["app"]["pexels_api_keys"] == [content_workbench.MASKED_SECRET]

    content_workbench.update_settings(
        {"app": {"openai_api_key": "", "pexels_api_keys": [content_workbench.MASKED_SECRET]}}
    )
    unmasked = content_workbench.get_settings(mask_secrets=False)
    assert unmasked["app"]["openai_api_key"] == "secret"
    assert unmasked["app"]["pexels_api_keys"] == ["pexels-secret"]


def test_video_job_subject_can_be_renamed(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    prepare_content_db(tmp_path, monkeypatch)
    from app import database
    from app.services import content_workbench

    with database.connect() as conn:
        conn.execute("INSERT INTO video_jobs(id, subject, status) VALUES('rename-job', '旧主题', 'succeeded')")

    renamed = content_workbench.update_video_job("rename-job", "  新主题  ")
    assert renamed["subject"] == "新主题"


def test_video_output_upload_status_can_be_updated(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    prepare_content_db(tmp_path, monkeypatch)
    from app import database
    from app.services import content_workbench

    with database.connect() as conn:
        conn.execute(
            "INSERT INTO video_jobs(id, subject, status, outputs) VALUES('upload-job', '主题', 'succeeded', ?)",
            ('[{"name":"final.mp4","upload_status":"not_uploaded"}]',),
        )

    updated = content_workbench.update_video_output_status("upload-job", "final.mp4", "uploaded")
    assert updated["outputs"][0]["upload_status"] == "uploaded"


def _wav_bytes() -> bytes:
    buffer = BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(8000)
        writer.writeframes(b"\x00\x00" * 800)
    return buffer.getvalue()
