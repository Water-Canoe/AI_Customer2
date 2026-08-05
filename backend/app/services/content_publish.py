from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from app import database
from app.services import content_assets


PLATFORMS = {"dy", "ks", "xhs"}
ACTIVE_TASK_STATUSES = {"waiting_media", "queued", "running"}
TERMINAL_TASK_STATUSES = {"succeeded", "failed", "review_required", "cancelled"}
ACCOUNT_STATUSES = {"login_required", "checking", "ready", "expired", "error"}


def validate_auto_publish(plan: dict[str, Any]) -> None:
    if not bool(plan.get("enabled")):
        return
    _selected_accounts([str(value) for value in plan.get("account_ids", [])])
    if str(plan.get("output_scope") or "first") not in {"first", "all"}:
        raise ValueError("自动发布成品范围只支持首个或全部")
    _strategy(str(plan.get("publish_strategy") or "immediate"), str(plan.get("scheduled_at") or ""))


def create_account(platform: str, name: str) -> dict[str, Any]:
    platform = str(platform or "").strip().lower()
    name = str(name or "").strip()
    if platform not in PLATFORMS:
        raise ValueError("发布平台只支持抖音、快手和小红书")
    if not name:
        raise ValueError("账号名称不能为空")
    account_id = uuid.uuid4().hex
    try:
        with database.connect() as conn:
            conn.execute(
                "INSERT INTO publish_accounts(id, platform, name, auth_relative_path) VALUES(?, ?, ?, ?)",
                (account_id, platform, name[:100], f"platform_accounts/{platform}/{account_id}/creator-profile"),
            )
    except sqlite3.IntegrityError as exc:
        raise ValueError("同一平台下账号名称不能重复") from exc
    return get_account(account_id)


def list_accounts(*, include_deleted: bool = False) -> list[dict[str, Any]]:
    where = "1 = 1" if include_deleted else "deleted_at IS NULL"
    with database.connect() as conn:
        rows = conn.execute(
            f"SELECT * FROM publish_accounts WHERE {where} ORDER BY platform, is_default DESC, created_at DESC"
        ).fetchall()
    return [_format_account(row) for row in rows]


def get_account(account_id: str, *, include_deleted: bool = False) -> dict[str, Any]:
    with database.connect() as conn:
        row = conn.execute("SELECT * FROM publish_accounts WHERE id = ?", (str(account_id),)).fetchone()
    if not row or (row["deleted_at"] and not include_deleted):
        raise ValueError("平台账号不存在")
    return _format_account(row)


def delete_account(account_id: str) -> dict[str, Any]:
    account = get_account(account_id)
    with database.connect() as conn:
        active = conn.execute(
            "SELECT 1 FROM publish_tasks WHERE account_id = ? AND status IN ('waiting_media', 'queued', 'running') LIMIT 1",
            (account_id,),
        ).fetchone()
        if active:
            raise RuntimeError("该账号还有待发布或运行中的任务，暂时不能删除")
        conn.execute(
            "UPDATE publish_accounts SET deleted_at = datetime('now', 'localtime'), enabled = 0, is_default = 0, updated_at = datetime('now', 'localtime') WHERE id = ?",
            (account_id,),
        )
    # 账号采用持久化 Profile，软删除记录时保留用户登录数据，避免误删整个目录。
    return {"id": account_id, "deleted": True}


def set_account_state(
    account_id: str,
    status: str,
    *,
    error: str = "",
    qrcode_relative_path: str = "",
    checked: bool = False,
) -> dict[str, Any]:
    if status not in ACCOUNT_STATUSES:
        raise ValueError("未知账号状态")
    get_account(account_id, include_deleted=True)
    checked_sql = ", last_checked_at = datetime('now', 'localtime')" if checked else ""
    with database.connect() as conn:
        conn.execute(
            f"""
            UPDATE publish_accounts
            SET status = ?, last_error = ?, qrcode_relative_path = ?,
                updated_at = datetime('now', 'localtime'){checked_sql}
            WHERE id = ?
            """,
            (status, str(error or ""), str(qrcode_relative_path or ""), account_id),
        )
    return get_account(account_id, include_deleted=True)


def create_video_output_tasks(
    *,
    video_job_id: str,
    output_name: str,
    account_ids: list[str] | None = None,
    title: str = "",
    description: str = "",
    tags: list[str] | None = None,
    publish_strategy: str = "immediate",
    scheduled_at: str = "",
    platform_overrides: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    job = _video_job(video_job_id)
    output = next((item for item in job["outputs"] if str(item.get("name") or "") == output_name), None)
    if not output:
        raise ValueError("视频成品不存在")
    return _insert_tasks(
        accounts=_selected_accounts(account_ids),
        source_type="video_output",
        content_type="video",
        video_job_id=video_job_id,
        output_name=output_name,
        title=title or str(job["subject"]),
        description=description or str(job["script"] or ""),
        tags=tags or [],
        publish_strategy=publish_strategy,
        scheduled_at=scheduled_at,
        platform_overrides=platform_overrides or {},
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
    platform_overrides: dict[str, Any] | None = None,
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
    return _insert_tasks(
        accounts=_selected_accounts(account_ids),
        source_type=source_type,
        content_type=content_type,
        title=title,
        description=description,
        tags=tags or [],
        publish_strategy=publish_strategy,
        scheduled_at=scheduled_at,
        platform_overrides=platform_overrides or {},
        assets=assets,
    )


def create_waiting_video_tasks(
    video_job_id: str,
    account_ids: list[str],
    output_count: int,
    output_scope: str,
    publish_strategy: str,
    scheduled_at: str,
) -> list[dict[str, Any]]:
    job = _video_job(video_job_id)
    accounts = _selected_accounts(account_ids)
    indexes = range(max(1, int(output_count))) if output_scope == "all" else range(1)
    strategy = _strategy(publish_strategy, scheduled_at)
    schedule = _schedule_value(strategy, scheduled_at)
    batch_id = uuid.uuid4().hex
    task_ids: list[str] = []
    with database.connect() as conn:
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
    return [get_task(task_id) for task_id in task_ids]


def bind_waiting_video_tasks(video_job_id: str, outputs: list[dict[str, Any]]) -> list[dict[str, Any]]:
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
                    "UPDATE publish_tasks SET output_name = ?, status = 'queued', current_stage = 'queued', updated_at = datetime('now', 'localtime') WHERE id = ?",
                    (str(outputs[index].get("name") or ""), row["id"]),
                )
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


def get_task(task_id: str) -> dict[str, Any]:
    with database.connect() as conn:
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


def resolve_runtime_path(relative_path: str) -> Path:
    root = database.get_data_root().resolve()
    path = (root / str(relative_path or "")).resolve()
    if path == root or root not in path.parents:
        raise ValueError("发布运行路径超出数据目录")
    return path


def account_auth_path(account_id: str, login_kind: str = "creator") -> Path:
    get_account(account_id, include_deleted=True)
    if login_kind not in {"user", "creator"}:
        raise ValueError("未知登录类型")
    creator_path = resolve_runtime_path(_auth_relative_path(account_id))
    path = creator_path if login_kind == "creator" else creator_path.parent / "user-profile"
    path.mkdir(parents=True, exist_ok=True)
    return path


def account_qrcode_path(account_id: str) -> Path:
    with database.connect() as conn:
        row = conn.execute("SELECT qrcode_relative_path FROM publish_accounts WHERE id = ?", (account_id,)).fetchone()
    if not row or not row["qrcode_relative_path"]:
        raise ValueError("登录二维码尚未生成")
    path = resolve_runtime_path(str(row["qrcode_relative_path"]))
    if not path.is_file():
        raise ValueError("登录二维码不存在或已失效")
    return path


def task_media_paths(task: dict[str, Any]) -> list[Path]:
    if task["source_type"] == "video_output":
        from app.services import content_workbench

        job = content_workbench.get_video_job(str(task["video_job_id"]), include_archived=True)
        output = next((item for item in job["outputs"] if str(item.get("name") or "") == task["output_name"]), None)
        if not output:
            raise ValueError("视频成品不存在")
        return [content_workbench.resolve_video_output(str(output["relative_path"]))]
    return [
        content_assets.resolve_asset_path(str(asset["relative_path"]))
        for asset in task.get("assets", []) if asset["role"] == "media"
    ]


def run_account_login(account_id: str, login_kind: str = "creator", cancel_check=None) -> dict[str, Any]:
    from app.publish_engine import service as engine
    from app.services import account_center, platform_login

    account = account_center.require_login_kind(account_id, login_kind)
    account_center.set_login_status(account_id, login_kind, "checking")

    def qrcode_callback(payload: dict[str, Any]) -> None:
        image_path = Path(str(payload.get("image_path") or ""))
        relative = image_path.resolve().relative_to(database.get_data_root().resolve()).as_posix() if image_path.is_file() else ""
        set_account_state(account_id, "checking", qrcode_relative_path=relative)

    try:
        if login_kind == "creator":
            operation = engine.login_account(
                account["platform"],
                account_auth_path(account_id, "creator"),
                qrcode_callback,
                cancel_check=cancel_check,
            )
        else:
            operation = platform_login.login_account(
                account["platform"],
                account_auth_path(account_id, "user"),
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
                account_auth_path(account_id, "creator"),
                cancel_check=cancel_check,
            )
        else:
            operation = platform_login.check_account(
                account["platform"],
                account_auth_path(account_id, "user"),
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
                account_file=account_auth_path(str(account["id"])),
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
    if task["status"] not in TERMINAL_TASK_STATUSES:
        raise ValueError("只有已结束的发布任务可以重试")
    with database.connect() as conn:
        row = conn.execute(
            "SELECT id FROM runtime_jobs WHERE kind = 'content_publish' AND entity_id = ? ORDER BY created_at DESC LIMIT 1",
            (task_id,),
        ).fetchone()
    update_task_state(task_id, "queued", stage="queued", progress=0)
    runtime = job_queue.retry_job(str(row["id"])) if row else job_queue.enqueue_publish_task(task_id)
    _set_runtime_job(task_id, str(runtime["id"]))
    return get_task(task_id)


def mark_task_result(task_id: str, status: str, note: str = "") -> dict[str, Any]:
    if status not in {"succeeded", "failed"}:
        raise ValueError("人工确认结果只支持已发布或失败")
    task = get_task(task_id)
    if task["status"] not in TERMINAL_TASK_STATUSES:
        raise ValueError("运行中的任务不能人工修改结果")
    return update_task_state(task_id, status, error="" if status == "succeeded" else note, result={"manual": True, "note": note})


def enqueue_tasks(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from app.services import job_queue

    result = []
    for task in tasks:
        if task["status"] != "queued":
            result.append(task)
            continue
        runtime = job_queue.enqueue_publish_task(str(task["id"]))
        _set_runtime_job(str(task["id"]), str(runtime["id"]))
        result.append(get_task(str(task["id"])))
    return result


def _set_runtime_job(task_id: str, runtime_job_id: str) -> None:
    with database.connect() as conn:
        conn.execute(
            "UPDATE publish_tasks SET runtime_job_id = ?, updated_at = datetime('now', 'localtime') WHERE id = ?",
            (runtime_job_id, task_id),
        )


def _insert_tasks(
    *,
    accounts: list[dict[str, Any]],
    source_type: str,
    content_type: str,
    title: str,
    description: str,
    tags: list[str],
    publish_strategy: str,
    scheduled_at: str,
    platform_overrides: dict[str, Any],
    video_job_id: str | None = None,
    output_name: str = "",
    assets: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    if not str(title or "").strip():
        raise ValueError("发布标题不能为空")
    strategy = _strategy(publish_strategy, scheduled_at)
    schedule = _schedule_value(strategy, scheduled_at)
    batch_id, task_ids = uuid.uuid4().hex, []
    with database.connect() as conn:
        for account in accounts:
            task_id = uuid.uuid4().hex
            task_title, task_description, task_tags, options = _task_values(
                account["platform"], title, description, tags, platform_overrides
            )
            conn.execute(
                """
                INSERT INTO publish_tasks(
                    id, batch_id, account_id, source_type, content_type, video_job_id, output_name,
                    title, description, tags, publish_strategy, scheduled_at, platform_options
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id, batch_id, account["id"], source_type, content_type, video_job_id, output_name,
                    task_title, task_description, json.dumps(task_tags, ensure_ascii=False), strategy,
                    schedule, json.dumps(options, ensure_ascii=False),
                ),
            )
            for index, asset in enumerate(assets or []):
                conn.execute(
                    "INSERT INTO publish_task_assets(task_id, asset_id, role, sort_order) VALUES(?, ?, 'media', ?)",
                    (task_id, asset["id"], index),
                )
            task_ids.append(task_id)
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


def _video_job(video_job_id: str) -> dict[str, Any]:
    with database.connect() as conn:
        row = conn.execute("SELECT * FROM video_jobs WHERE id = ?", (str(video_job_id),)).fetchone()
    if not row:
        raise ValueError("视频任务不存在")
    result = dict(row)
    result["outputs"] = json.loads(result.get("outputs") or "[]")
    return result


def _task_values(platform: str, title: str, description: str, tags: list[str], overrides: dict[str, Any]) -> tuple[str, str, list[str], dict[str, Any]]:
    override = overrides.get(platform, {}) if isinstance(overrides, dict) else {}
    clean_tags: list[str] = []
    raw_tags = override.get("tags", tags) if isinstance(override, dict) else tags
    for tag in raw_tags if isinstance(raw_tags, list) else []:
        value = str(tag or "").strip().lstrip("#")
        if value and value not in clean_tags:
            clean_tags.append(value)
        if len(clean_tags) >= 5:
            break
    options = override.get("options", {}) if isinstance(override, dict) else {}
    return (
        str(override.get("title") or title or "").strip()[:20],
        str(override.get("description") or description or "").strip()[:1000],
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
    if scheduled <= datetime.now() + timedelta(hours=2):
        raise ValueError("定时发布时间必须晚于当前时间至少2小时")
    return scheduled.strftime("%Y-%m-%d %H:%M:%S")


def _auth_relative_path(account_id: str) -> str:
    with database.connect() as conn:
        row = conn.execute("SELECT auth_relative_path FROM publish_accounts WHERE id = ?", (account_id,)).fetchone()
    if not row:
        raise ValueError("平台账号不存在")
    return str(row["auth_relative_path"])


def _format_account(row: sqlite3.Row) -> dict[str, Any]:
    value = dict(row)
    value.pop("auth_relative_path", None)
    value["enabled"] = bool(value.get("enabled"))
    value["is_default"] = bool(value.get("is_default"))
    value["qrcode_url"] = f"/api/accounts/{value['id']}/qrcode" if value.get("qrcode_relative_path") else ""
    return value


def _format_task(row: sqlite3.Row) -> dict[str, Any]:
    value = dict(row)
    for key, fallback in (("tags", []), ("platform_options", {}), ("result", {})):
        try:
            value[key] = json.loads(value.get(key) or json.dumps(fallback))
        except json.JSONDecodeError:
            value[key] = fallback
    return value
