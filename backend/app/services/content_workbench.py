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

from app import database, protected_settings
from app.services import content_assets, video_outputs


TERMINAL_VIDEO_STATUSES = {"succeeded", "failed", "cancelled", "interrupted"}


class VideoJobCancelled(RuntimeError):
    pass


def default_settings() -> dict[str, Any]:
    path = Path(__file__).resolve().parents[1] / "video_engine" / "config.default.toml"
    return toml.load(path) | {'model_connection_mode': 'shared'}


def get_settings(*, mask_secrets: bool = True) -> dict[str, Any]:
    with database.connect() as conn:
        raw = database.get_setting(conn, "content_video_config", "{}")
    try:
        saved = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        saved = {}
    result = _deep_merge(default_settings(), saved if isinstance(saved, dict) else {})
    if not mask_secrets:
        return result
    public = _mask_settings(result)
    mode = str(result.get('model_connection_mode') or 'shared')
    if mode == 'shared':
        with database.connect() as conn:
            public['model_connection'] = {'mode': mode, 'base_url': database.get_setting(conn, 'ai_base_url'), 'model': database.get_setting(conn, 'ai_model'), 'api_key_configured': bool(database.get_setting(conn, 'ai_api_key'))}
    else:
        app_values = result.get('app', {})
        provider = str(app_values.get('llm_provider') or '')
        public['model_connection'] = {'mode': mode, 'provider': provider, 'base_url': app_values.get(f'{provider}_base_url', ''), 'model': app_values.get(f'{provider}_model_name', ''), 'api_key_configured': bool(app_values.get(f'{provider}_api_key'))}
    return public


def update_settings(values: dict[str, Any], clear_secrets: list[str] | None = None) -> dict[str, Any]:
    current = get_settings(mask_secrets=False)
    merged = _merge_user_settings(current, values if isinstance(values, dict) else {})
    if merged.get('model_connection_mode') not in {'shared', 'custom'}:
        raise ValueError('模型连接模式仅支持复用全局或单独配置')
    for path in clear_secrets or []:
        parts = path.split('.')
        target = merged
        for part in parts[:-1]:
            target = target.get(part, {}) if isinstance(target, dict) else {}
        key = parts[-1]
        if not isinstance(target, dict) or key not in target or not _is_secret_key(key):
            raise ValueError('清除密钥字段不存在')
        target[key] = [] if isinstance(target[key], list) else ''
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
    draft_key: str = '',
) -> dict[str, Any]:
    from app.video_engine.models.schema import TaskVideoRequest
    from app.services import content_publish
    from app.services import job_queue

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
    content_publish.validate_auto_publish(publish_plan, str(validated.video_subject), str(validated.video_script or ''))
    if draft_key:
        from app.services import drafts
        if not drafts.get_draft('video', draft_key)['exists']:
            raise ValueError('视频草稿不存在')

    job_id = uuid.uuid4().hex
    serialized = validated.model_dump(mode="json", warnings=False)
    _validate_voice_reference(serialized)
    with database.connect() as conn:
        conn.execute(
            """
            INSERT INTO video_jobs(id, subject, script, params, draft_key, status, current_stage)
            VALUES(?, ?, ?, ?, ?, 'queued', 'queued')
            """,
            (
                job_id,
                str(validated.video_subject),
                str(validated.video_script or ""),
                json.dumps(serialized, ensure_ascii=False),
                draft_key,
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
        from app.services import drafts
        drafts.record_submission(conn, 'video', draft_key, {'route': '/content-records', 'query': {'job': job_id}, 'selection': []})
        if bool(publish_plan.get("enabled")):
            content_publish.create_waiting_video_tasks(
                job_id,
                [str(value) for value in publish_plan.get("account_ids", [])],
                int(serialized.get("video_count") or 1),
                str(publish_plan.get("output_scope") or "first"),
                str(publish_plan.get("publish_strategy") or "immediate"),
                str(publish_plan.get("scheduled_at") or ""),
                conn=conn,
            )
        runtime_job = job_queue.enqueue_video_job(job_id, conn=conn)
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
        # 引擎发送局部更新；仅状态通知不能把已有进度重置为零。
        if "progress" not in snapshot:
            return
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
        # 先核对本地输入，避免素材已删除后仍先消耗脚本或配音额度。
        _validate_video_inputs(job)
        result = task.start(engine_task_id, params, stop_at="video")
        if not result or not result.get("videos"):
            raise RuntimeError("视频生成未产生有效成品，请查看任务错误后重试")
        with database.connect() as conn:
            script = str(result.get('script') or params.video_script or '')
            output_ids = video_outputs.register_outputs(conn, video_job_id, attempt, result['videos'], script)
            conn.execute(
                """
                UPDATE video_jobs
                SET status = 'succeeded', progress = 100, current_stage = 'completed',
                    script = ?, error = '',
                    finished_at = datetime('now', 'localtime'), updated_at = datetime('now', 'localtime')
                WHERE id = ?
                """,
                (
                    script,
                    video_job_id,
                ),
            )
        content_publish.bind_waiting_video_tasks(video_job_id, [video_outputs.get_output(output_id) for output_id in output_ids])
        return get_video_job(video_job_id, include_archived=True)
    except VideoJobCancelled as exc:
        _finish_video_job(video_job_id, "cancelled", str(exc))
        return {"id": video_job_id, "status": "cancelled"}
    except Exception as exc:
        _finish_video_job(video_job_id, "failed", str(exc))
        raise
    finally:
        state.set_progress_listener(None)


def list_video_jobs(page: int = 1, page_size: int = 20, include_archived: bool = False, search: str = '') -> dict[str, Any]:
    safe_page = max(1, int(page))
    safe_size = max(1, min(100, int(page_size)))
    where = "1 = 1" if include_archived else "archived = 0"
    params: list[Any] = []
    if search:
        where += ' AND instr(lower(subject),lower(?)) > 0'
        params.append(search)
    with database.connect() as conn:
        total = int(conn.execute(f"SELECT COUNT(*) FROM video_jobs WHERE {where}", params).fetchone()[0])
        active = int(conn.execute(
            f"SELECT COUNT(*) FROM video_jobs WHERE {where} AND status IN ('queued', 'running')", params
        ).fetchone()[0])
        rows = conn.execute(
            f"SELECT * FROM video_jobs WHERE {where} ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
            [*params, safe_size, (safe_page - 1) * safe_size],
        ).fetchall()
        items = _format_video_jobs(rows, conn)
    return {
        "items": items,
        "total": total,
        "page": safe_page,
        "page_size": safe_size,
        "active": active,
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


def update_video_output_status(video_job_id: str, output_id: str, upload_status: str) -> dict[str, Any]:
    with database.connect() as conn:
        cursor = conn.execute('UPDATE video_outputs SET upload_status=? WHERE id=? AND video_job_id=?', (upload_status, output_id, video_job_id))
        if not cursor.rowcount:
            raise ValueError('视频成品不存在')
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
        job_queue.retry_job(str(runtime["id"]))
    else:
        with database.connect() as conn:
            reset_video_job_for_retry(conn, video_job_id)
            runtime_job = job_queue.enqueue_video_job(video_job_id, conn=conn)
            conn.execute('UPDATE video_jobs SET runtime_job_id=? WHERE id=?', (runtime_job['id'], video_job_id))
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
        "cloakbrowser",
        "cv2",
        "qrcode",
        "segno",
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
    from app.services import job_queue, voice_synthesis

    voice_status = voice_synthesis.model_status()
    install_jobs = job_queue.list_jobs(kind="voice_runtime_install", page=1, page_size=1)["items"]
    voice_status["install_job"] = install_jobs[0] if install_jobs else None

    publish_browser = ""
    try:
        from cloakbrowser import binary_info

        publish_browser = str(binary_info().get("binary_path") or "")
    except Exception:
        pass
    publish_ok = all(dependencies.get(name, False) for name in ("cloakbrowser", "cv2", "qrcode", "segno")) and Path(publish_browser).is_file()

    return {
        "ok": all(dependencies.values()) and ffmpeg_ok and bool(fonts) and publish_ok,
        "dependencies": dependencies,
        "ffmpeg": {"ok": ffmpeg_ok, "path": str(ffmpeg)},
        "fonts": {"ok": bool(fonts), "count": len(fonts)},
        "whisper": {
            "model_size": str(get_settings(mask_secrets=False).get("whisper", {}).get("model_size") or "large-v3"),
            "downloaded": model_root.exists() and any(model_root.iterdir()),
            "path": str(model_root),
        },
        "voice_models": {"voxcpm2": voice_status},
        "social_publish": {"ok": publish_ok, "browser_path": publish_browser},
        "disk": {"free": usage.free, "total": usage.total},
    }


def reset_video_job_for_retry(conn: Any, video_job_id: str) -> None:
    row = conn.execute('SELECT * FROM video_jobs WHERE id=?', (video_job_id,)).fetchone()
    if not row:
        raise ValueError('视频任务不存在')
    _validate_video_inputs(_format_video_jobs([row], conn)[0])
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
    if values.get('model_connection_mode') == 'shared':
        # 执行时解析同一连接引用，密钥不会复制到内容配置、草稿或生成参数快照。
        with database.connect() as conn:
            app_values.update({'llm_provider': 'openai', 'openai_base_url': database.get_setting(conn, 'ai_base_url'), 'openai_model_name': database.get_setting(conn, 'ai_model'), 'openai_api_key': database.get_setting(conn, 'ai_api_key')})
    app_values["local_material_directory"] = str(database.get_content_assets_root() / "originals")
    app_values["bgm_directory"] = str(database.get_content_assets_root() / "originals")
    app_values["material_directory"] = str(database.get_video_generation_root() / "cache_videos")
    return values


def _format_video_job(row: Any) -> dict[str, Any]:
    with database.connect() as conn:
        return _format_video_jobs([row], conn)[0]


def _format_video_jobs(rows: list[Any], conn: Any) -> list[dict[str, Any]]:
    items = [dict(row) for row in rows]
    if not items:
        return []
    ids = [item['id'] for item in items]
    outputs = video_outputs.outputs_for_jobs(ids, conn)
    assets = conn.execute(f"SELECT asset.*,link.video_job_id,link.role,link.sort_order FROM video_job_assets link JOIN content_assets asset ON asset.id=link.asset_id WHERE link.video_job_id IN ({','.join('?' for _ in ids)}) ORDER BY CASE link.role WHEN 'material' THEN 0 WHEN 'audio' THEN 1 ELSE 2 END,link.sort_order", ids).fetchall()
    by_job: dict[str, list[dict[str, Any]]] = {job_id: [] for job_id in ids}
    for asset in assets:
        by_job[asset['video_job_id']].append(dict(asset))
    for item in items:
        item['params'] = json.loads(item['params'])
        item['outputs'] = outputs[item['id']]
        item['assets'] = by_job[item['id']]
    return items


def _require_asset(asset_id: str, allowed_types: set[str]) -> dict[str, Any]:
    asset = content_assets.get_asset(asset_id)
    if str(asset.get("asset_type") or "") not in allowed_types:
        raise ValueError("选择的内容资产类型不符合当前用途")
    path = content_assets.resolve_asset_path(str(asset.get("relative_path") or ""))
    if not path.is_file():
        raise ValueError(f"内容资产文件不存在：{asset.get('name') or asset_id}")
    return asset


def _validate_video_inputs(job: dict[str, Any]) -> None:
    for asset in job['assets']:
        _require_asset(str(asset['id']), {'video', 'image'} if asset['role'] == 'material' else {'audio'})
    _validate_voice_reference(job['params'])


def _validate_voice_reference(params: dict[str, Any]) -> None:
    voice_name = str(params.get('voice_name') or '')
    if params.get('custom_audio_file') or not voice_name.startswith('voxcpm2:'):
        return
    from app.services import voice_profiles

    profile = voice_profiles.get_profile(voice_name.split(':', 1)[1])
    if not profile['consent_confirmed']:
        raise ValueError('克隆音色尚未确认参考声音的使用授权')
    _require_asset(str(profile['reference_asset_id']), {'audio'})


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
        if key not in current:
            continue
        if isinstance(value, dict):
            result[key] = _merge_user_settings(
                result.get(key, {}) if isinstance(result.get(key), dict) else {},
                value,
            )
        elif _is_secret_key(key) and (value is None or value == '' or value == []):
            continue
        else:
            if 'base_url' in key:
                protected_settings.validate_connection_url(str(value or ''))
            result[key] = deepcopy(value)
    return result


def _mask_settings(values: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(values)
    for key, value in list(result.items()):
        if isinstance(value, dict):
            result[key] = _mask_settings(value)
        elif _is_secret_key(key):
            result[f'{key}_configured'] = bool(value)
            result[key] = [] if isinstance(value, list) else ''
    return result


def _is_secret_key(key: str) -> bool:
    normalized = str(key or "").lower()
    return any(token in normalized for token in ("api_key", "api_keys", "speech_key", "password"))


def _validation_message(exc: ValidationError) -> str:
    errors = exc.errors()
    if not errors:
        return "视频参数不合法"
    first = errors[0]
    field = ".".join(str(value) for value in first.get("loc", []))
    return f"视频参数 {field or '未知字段'}：{first.get('msg') or '不合法'}"
