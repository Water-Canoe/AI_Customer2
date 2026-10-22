from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import nullcontext
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from app import database
from app.services import content_assets, video_outputs


ACTIVE_TASK_STATUSES = {"waiting_media", "queued", "running"}
TERMINAL_TASK_STATUSES = {"succeeded", "failed", "review_required", "cancelled"}


def validate_auto_publish(plan: dict[str, Any], title: str = '', description: str = '') -> None:
    if not bool(plan.get("enabled")):
        return
    accounts = _selected_accounts([str(value) for value in plan.get("account_ids", [])])
    if title:
        for account in accounts:
            _task_values(account['platform'], title, description, [], {})
    if str(plan.get("output_scope") or "first") not in {"first", "all"}:
        raise ValueError("自动发布成品范围只支持首个或全部")
    _strategy(str(plan.get("publish_strategy") or "immediate"), str(plan.get("scheduled_at") or ""))


def create_video_output_tasks(
    *,
    output_ids: list[str],
    account_ids: list[str] | None = None,
    title: str = "",
    description: str | None = None,
    tags: list[str] | None = None,
    publish_strategy: str = "immediate",
    scheduled_at: str = "",
    account_overrides: dict[str, Any] | None = None,
    draft_key: str = '',
) -> list[dict[str, Any]]:
    if not output_ids or len(set(output_ids)) != len(output_ids):
        raise ValueError('请选择不重复的视频成品ID')
    outputs = [video_outputs.get_output(output_id) for output_id in output_ids]
    return _insert_tasks(
        accounts=_selected_accounts(account_ids),
        source_type="video_output",
        content_type="video",
        outputs=outputs,
        title=title,
        description=description,
        tags=tags or [],
        publish_strategy=publish_strategy,
        scheduled_at=scheduled_at,
        account_overrides=account_overrides or {},
        draft_key=draft_key,
    )


def create_asset_tasks(
    *,
    asset_ids: list[str],
    account_ids: list[str] | None,
    title: str,
    description: str = "",
    tags: list[str] | None = None,
    publish_strategy: str = "immediate",
    scheduled_at: str = "",
    account_overrides: dict[str, Any] | None = None,
    draft_key: str = '',
) -> list[dict[str, Any]]:
    assets = [content_assets.get_asset(asset_id) for asset_id in asset_ids]
    if not assets:
        raise ValueError("请选择要发布的内容资产")
    types = {str(asset["asset_type"]) for asset in assets}
    if types == {"video"} and len(assets) == 1:
        source_type, content_type = "asset_video", "video"
    elif types == {"image"}:
        source_type, content_type = "asset_images", "note"
    else:
        raise ValueError("只能选择一个视频，或选择一组图片发布图文")
    accounts = _selected_accounts(account_ids)
    if content_type == 'note' and any(account['platform'] == 'dy' for account in accounts) and len(assets) > 35:
        raise ValueError('抖音图文最多选择35张图片')
    return _insert_tasks(
        accounts=accounts,
        source_type=source_type,
        content_type=content_type,
        title=title,
        description=description,
        tags=tags or [],
        publish_strategy=publish_strategy,
        scheduled_at=scheduled_at,
        account_overrides=account_overrides or {},
        assets=assets,
        draft_key=draft_key,
    )


def create_waiting_video_tasks(
    video_job_id: str,
    account_ids: list[str],
    output_count: int,
    output_scope: str,
    publish_strategy: str,
    scheduled_at: str,
    *,
    conn: Any | None = None,
) -> list[dict[str, Any]]:
    job = _video_job(video_job_id, conn=conn)
    accounts = _selected_accounts(account_ids)
    indexes = range(max(1, int(output_count))) if output_scope == "all" else range(1)
    strategy = _strategy(publish_strategy, scheduled_at)
    schedule = _schedule_value(strategy, scheduled_at)
    batch_id = uuid.uuid4().hex
    task_ids: list[str] = []
    with database.connect() if conn is None else nullcontext(conn) as conn:
        for account in accounts:
            for output_index in indexes:
                task_id = uuid.uuid4().hex
                title, description, tags, options = _task_values(
                    account["platform"], str(job["subject"]), str(job["script"] or ""), [], {}
                )
                conn.execute(
                    """
                    INSERT INTO publish_tasks(
                        id, batch_id, account_id, source_type, content_type, video_job_id,
                        output_index, title, description, tags, publish_strategy, scheduled_at,
                        platform_options, status, current_stage
                    ) VALUES(?, ?, ?, 'video_output', 'video', ?, ?, ?, ?, ?, ?, ?, ?, 'waiting_media', 'waiting_media')
                    """,
                    (
                        task_id, batch_id, account["id"], video_job_id, output_index,
                        title, description, json.dumps(tags, ensure_ascii=False), strategy, schedule,
                        json.dumps(options, ensure_ascii=False),
                    ),
                )
                task_ids.append(task_id)
        tasks = [get_task(task_id, conn=conn) for task_id in task_ids]
    return tasks


def bind_waiting_video_tasks(video_job_id: str, outputs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from app.services import job_queue

    bound: list[str] = []
    with database.connect() as conn:
        rows = conn.execute(
            "SELECT id, output_index FROM publish_tasks WHERE video_job_id = ? AND status = 'waiting_media' ORDER BY account_id, output_index, id",
            (video_job_id,),
        ).fetchall()
        for row in rows:
            index = int(row["output_index"] or 0)
            if index >= len(outputs):
                conn.execute(
                    "UPDATE publish_tasks SET status = 'failed', current_stage = 'failed', error = '对应视频成品未生成', finished_at = datetime('now', 'localtime'), updated_at = datetime('now', 'localtime') WHERE id = ?",
                    (row["id"],),
                )
            else:
                conn.execute(
                    "UPDATE publish_tasks SET output_id = ?, output_name = ?, status = 'queued', current_stage = 'queued', updated_at = datetime('now', 'localtime') WHERE id = ?",
                    (outputs[index]['id'], str(outputs[index].get("name") or ""), row["id"]),
                )
                runtime = job_queue.enqueue_publish_task(str(row['id']), conn=conn)
                conn.execute('UPDATE publish_tasks SET runtime_job_id=? WHERE id=?', (runtime['id'], row['id']))
                bound.append(str(row["id"]))
    return [get_task(task_id) for task_id in bound]


def fail_waiting_video_tasks(video_job_id: str, reason: str) -> None:
    with database.connect() as conn:
        conn.execute(
            """
            UPDATE publish_tasks
            SET status = 'cancelled', current_stage = 'cancelled', error = ?,
                finished_at = datetime('now', 'localtime'), updated_at = datetime('now', 'localtime')
            WHERE video_job_id = ? AND status = 'waiting_media'
            """,
            (str(reason or "视频未生成"), video_job_id),
        )


def list_tasks(page: int = 1, page_size: int = 30, status: str = "") -> dict[str, Any]:
    page, page_size = max(1, int(page)), max(1, min(100, int(page_size)))
    where, params = ("WHERE t.status = ?", [status]) if status else ("", [])
    with database.connect() as conn:
        total = int(conn.execute(f"SELECT COUNT(*) FROM publish_tasks t {where}", params).fetchone()[0])
        summary_row = conn.execute(
            """
            SELECT
                SUM(CASE WHEN status IN ('waiting_media', 'queued') THEN 1 ELSE 0 END) AS pending,
                SUM(CASE WHEN status = 'running' THEN 1 ELSE 0 END) AS running,
                SUM(CASE WHEN status = 'succeeded' THEN 1 ELSE 0 END) AS succeeded,
                SUM(CASE WHEN status IN ('failed', 'review_required') THEN 1 ELSE 0 END) AS review
            FROM publish_tasks
            """
        ).fetchone()
        rows = conn.execute(
            f"""
            SELECT t.*, a.platform, a.name AS account_name
            FROM publish_tasks t JOIN publish_accounts a ON a.id = t.account_id
            {where} ORDER BY t.created_at DESC, t.id DESC LIMIT ? OFFSET ?
            """,
            [*params, page_size, (page - 1) * page_size],
        ).fetchall()
    summary = {key: int(summary_row[key] or 0) for key in ("pending", "running", "succeeded", "review")}
    summary["active"] = summary["pending"] + summary["running"]
    return {"items": [_format_task(row) for row in rows], "total": total, "page": page, "page_size": page_size, "summary": summary}


def get_task(task_id: str, *, conn: Any | None = None) -> dict[str, Any]:
    with database.connect() if conn is None else nullcontext(conn) as conn:
        row = conn.execute(
            """
            SELECT t.*, a.platform, a.name AS account_name
            FROM publish_tasks t JOIN publish_accounts a ON a.id = t.account_id WHERE t.id = ?
            """,
            (str(task_id),),
        ).fetchone()
        assets = conn.execute(
            """
            SELECT pta.role, pta.sort_order, ca.*
            FROM publish_task_assets pta JOIN content_assets ca ON ca.id = pta.asset_id
            WHERE pta.task_id = ? ORDER BY pta.role, pta.sort_order
            """,
            (str(task_id),),
        ).fetchall()
    if not row:
        raise ValueError("发布任务不存在")
    result = _format_task(row)
    result["assets"] = [dict(asset) for asset in assets]
    return result


def update_task_state(
    task_id: str,
    status: str,
    *,
    stage: str = "",
    progress: int | None = None,
    error: str = "",
    result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    get_task(task_id)
    terminal, succeeded = status in TERMINAL_TASK_STATUSES, status == "succeeded"
    with database.connect() as conn:
        conn.execute(
            """
            UPDATE publish_tasks
            SET status = ?, current_stage = ?, progress = COALESCE(?, progress), error = ?, result = ?,
                started_at = CASE WHEN ? = 'running' THEN COALESCE(started_at, datetime('now', 'localtime')) ELSE started_at END,
                finished_at = CASE WHEN ? THEN datetime('now', 'localtime') ELSE finished_at END,
                published_at = CASE WHEN ? THEN datetime('now', 'localtime') ELSE published_at END,
                updated_at = datetime('now', 'localtime') WHERE id = ?
            """,
            (
                status, stage or status, progress, str(error or ""), json.dumps(result or {}, ensure_ascii=False),
                status, int(terminal), int(succeeded), task_id,
            ),
        )
    return get_task(task_id)


def task_media_paths(task: dict[str, Any]) -> list[Path]:
    if task["source_type"] == "video_output":
        return [video_outputs.resolve_output_path(str(task['output_id'] or ''))]
    return [
        content_assets.resolve_asset_path(str(asset["relative_path"]))
        for asset in task.get("assets", []) if asset["role"] == "media"
    ]


def run_account_login(account_id: str, login_kind: str = "creator", cancel_check=None) -> dict[str, Any]:
    from app.publish_engine import service as engine
    from app.services import account_center, message_inbox, platform_login

    account = account_center.require_login_kind(account_id, login_kind)

    def qrcode_callback(payload: dict[str, Any]) -> None:
        image_path = Path(str(payload.get("image_path") or ""))
        relative = image_path.resolve().relative_to(database.get_data_root().resolve()).as_posix() if image_path.is_file() else ""
        account_center.set_account_state(account_id, "checking", qrcode_relative_path=relative)

    # 同步认证失败不等于网页登录失败；账号登录状态只由登录过程更新。
    with message_inbox.account_login_session(account_id, login_kind, cancel_check):
        account_center.set_login_status(account_id, login_kind, "checking")
        try:
            if login_kind == "creator":
                operation = engine.login_account(
                    account["platform"],
                    account_center.account_auth_path(account_id, "creator"),
                    qrcode_callback,
                    cancel_check=cancel_check,
                )
            else:
                operation = platform_login.login_account(
                    account["platform"],
                    account_center.account_auth_path(account_id, "user"),
                    cancel_check=cancel_check,
                )
            result = engine.run(operation)
            if not bool(result.get("success")):
                raise RuntimeError(str(result.get("message") or "扫码登录失败"))
            account_center.set_login_status(account_id, login_kind, "ready", checked=True)
            return {"account_id": account_id, "login_kind": login_kind, "success": True}
        except Exception as exc:
            cancelled = bool(cancel_check and cancel_check())
            account_center.set_login_status(
                account_id,
                login_kind,
                "expired" if cancelled else "error",
                "用户取消" if cancelled else str(exc),
                checked=True,
            )
            raise


def run_account_check(account_id: str, login_kind: str = "creator", cancel_check=None) -> dict[str, Any]:
    from app.publish_engine import service as engine
    from app.services import account_center, platform_login

    account = account_center.require_login_kind(account_id, login_kind)
    account_center.set_login_status(account_id, login_kind, "checking")
    try:
        if login_kind == "creator":
            operation = engine.check_account(
                account["platform"],
                account_center.account_auth_path(account_id, "creator"),
                cancel_check=cancel_check,
            )
        else:
            operation = platform_login.check_account(
                account["platform"],
                account_center.account_auth_path(account_id, "user"),
                cancel_check=cancel_check,
            )
        valid = bool(engine.run(operation))
        account_center.set_login_status(
            account_id,
            login_kind,
            "ready" if valid else "expired",
            "" if valid else "登录已失效",
            checked=True,
        )
        return {"account_id": account_id, "login_kind": login_kind, "valid": valid}
    except Exception as exc:
        cancelled = bool(cancel_check and cancel_check())
        account_center.set_login_status(
            account_id,
            login_kind,
            "expired" if cancelled else "error",
            "用户取消" if cancelled else str(exc),
            checked=True,
        )
        raise


def run_publish_task(task_id: str) -> dict[str, Any]:
    from app.publish_engine import service as engine
    from app.services import account_center, job_queue

    task = get_task(task_id)
    try:
        account = account_center.get_account(str(task["account_id"]), feature="publish", require_ready=True)
    except ValueError as exc:
        update_task_state(task_id, "failed", error=str(exc))
        raise RuntimeError(str(exc)) from exc
    with database.connect() as conn:
        conn.execute(
            "UPDATE publish_tasks SET attempt = attempt + 1, updated_at = datetime('now', 'localtime') WHERE id = ?",
            (task_id,),
        )
    update_task_state(task_id, "running", stage="preparing", progress=5)
    progress = {"preparing": 10, "uploading": 45, "publishing": 85, "completed": 100}

    def stage_callback(stage: str) -> None:
        update_task_state(task_id, "running", stage=stage, progress=progress.get(stage, 10))

    try:
        scheduled = datetime.fromisoformat(task["scheduled_at"]) if task.get("scheduled_at") else 0
        result = engine.run(
            engine.publish(
                platform=account["platform"],
                content_type=task["content_type"],
                account_file=account_center.account_auth_path(str(account["id"])),
                title=task["title"],
                description=task["description"],
                tags=task["tags"],
                media_paths=task_media_paths(task),
                publish_strategy=task["publish_strategy"],
                publish_date=scheduled,
                options=task["platform_options"],
                stage_callback=stage_callback,
                cancel_check=lambda: job_queue.is_entity_cancel_requested("content_publish", task_id),
            )
        )
        update_task_state(task_id, "succeeded", stage="completed", progress=100, result=result)
        return result
    except engine.PublishCancelled as exc:
        update_task_state(task_id, "cancelled", error=str(exc))
        return {"cancelled": True}
    except engine.PublishReviewRequired as exc:
        update_task_state(task_id, "review_required", stage="review_required", progress=90, error=str(exc))
        raise
    except Exception as exc:
        update_task_state(task_id, "failed", error=str(exc))
        raise


def cancel_task(task_id: str) -> dict[str, Any]:
    from app.services import job_queue

    task = get_task(task_id)
    if task["status"] not in ACTIVE_TASK_STATUSES:
        raise ValueError("该发布任务当前不能取消")
    jobs = job_queue.cancel_by_entity("content_publish", task_id)
    if not jobs and task["status"] == "waiting_media":
        return update_task_state(task_id, "cancelled", error="用户取消")
    return get_task(task_id)


def retry_task(task_id: str) -> dict[str, Any]:
    from app.services import job_queue

    task = get_task(task_id)
    if task["status"] not in {"failed", "cancelled"}:
        raise ValueError("只允许重试确认未发布的失败或取消任务；结果不明确请先人工核实")
    with database.connect() as conn:
        row = conn.execute(
            "SELECT id FROM runtime_jobs WHERE kind = 'content_publish' AND entity_id = ? ORDER BY created_at DESC LIMIT 1",
            (task_id,),
        ).fetchone()
    if row:
        job_queue.retry_job(str(row["id"]))
    else:
        with database.connect() as conn:
            conn.execute("UPDATE publish_tasks SET status='queued',current_stage='queued',progress=0,error='',result='{}',started_at=NULL,finished_at=NULL,published_at=NULL,updated_at=datetime('now','localtime') WHERE id=?", (task_id,))
            runtime = job_queue.enqueue_publish_task(task_id, conn=conn)
            conn.execute('UPDATE publish_tasks SET runtime_job_id=? WHERE id=?', (runtime['id'], task_id))
    return get_task(task_id)


def mark_task_result(task_id: str, status: str, note: str = "") -> dict[str, Any]:
    if status not in {"succeeded", "failed"}:
        raise ValueError("人工确认结果只支持已发布或失败")
    task = get_task(task_id)
    if task["status"] not in TERMINAL_TASK_STATUSES:
        raise ValueError("运行中的任务不能人工修改结果")
    return update_task_state(task_id, status, error="" if status == "succeeded" else note, result={"manual": True, "note": note})


def _insert_tasks(
    *,
    accounts: list[dict[str, Any]],
    source_type: str,
    content_type: str,
    title: str,
    description: str | None,
    tags: list[str],
    publish_strategy: str,
    scheduled_at: str,
    account_overrides: dict[str, Any],
    outputs: list[dict[str, Any]] | None = None,
    assets: list[dict[str, Any]] | None = None,
    draft_key: str = '',
) -> list[dict[str, Any]]:
    from app.services import job_queue

    if not outputs and not str(title or "").strip():
        raise ValueError("发布标题不能为空")
    if set(account_overrides) - {account['id'] for account in accounts}:
        raise ValueError('逐账号发布文案包含未选中的账号')
    batch_id, task_ids = uuid.uuid4().hex, []
    with database.connect() as conn:
        for account in accounts:
            override = account_overrides.get(account['id'], {})
            strategy = _strategy(str(override.get('publish_strategy', publish_strategy)), str(override.get('scheduled_at', scheduled_at)))
            schedule = _schedule_value(strategy, str(override.get('scheduled_at', scheduled_at)))
            for output in outputs or [None]:
                task_id = uuid.uuid4().hex
                task_title, task_description, task_tags, options = _task_values(
                    account['platform'], title or (output['subject'] if output else ''),
                    description if description is not None else (output['script'] if output else ''), tags, override,
                )
                conn.execute(
                    """INSERT INTO publish_tasks(id,batch_id,account_id,source_type,content_type,video_job_id,output_id,output_name,output_index,title,description,tags,publish_strategy,scheduled_at,platform_options)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (task_id,batch_id,account['id'],source_type,content_type,output['video_job_id'] if output else None,
                     output['id'] if output else None,output['name'] if output else '',output['index'] if output else 0,
                     task_title,task_description,json.dumps(task_tags,ensure_ascii=False),strategy,schedule,json.dumps(options,ensure_ascii=False)),
                )
                for index, asset in enumerate(assets or []):
                    conn.execute("INSERT INTO publish_task_assets(task_id,asset_id,role,sort_order) VALUES(?,?,'media',?)", (task_id,asset['id'],index))
                runtime = job_queue.enqueue_publish_task(task_id, conn=conn)
                conn.execute('UPDATE publish_tasks SET runtime_job_id=? WHERE id=?', (runtime['id'], task_id))
                task_ids.append(task_id)
        from app.services import drafts
        drafts.record_submission(conn, 'publish', draft_key, {'route': '/content-publish', 'query': {'task': task_ids[0]}, 'selection': task_ids})
    return [get_task(task_id) for task_id in task_ids]


def _selected_accounts(account_ids: list[str] | None) -> list[dict[str, Any]]:
    from app.services import account_center

    requested = set(account_ids or [])
    accounts = account_center.list_accounts(feature="publish")
    selected = [item for item in accounts if item["id"] in requested] if requested else [item for item in accounts if "publish" in item["default_features"]]
    selected = [
        item for item in selected
        if item["enabled"] and item["feature_status"].get("publish", {}).get("status") == "ready"
    ]
    if not selected:
        raise ValueError("没有可用的默认发布账号，请先完成扫码登录并设为默认账号")
    if requested and len(selected) != len(requested):
        raise ValueError("部分发布账号不存在、已停用或登录失效")
    return selected


def _video_job(video_job_id: str, *, conn: Any | None = None) -> dict[str, Any]:
    with database.connect() if conn is None else nullcontext(conn) as conn:
        row = conn.execute("SELECT * FROM video_jobs WHERE id = ?", (str(video_job_id),)).fetchone()
    if not row:
        raise ValueError("视频任务不存在")
    return dict(row)


def _task_values(platform: str, title: str, description: str, tags: list[str], overrides: dict[str, Any]) -> tuple[str, str, list[str], dict[str, Any]]:
    override = overrides if isinstance(overrides, dict) else {}
    clean_tags: list[str] = []
    raw_tags = override.get("tags", tags) if isinstance(override, dict) else tags
    for tag in raw_tags if isinstance(raw_tags, list) else []:
        value = str(tag or "").strip().lstrip("#")
        if value and value not in clean_tags:
            clean_tags.append(value)
    clean_title = str(override.get('title', title) or '').strip()
    clean_description = str(override.get('description', description) or '').strip()
    if not clean_title or len(clean_title) > 20:
        raise ValueError(f'{platform} 发布标题需为1至20个字符，请调整文案')
    if len(clean_description) > 1000:
        raise ValueError(f'{platform} 发布正文不能超过1000个字符，请调整文案')
    if len(clean_tags) > 5:
        raise ValueError(f'{platform} 发布标签最多5个，请调整标签')
    options = override.get("options", {}) if isinstance(override, dict) else {}
    return (
        clean_title,
        clean_description,
        clean_tags,
        options if isinstance(options, dict) else {},
    )


def _strategy(value: str, scheduled_at: str) -> str:
    strategy = str(value or "immediate")
    if strategy not in {"immediate", "scheduled"}:
        raise ValueError("发布方式只支持立即发布或定时发布")
    if strategy == "scheduled":
        _schedule_value(strategy, scheduled_at)
    return strategy


def _schedule_value(strategy: str, value: str) -> str | None:
    if strategy != "scheduled":
        return None
    try:
        scheduled = datetime.fromisoformat(str(value or ""))
    except ValueError as exc:
        raise ValueError("定时发布时间格式不正确") from exc
    if scheduled.tzinfo is not None:
        scheduled = scheduled.astimezone().replace(tzinfo=None)
    if scheduled <= datetime.now() + timedelta(hours=2):
        raise ValueError("定时发布时间必须晚于当前时间至少2小时")
    return scheduled.strftime("%Y-%m-%d %H:%M:%S")


def _format_task(row: sqlite3.Row) -> dict[str, Any]:
    value = dict(row)
    for key, fallback in (("tags", []), ("platform_options", {}), ("result", {})):
        try:
            value[key] = json.loads(value.get(key) or json.dumps(fallback))
        except json.JSONDecodeError:
            value[key] = fallback
    return value
