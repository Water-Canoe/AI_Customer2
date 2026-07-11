from __future__ import annotations

import importlib.util
import json
import shutil
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any

import toml
from pydantic import ValidationError

from app import database
from app.services import content_assets


MASKED_SECRET = "********"
TERMINAL_VIDEO_STATUSES = {"succeeded", "failed", "cancelled", "interrupted"}


class VideoJobCancelled(RuntimeError):
    pass


def default_settings() -> dict[str, Any]:
    path = Path(__file__).resolve().parents[1] / "video_engine" / "config.default.toml"
    return toml.load(path)


def get_settings(*, mask_secrets: bool = True) -> dict[str, Any]:
    with database.connect() as conn:
        raw = database.get_setting(conn, "content_video_config", "{}")
    try:
        saved = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        saved = {}
    result = _deep_merge(default_settings(), saved if isinstance(saved, dict) else {})
    return _mask_settings(result) if mask_secrets else result


def update_settings(values: dict[str, Any]) -> dict[str, Any]:
    current = get_settings(mask_secrets=False)
    merged = _merge_user_settings(current, values if isinstance(values, dict) else {})
    with database.connect() as conn:
        database.set_setting(
            conn,
            "content_video_config",
            json.dumps(merged, ensure_ascii=False, separators=(",", ":")),
        )
    return get_settings(mask_secrets=True)


def create_video_job(
    params: dict[str, Any],
    asset_ids: list[str],
    audio_asset_id: str = "",
    bgm_asset_id: str = "",
    publish: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from app.video_engine.models.schema import TaskVideoRequest
    from app.services import content_publish

    payload = deepcopy(params or {})
    material_assets = [_require_asset(asset_id, {"video", "image"}) for asset_id in asset_ids]
    if str(payload.get("video_source") or "") == "local":
        if not material_assets:
            raise ValueError("本地素材模式必须至少选择一项视频或图片资产")
        payload["video_materials"] = [
            {
                "provider": "local",
                "url": str(content_assets.resolve_asset_path(asset["relative_path"])),
                "duration": int(asset.get("duration") or 0),
            }
            for asset in material_assets
        ]

    audio_asset = _require_asset(audio_asset_id, {"audio"}) if audio_asset_id else None
    bgm_asset = _require_asset(bgm_asset_id, {"audio"}) if bgm_asset_id else None
    if bgm_asset and bgm_asset.get("purpose") != "background_music":
        raise ValueError("背景音乐只能选择背景音乐分区中的音频")
    if audio_asset:
        payload["custom_audio_file"] = str(content_assets.resolve_asset_path(audio_asset["relative_path"]))
    if bgm_asset:
        payload["bgm_type"] = "custom"
        payload["bgm_file"] = str(content_assets.resolve_asset_path(bgm_asset["relative_path"]))

    try:
        validated = TaskVideoRequest(**payload)
    except ValidationError as exc:
        raise ValueError(_validation_message(exc)) from exc

    publish_plan = publish or {}
    content_publish.validate_auto_publish(publish_plan)

    job_id = uuid.uuid4().hex
    serialized = validated.model_dump(mode="json", warnings=False)
    with database.connect() as conn:
        conn.execute(
            """
            INSERT INTO video_jobs(id, subject, script, params, status, current_stage)
            VALUES(?, ?, ?, ?, 'queued', 'queued')
            """,
            (
                job_id,
                str(validated.video_subject),
                str(validated.video_script or ""),
                json.dumps(serialized, ensure_ascii=False),
            ),
        )
        for index, asset in enumerate(material_assets):
            conn.execute(
                "INSERT INTO video_job_assets(video_job_id, asset_id, role, sort_order) VALUES(?, ?, 'material', ?)",
                (job_id, str(asset["id"]), index),
            )
        if audio_asset:
            conn.execute(
                "INSERT INTO video_job_assets(video_job_id, asset_id, role, sort_order) VALUES(?, ?, 'audio', 0)",
                (job_id, str(audio_asset["id"])),
            )
        if bgm_asset:
            conn.execute(
                "INSERT INTO video_job_assets(video_job_id, asset_id, role, sort_order) VALUES(?, ?, 'bgm', 0)",
                (job_id, str(bgm_asset["id"])),
            )

    if bool(publish_plan.get("enabled")):
        content_publish.create_waiting_video_tasks(
            job_id,
            [str(value) for value in publish_plan.get("account_ids", [])],
            int(serialized.get("video_count") or 1),
            str(publish_plan.get("output_scope") or "first"),
            str(publish_plan.get("publish_strategy") or "immediate"),
            str(publish_plan.get("scheduled_at") or ""),
        )

    from app.services import job_queue

    runtime_job = job_queue.enqueue_video_job(job_id)
    with database.connect() as conn:
        conn.execute(
            "UPDATE video_jobs SET runtime_job_id = ?, updated_at = datetime('now', 'localtime') WHERE id = ?",
            (str(runtime_job["id"]), job_id),
        )
    return get_video_job(job_id)


def run_video_job(video_job_id: str) -> dict[str, Any]:
    from app.services import job_queue
    from app.video_engine.config import config
    from app.video_engine.services import state, task
    from app.services import content_publish
    from app.video_engine.models.schema import TaskVideoRequest

    job = get_video_job(video_job_id, include_archived=True)
    attempt = int(job.get("attempt") or 0) + 1
    engine_task_id = f"{video_job_id}/attempt-{attempt}"
    settings = _runtime_settings()
    config.apply_runtime_config(settings)
    params = TaskVideoRequest(**job["params"])

    with database.connect() as conn:
        conn.execute(
            """
            UPDATE video_jobs
            SET status = 'running', progress = 1, current_stage = 'preparing', attempt = ?,
                error = '', started_at = datetime('now', 'localtime'), finished_at = NULL,
                updated_at = datetime('now', 'localtime')
            WHERE id = ?
            """,
            (attempt, video_job_id),
        )

    def on_progress(snapshot: dict[str, Any]) -> None:
        if job_queue.is_entity_cancel_requested("video_generation", video_job_id):
            raise VideoJobCancelled("用户取消视频生成")
        progress = max(0, min(100, int(snapshot.get("progress") or 0)))
        with database.connect() as conn:
            conn.execute(
                """
                UPDATE video_jobs
                SET progress = ?, current_stage = ?, updated_at = datetime('now', 'localtime')
                WHERE id = ?
                """,
                (progress, _stage_for_progress(progress), video_job_id),
            )

    state.set_progress_listener(on_progress)
    try:
        result = task.start(engine_task_id, params, stop_at="video")
        if not result or not result.get("videos"):
            raise RuntimeError("视频生成未产生有效成品，请查看任务错误后重试")
        outputs = [_format_output(video_job_id, path) for path in result.get("videos", [])]
        publish_results = []
        with database.connect() as conn:
            conn.execute(
                """
                UPDATE video_jobs
                SET status = 'succeeded', progress = 100, current_stage = 'completed',
                    script = ?, outputs = ?, publish_results = ?, error = '',
                    finished_at = datetime('now', 'localtime'), updated_at = datetime('now', 'localtime')
                WHERE id = ?
                """,
                (
                    str(result.get("script") or params.video_script or ""),
                    json.dumps(outputs, ensure_ascii=False),
                    json.dumps(publish_results, ensure_ascii=False),
                    video_job_id,
                ),
            )
        content_publish.enqueue_tasks(content_publish.bind_waiting_video_tasks(video_job_id, outputs))
        return get_video_job(video_job_id, include_archived=True)
    except VideoJobCancelled as exc:
        _finish_video_job(video_job_id, "cancelled", str(exc))
        return {"id": video_job_id, "status": "cancelled"}
    except Exception as exc:
        _finish_video_job(video_job_id, "failed", str(exc))
        raise
    finally:
        state.set_progress_listener(None)


def list_video_jobs(page: int = 1, page_size: int = 20, include_archived: bool = False) -> dict[str, Any]:
    safe_page = max(1, int(page))
    safe_size = max(1, min(100, int(page_size)))
    where = "1 = 1" if include_archived else "archived = 0"
    with database.connect() as conn:
        total = int(conn.execute(f"SELECT COUNT(*) FROM video_jobs WHERE {where}").fetchone()[0])
        rows = conn.execute(
            f"SELECT * FROM video_jobs WHERE {where} ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
            (safe_size, (safe_page - 1) * safe_size),
        ).fetchall()
    return {
        "items": [_format_video_job(row) for row in rows],
        "total": total,
        "page": safe_page,
        "page_size": safe_size,
    }


def get_video_job(video_job_id: str, *, include_archived: bool = False) -> dict[str, Any]:
    with database.connect() as conn:
        row = conn.execute("SELECT * FROM video_jobs WHERE id = ?", (str(video_job_id),)).fetchone()
    if not row or (int(row["archived"] or 0) and not include_archived):
        raise ValueError("视频任务不存在")
    return _format_video_job(row)


def update_video_job(video_job_id: str, subject: str) -> dict[str, Any]:
    clean_subject = str(subject or "").strip()
    if not clean_subject:
        raise ValueError("视频主题不能为空")
    get_video_job(video_job_id, include_archived=True)
    with database.connect() as conn:
        conn.execute(
            "UPDATE video_jobs SET subject = ?, updated_at = datetime('now', 'localtime') WHERE id = ?",
            (clean_subject, video_job_id),
        )
    return get_video_job(video_job_id, include_archived=True)


def update_video_output_status(video_job_id: str, output_name: str, upload_status: str) -> dict[str, Any]:
    job = get_video_job(video_job_id, include_archived=True)
    matched = False
    for output in job["outputs"]:
        if str(output.get("name") or "") == output_name:
            output["upload_status"] = upload_status
            matched = True
            break
    if not matched:
        raise ValueError("视频成品不存在")
    with database.connect() as conn:
        conn.execute(
            "UPDATE video_jobs SET outputs = ?, updated_at = datetime('now', 'localtime') WHERE id = ?",
            (json.dumps(job["outputs"], ensure_ascii=False), video_job_id),
        )
    return get_video_job(video_job_id, include_archived=True)


def cancel_video_job(video_job_id: str) -> dict[str, Any]:
    get_video_job(video_job_id, include_archived=True)
    from app.services import job_queue

    jobs = job_queue.cancel_by_entity("video_generation", video_job_id)
    if not jobs:
        raise ValueError("视频任务当前不能取消")
    return get_video_job(video_job_id, include_archived=True)


def retry_video_job(video_job_id: str) -> dict[str, Any]:
    job = get_video_job(video_job_id, include_archived=True)
    if str(job["status"]) not in TERMINAL_VIDEO_STATUSES:
        raise ValueError("只有已结束的视频任务可以重试")
    from app.services import job_queue

    with database.connect() as conn:
        runtime = conn.execute(
            "SELECT id FROM runtime_jobs WHERE kind = 'video_generation' AND entity_id = ? ORDER BY created_at DESC LIMIT 1",
            (video_job_id,),
        ).fetchone()
    if runtime:
        runtime_job = job_queue.retry_job(str(runtime["id"]))
    else:
        _reset_video_job(video_job_id)
        runtime_job = job_queue.enqueue_video_job(video_job_id)
    with database.connect() as conn:
        conn.execute(
            "UPDATE video_jobs SET runtime_job_id = ?, archived = 0, updated_at = datetime('now', 'localtime') WHERE id = ?",
            (str(runtime_job["id"]), video_job_id),
        )
    return get_video_job(video_job_id, include_archived=True)


def archive_video_job(video_job_id: str) -> dict[str, Any]:
    job = get_video_job(video_job_id, include_archived=True)
    if str(job["status"]) not in TERMINAL_VIDEO_STATUSES:
        raise ValueError("运行中或排队中的视频任务不能归档")
    with database.connect() as conn:
        conn.execute(
            "UPDATE video_jobs SET archived = 1, updated_at = datetime('now', 'localtime') WHERE id = ?",
            (video_job_id,),
        )
    return {"id": video_job_id, "archived": True}


def publish_video_job(video_job_id: str, output_name: str = "") -> dict[str, Any]:
    from app.video_engine.config import config
    from app.video_engine.services import upload_post

    job = get_video_job(video_job_id, include_archived=True)
    if str(job["status"]) != "succeeded":
        raise ValueError("只有生成成功的视频可以发布")
    config.apply_runtime_config(_runtime_settings())
    service = upload_post.upload_post_service.refresh()
    if not service.is_configured():
        raise ValueError("请先在内容设置中配置并启用Upload-Post")
    outputs = [output for output in job["outputs"] if not output_name or str(output.get("name") or "") == output_name]
    if not outputs:
        raise ValueError("视频成品不存在")
    results = []
    for output in outputs:
        path = resolve_video_output(str(output.get("relative_path") or ""))
        result = upload_post.cross_post_video(str(path), str(job["subject"]))
        result["output_name"] = str(output.get("name") or "")
        results.append(result)
    with database.connect() as conn:
        conn.execute(
            "UPDATE video_jobs SET publish_results = ?, updated_at = datetime('now', 'localtime') WHERE id = ?",
            (json.dumps((job["publish_results"] if output_name else []) + results, ensure_ascii=False), video_job_id),
        )
    for result in results:
        if result.get("success"):
            update_video_output_status(video_job_id, str(result["output_name"]), "uploaded")
    return {"id": video_job_id, "results": results}


def generate_script(payload: dict[str, Any]) -> dict[str, Any]:
    _apply_runtime_settings()
    from app.video_engine.services import llm

    script = llm.generate_script(
        video_subject=str(payload.get("video_subject") or ""),
        language=str(payload.get("video_language") or ""),
        paragraph_number=int(payload.get("paragraph_number") or 1),
        video_script_prompt=str(payload.get("video_script_prompt") or ""),
        custom_system_prompt=str(payload.get("custom_system_prompt") or ""),
    )
    if not script or str(script).startswith("Error: "):
        raise RuntimeError(str(script or "视频文案生成失败"))
    return {"video_script": script}


def generate_terms(payload: dict[str, Any]) -> dict[str, Any]:
    _apply_runtime_settings()
    from app.video_engine.services import llm

    terms = llm.generate_terms(
        video_subject=str(payload.get("video_subject") or ""),
        video_script=str(payload.get("video_script") or ""),
        amount=int(payload.get("amount") or 5),
        match_script_order=bool(payload.get("match_materials_to_script")),
    )
    if not terms:
        raise RuntimeError("素材关键词生成失败")
    return {"video_terms": terms}


def generate_social_metadata(payload: dict[str, Any]) -> dict[str, Any]:
    _apply_runtime_settings()
    from app.video_engine.services import llm

    return llm.generate_social_metadata(
        video_subject=str(payload.get("video_subject") or ""),
        video_script=str(payload.get("video_script") or ""),
        language=str(payload.get("language") or "auto"),
        platform=str(payload.get("platform") or "tiktok"),
    )


def generate_voice_reference_script() -> dict[str, Any]:
    result = generate_script({
        "video_subject": "声音克隆标准朗读样本",
        "video_language": "zh-CN",
        "paragraph_number": 1,
        "video_script_prompt": "生成80到120个汉字，语气自然，包含陈述、疑问和感叹语气，并覆盖常见声母、韵母与停顿。只返回需要朗读的正文。",
        "custom_system_prompt": "你是声音克隆参考文案生成器。只输出一段适合普通人连续朗读的中文正文，不要标题、序号、解释、括号或特殊符号。",
    })
    return {"script": str(result["video_script"]).strip()}


def list_voices(provider: str = "edge") -> list[str]:
    _apply_runtime_settings()
    from app.video_engine.config import config
    from app.video_engine.services import voice

    if provider == "siliconflow":
        return voice.get_siliconflow_voices()
    if provider == "gemini":
        return voice.get_gemini_voices()
    if provider == "mimo":
        return voice.get_mimo_voices()
    if provider == "elevenlabs":
        return voice.get_elevenlabs_voices(str(config.elevenlabs.get("api_key") or ""))
    if provider == "chatterbox":
        return voice.get_chatterbox_voices()
    if provider == "voxcpm2":
        from app.services import voice_profiles

        return [f"voxcpm2:{item['id']}" for item in voice_profiles.list_profiles("voxcpm2")]
    voices = voice.get_all_azure_voices(filter_locals=None)
    if provider == "azure-v2":
        return [item for item in voices if "V2" in item]
    return [item for item in voices if "V2" not in item]


def environment_check() -> dict[str, Any]:
    dependency_names = [
        "moviepy",
        "edge_tts",
        "openai",
        "faster_whisper",
        "ctranslate2",
        "PIL",
        "requests",
        "dashscope",
        "azure.cognitiveservices.speech",
        "litellm",
        "twelvelabs",
    ]
    dependencies = {name: bool(importlib.util.find_spec(name)) for name in dependency_names}
    from app.video_engine.utils import utils

    ffmpeg = utils.get_ffmpeg_binary()
    ffmpeg_ok = bool(ffmpeg and (Path(ffmpeg).is_file() or shutil.which(ffmpeg)))
    fonts = list((Path(utils.font_dir())).glob("*.*"))
    root = database.get_data_root()
    root.mkdir(parents=True, exist_ok=True)
    usage = shutil.disk_usage(root)
    model_root = database.get_video_generation_root() / "models"
    from app.services import voice_synthesis

    return {
        "ok": all(dependencies.values()) and ffmpeg_ok and bool(fonts),
        "dependencies": dependencies,
        "ffmpeg": {"ok": ffmpeg_ok, "path": str(ffmpeg)},
        "fonts": {"ok": bool(fonts), "count": len(fonts)},
        "whisper": {
            "model_size": str(get_settings(mask_secrets=False).get("whisper", {}).get("model_size") or "large-v3"),
            "downloaded": model_root.exists() and any(model_root.iterdir()),
            "path": str(model_root),
        },
        "voice_models": {"voxcpm2": voice_synthesis.model_status()},
        "disk": {"free": usage.free, "total": usage.total},
    }


def resolve_video_output(relative_path: str) -> Path:
    root = database.get_video_generation_root().resolve()
    candidate = (root / str(relative_path or "")).resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError("视频输出路径超出运行目录")
    if not candidate.is_file():
        raise ValueError("视频输出文件不存在")
    return candidate


def reset_video_job_for_retry(conn: Any, video_job_id: str) -> None:
    conn.execute(
        """
        UPDATE video_jobs
        SET status = 'queued', progress = 0, current_stage = 'queued', error = '', archived = 0,
            started_at = NULL, finished_at = NULL, updated_at = datetime('now', 'localtime')
        WHERE id = ?
        """,
        (video_job_id,),
    )


def mark_video_job_cancelled(video_job_id: str, reason: str) -> None:
    _finish_video_job(video_job_id, "cancelled", reason)


def mark_video_job_interrupted(video_job_id: str, reason: str) -> None:
    _finish_video_job(video_job_id, "interrupted", reason)


def mark_video_job_failed(video_job_id: str, reason: str) -> None:
    _finish_video_job(video_job_id, "failed", reason)


def _apply_runtime_settings() -> dict[str, Any]:
    from app.video_engine.config import config

    values = _runtime_settings()
    config.apply_runtime_config(values)
    return values


def _runtime_settings() -> dict[str, Any]:
    values = get_settings(mask_secrets=False)
    app_values = values.setdefault("app", {})
    app_values["local_material_directory"] = str(database.get_content_assets_root() / "originals")
    app_values["bgm_directory"] = str(database.get_content_assets_root() / "originals")
    app_values["material_directory"] = str(database.get_video_generation_root() / "cache_videos")
    app_values["enable_redis"] = False
    app_values["max_concurrent_tasks"] = 1
    return values


def _format_video_job(row: Any) -> dict[str, Any]:
    item = database.row_to_dict(row) or {}
    for key, fallback in (("params", {}), ("outputs", []), ("publish_results", [])):
        try:
            item[key] = json.loads(str(item.get(key) or json.dumps(fallback)))
        except json.JSONDecodeError:
            item[key] = fallback
    with database.connect() as conn:
        assets = conn.execute(
            """
            SELECT asset.*, link.role, link.sort_order
            FROM video_job_assets link
            JOIN content_assets asset ON asset.id = link.asset_id
            WHERE link.video_job_id = ?
            ORDER BY CASE link.role WHEN 'material' THEN 0 WHEN 'audio' THEN 1 ELSE 2 END, link.sort_order
            """,
            (str(item.get("id") or ""),),
        ).fetchall()
    item["assets"] = [database.row_to_dict(asset) or {} for asset in assets]
    return item


def _format_output(video_job_id: str, file_path: str) -> dict[str, Any]:
    root = database.get_video_generation_root().resolve()
    path = Path(file_path).resolve()
    if root not in path.parents:
        raise ValueError("生成结果不在视频运行目录中")
    relative = path.relative_to(root).as_posix()
    return {
        "name": path.name,
        "relative_path": relative,
        "size": path.stat().st_size,
        "url": f"/api/content/video-jobs/{video_job_id}/files/{path.name}",
        "upload_status": "not_uploaded",
    }


def _require_asset(asset_id: str, allowed_types: set[str]) -> dict[str, Any]:
    asset = content_assets.get_asset(asset_id)
    if str(asset.get("asset_type") or "") not in allowed_types:
        raise ValueError("选择的内容资产类型不符合当前用途")
    path = content_assets.resolve_asset_path(str(asset.get("relative_path") or ""))
    if not path.is_file():
        raise ValueError(f"内容资产文件不存在：{asset.get('name') or asset_id}")
    return asset


def _finish_video_job(video_job_id: str, status: str, error: str) -> None:
    from app.services import content_publish

    with database.connect() as conn:
        conn.execute(
            """
            UPDATE video_jobs
            SET status = ?, current_stage = ?, error = ?,
                finished_at = datetime('now', 'localtime'), updated_at = datetime('now', 'localtime')
            WHERE id = ?
            """,
            (status, status, str(error or ""), video_job_id),
        )
    if status in {"failed", "cancelled", "interrupted"}:
        content_publish.fail_waiting_video_tasks(video_job_id, error or "视频未生成")


def _reset_video_job(video_job_id: str) -> None:
    with database.connect() as conn:
        reset_video_job_for_retry(conn, video_job_id)


def _stage_for_progress(progress: int) -> str:
    if progress < 10:
        return "script"
    if progress < 20:
        return "terms"
    if progress < 30:
        return "audio"
    if progress < 40:
        return "subtitle"
    if progress < 50:
        return "materials"
    if progress < 100:
        return "rendering"
    return "completed"


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def _merge_user_settings(current: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(current)
    for key, value in incoming.items():
        if isinstance(value, dict):
            result[key] = _merge_user_settings(
                result.get(key, {}) if isinstance(result.get(key), dict) else {},
                value,
            )
        elif _is_secret_key(key) and _is_masked_secret_value(value):
            continue
        else:
            result[key] = deepcopy(value)
    return result


def _mask_settings(values: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(values)
    for key, value in list(result.items()):
        if isinstance(value, dict):
            result[key] = _mask_settings(value)
        elif _is_secret_key(key) and value:
            result[key] = [MASKED_SECRET] if isinstance(value, list) else MASKED_SECRET
    return result


def _is_secret_key(key: str) -> bool:
    normalized = str(key or "").lower()
    return any(token in normalized for token in ("api_key", "api_keys", "speech_key", "password"))


def _is_masked_secret_value(value: Any) -> bool:
    if value is None or value == "" or value == MASKED_SECRET:
        return True
    return isinstance(value, list) and all(item in {"", MASKED_SECRET} for item in value)


def _validation_message(exc: ValidationError) -> str:
    errors = exc.errors()
    if not errors:
        return "视频参数不合法"
    first = errors[0]
    field = ".".join(str(value) for value in first.get("loc", []))
    return f"视频参数 {field or '未知字段'}：{first.get('msg') or '不合法'}"
