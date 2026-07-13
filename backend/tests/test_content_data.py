from __future__ import annotations

from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
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
    second = content_assets.import_asset_file("copy.wav", BytesIO(audio), "audio/wav", "voice_reference")

    assert first["id"] == second["id"]
    assert second["duplicate"] is True
    assert second["purpose"] == "voice_reference"
    with database.connect() as conn:
        assert migrations.current_version(conn) == migrations.latest_version()
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


def test_browser_webm_recording_is_imported_as_voice_audio(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    prepare_content_db(tmp_path, monkeypatch)
    from app.services import content_assets

    monkeypatch.setattr(content_assets, "_read_metadata", lambda *_: {"width": None, "height": None, "duration": None, "thumbnail_path": ""})

    asset = content_assets.import_asset_file(
        "现场录音.webm",
        BytesIO(b"browser-recording"),
        "audio/webm;codecs=opus",
        "voice_reference",
    )

    assert asset["asset_type"] == "audio"
    assert asset["purpose"] == "voice_reference"


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


def test_video_job_rejects_voice_reference_as_background_music(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    prepare_content_db(tmp_path, monkeypatch)
    from app.services import content_assets, content_workbench

    monkeypatch.setattr(content_assets, "_read_metadata", lambda *_: {"width": None, "height": None, "duration": None, "thumbnail_path": ""})
    reference = content_assets.import_asset_file(
        "clone.mp3",
        BytesIO(b"voice-reference"),
        "audio/mpeg",
        "voice_reference",
    )

    with pytest.raises(ValueError, match="背景音乐分区"):
        content_workbench.create_video_job(
            {"video_subject": "测试", "video_source": "pexels"},
            [],
            bgm_asset_id=reference["id"],
        )


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


def test_voice_profile_crud_and_asset_protection(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    prepare_content_db(tmp_path, monkeypatch)
    from app.services import content_assets, voice_profiles

    asset = content_assets.import_asset_file("reference.wav", BytesIO(_wav_bytes()), "audio/wav")
    with pytest.raises(ValueError, match="使用授权"):
        voice_profiles.create_profile({"name": "老板音色", "reference_asset_id": asset["id"]})

    profile = voice_profiles.create_profile({
        "name": "老板音色",
        "provider": "voxcpm2",
        "reference_asset_id": asset["id"],
        "prompt_text": "这是一段参考声音",
        "consent_confirmed": True,
    })
    assert profile["reference_asset"]["id"] == asset["id"]
    assert profile["reference_asset"]["purpose"] == "voice_reference"
    assert voice_profiles.update_profile(profile["id"], {"name": "品牌音色"})["name"] == "品牌音色"
    with pytest.raises(RuntimeError, match="克隆音色"):
        content_assets.delete_asset(asset["id"])
    assert voice_profiles.delete_profile(profile["id"])["deleted"] is True


def test_voxcpm2_adapter_uses_reference_and_prompt(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import voice_synthesis

    calls: dict[str, object] = {}

    class FakeModel:
        tts_model = SimpleNamespace(sample_rate=16000)

        def generate(self, **kwargs):
            import numpy as np

            calls.update(kwargs)
            return np.zeros(1600, dtype=np.float32)

    def fake_run(command, **_kwargs):
        Path(command[-1]).write_bytes(b"fake-mp3")
        return SimpleNamespace(returncode=0, stderr="", stdout="")

    monkeypatch.setattr(voice_synthesis, "_load_voxcpm2", lambda: FakeModel())
    monkeypatch.setattr(voice_synthesis.subprocess, "run", fake_run)
    monkeypatch.setattr(voice_synthesis.utils, "get_ffmpeg_binary", lambda: "ffmpeg")
    reference = tmp_path / "reference.webm"
    reference.write_bytes(_wav_bytes())
    output = tmp_path / "voice.mp3"

    voice_synthesis._synthesize_voxcpm2(
        {"prompt_text": "参考文字", "style_prompt": "温和（自然）"},
        "生成文字",
        reference,
        output,
        1.0,
        1.0,
    )

    prepared_reference = output.with_suffix(".reference.wav")
    assert calls["reference_wav_path"] == str(prepared_reference)
    assert calls["prompt_wav_path"] == str(prepared_reference)
    assert calls["prompt_text"] == "参考文字"
    assert calls["text"] == "(温和自然)生成文字"
    assert output.is_file()
    assert not prepared_reference.exists()
    assert not output.with_suffix(".voxcpm.wav").exists()


def test_voice_reference_script_uses_existing_video_ai(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    prepare_content_db(tmp_path, monkeypatch)
    from app.services import content_workbench
    from app.video_engine.services import llm

    captured: dict[str, object] = {}

    def fake_generate_script(**kwargs):
        captured.update(kwargs)
        return "今天天气很好，请用自然的语气读完这段文字。"

    monkeypatch.setattr(content_workbench, "_apply_runtime_settings", lambda: None)
    monkeypatch.setattr(llm, "generate_script", fake_generate_script)

    result = content_workbench.generate_voice_reference_script()

    assert result["script"].startswith("今天天气")
    assert "声音克隆" in str(captured["video_subject"])
    assert "80到120" in str(captured["video_script_prompt"])


def _wav_bytes() -> bytes:
    buffer = BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(8000)
        writer.writeframes(b"\x00\x00" * 800)
    return buffer.getvalue()
