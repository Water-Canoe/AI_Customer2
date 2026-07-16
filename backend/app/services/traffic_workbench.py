from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import random
import re
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlparse

from app import database
from app.schemas import TrafficAutomationPlanConfig, TrafficPlanCreate, TrafficSettingsUpdate
from app.services import browser_queue, profile_manager


# 引流设置默认值只覆盖缺失项，避免覆盖用户在设置页保存的配置。
TRAFFIC_SETTING_KEYS = {
    "traffic_daily_action_limit": "50",
    "traffic_min_watch_seconds": "3",
    "traffic_max_watch_seconds": "8",
    "traffic_author_cooldown_hours": "24",
    "traffic_stop_after_failures": "3",
    "traffic_close_browser_on_failure": "true",
    "traffic_headless": "false",
    "traffic_action_probability": "60",
}

TRAFFIC_IMAGE_DIR = database.get_data_root() / "traffic_images"
TRAFFIC_DOUYIN_PROFILE_DIR = database.get_douyin_cloak_profile_dir()
TRAFFIC_KUAISHOU_PROFILE_DIR = database.get_data_root() / "kuaishou_cloak_profile"
TRAFFIC_LAST_VIDEO_URL_KEY = "traffic_last_douyin_video_url"
KUAISHOU_RECO_URL = "https://www.kuaishou.com/new-reco"
TRAFFIC_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
TRAFFIC_IMAGE_MAX_BYTES = 8 * 1024 * 1024
INSTALL_TIMEOUT_SECONDS = 300
WARMUP_VIDEO_SKIP_COUNT = 3
ACTION_RESPONSE_TIMEOUT_MS = 4_000
VIDEO_CHANGE_TIMEOUT_MS = 1_600
KEYWORD_SOURCE_MODES = {"search_keyword", "collected_keyword"}
ACTION_FILTER_TERMS = {
    "like": ["like", "点赞视频"],
    "collect": ["collect", "收藏视频"],
    "follow": ["follow", "关注作者"],
    "comment_text": ["comment_text", "评论"],
    "comment_image": ["comment_image", "评论"],
}
SOURCE_MODE_LABELS = {
    "random_feed": "随机推荐流",
    "competitor_videos": "拓客竞品视频",
    "collected_keyword": "已采集关键词",
    "search_keyword": "手动搜索关键词",
}
PLATFORM_LOGIN_TARGETS = {
    "dy": {"label": "抖音", "url": "https://www.douyin.com/?recommend=1"},
    "xhs": {"label": "小红书", "url": "https://www.xiaohongshu.com/explore"},
    "ks": {"label": "快手", "url": "https://www.kuaishou.com/"},
}
TRAFFIC_REVIEW_SESSIONS: list[dict[str, Any]] = []
COMMENT_EDITOR_SELECTOR = "#videoSideCard .comment-input-inner-container textarea, #videoSideCard .comment-input-inner-container [contenteditable='true'], #videoSideBar .comment-input-inner-container textarea, #videoSideBar .comment-input-inner-container [contenteditable='true']"
COMMENT_EDITOR_VISIBLE_SELECTOR = ", ".join(f"{selector.strip()}:visible" for selector in COMMENT_EDITOR_SELECTOR.split(","))
COMMENT_CONTAINER_SELECTOR = "#videoSideCard .comment-input-inner-container, #videoSideBar .comment-input-inner-container, .comment-input-inner-container"


# 停机异常同时携带用户提示和技术详情，日志页面按这两个层级展示。
@dataclass
class TrafficStop(Exception):
    message: str
    reason: str
    suggestion: str
    phase: str = "stop"
    details: dict[str, Any] | None = None


ACTIVE_RUN_STATUSES = {"queued", "running"}


def list_plans(include_archived: bool = False) -> list[dict[str, Any]]:
    with database.connect() as conn:
        where = "WHERE automation_managed = 0" if include_archived else "WHERE automation_managed = 0 AND archived = 0"
        rows = conn.execute(f"SELECT * FROM traffic_plans {where} ORDER BY created_at DESC").fetchall()
    return [_format_plan(row) for row in rows]


def get_plan(plan_id: str) -> dict[str, Any] | None:
    with database.connect() as conn:
        row = conn.execute("SELECT * FROM traffic_plans WHERE id = ?", (plan_id,)).fetchone()
    return _format_plan(row) if row else None


def create_plan(payload: TrafficPlanCreate) -> dict[str, Any]:
    plan_id = uuid.uuid4().hex
    plan = _normalize_plan(payload, plan_id)
    with database.connect() as conn:
        conn.execute(
            """
            INSERT INTO traffic_plans(
                id, name, platform, source_mode, source_value,
                action_like, action_collect, action_follow,
                action_comment_text, action_comment_image, round_video_limit, enabled
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                plan_id,
                plan["name"],
                plan["platform"],
                plan["source_mode"],
                plan["source_value"],
                int(plan["action_like"]),
                int(plan["action_collect"]),
                int(plan["action_follow"]),
                int(plan["action_comment_text"]),
                int(plan["action_comment_image"]),
                int(plan["round_video_limit"]),
                int(plan["enabled"]),
            ),
        )
    created = get_plan(plan_id)
    assert created is not None
    return created


def create_automation_run(automation_run_id: str, plan_name: str, config: TrafficAutomationPlanConfig) -> dict[str, Any]:
    """Create the hidden traffic plan/run once for a resumable automation run."""
    plan_id = f"automation:{automation_run_id}"
    run_id = f"automation:{automation_run_id}"
    payload = TrafficPlanCreate(name=plan_name, enabled=True, **config.model_dump())
    plan = _normalize_plan(payload, plan_id)
    _validate_run_plan({**plan, "actions": _plan_actions(plan)})
    with database.connect() as conn:
        existing_plan = conn.execute("SELECT automation_managed FROM traffic_plans WHERE id = ?", (plan_id,)).fetchone()
        if existing_plan and not bool(existing_plan["automation_managed"]):
            raise RuntimeError("自动化引流内部计划 ID 与普通计划冲突")
        if not existing_plan:
            conn.execute(
                """
                INSERT INTO traffic_plans(
                    id, name, platform, source_mode, source_value,
                    action_like, action_collect, action_follow,
                    action_comment_text, action_comment_image, round_video_limit,
                    enabled, automation_managed
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 1)
                """,
                (
                    plan_id,
                    plan["name"],
                    plan["platform"],
                    plan["source_mode"],
                    plan["source_value"],
                    int(plan["action_like"]),
                    int(plan["action_collect"]),
                    int(plan["action_follow"]),
                    int(plan["action_comment_text"]),
                    int(plan["action_comment_image"]),
                    int(plan["round_video_limit"]),
                ),
            )
        if not conn.execute("SELECT 1 FROM traffic_runs WHERE id = ?", (run_id,)).fetchone():
            _insert_run(conn, run_id, plan_id)
            _insert_log(
                conn,
                run_id,
                "info",
                "probe",
                "自动引流批次已创建，等待浏览器资源。",
                "任务已进入自动化队列。",
                "系统会按照自动化计划顺序执行。",
                {"automation_run_id": automation_run_id},
            )
    run = get_run(run_id)
    assert run is not None
    return run


def update_plan(plan_id: str, payload: TrafficPlanCreate) -> dict[str, Any]:
    plan = _normalize_plan(payload, plan_id)
    with database.connect() as conn:
        row = conn.execute("SELECT id FROM traffic_plans WHERE id = ?", (plan_id,)).fetchone()
        if not row:
            raise ValueError("引流计划不存在")
        conn.execute(
            """
            UPDATE traffic_plans
            SET name = ?, platform = ?, source_mode = ?, source_value = ?,
                action_like = ?, action_collect = ?, action_follow = ?,
                action_comment_text = ?, action_comment_image = ?, round_video_limit = ?, enabled = ?,
                updated_at = datetime('now', 'localtime')
            WHERE id = ?
            """,
            (
                plan["name"],
                plan["platform"],
                plan["source_mode"],
                plan["source_value"],
                int(plan["action_like"]),
                int(plan["action_collect"]),
                int(plan["action_follow"]),
                int(plan["action_comment_text"]),
                int(plan["action_comment_image"]),
                int(plan["round_video_limit"]),
                int(plan["enabled"]),
                plan_id,
            ),
        )
    updated = get_plan(plan_id)
    assert updated is not None
    return updated


def delete_plan(plan_id: str) -> dict[str, Any]:
    with database.connect() as conn:
        active = conn.execute(
            """
            SELECT 1 FROM traffic_runs
            WHERE plan_id = ? AND status IN ('queued', 'running')
            LIMIT 1
            """,
            (plan_id,),
        ).fetchone()
        if active:
            raise ValueError("该计划仍有运行中的批次，请先停止批次后再删除")
        # 防重复账本外键会保留空引用，硬删除时必须主动清掉本计划痕迹。
        conn.execute(
            """
            DELETE FROM traffic_dedup_ledger
            WHERE run_id IN (SELECT id FROM traffic_runs WHERE plan_id = ?)
               OR record_id IN (SELECT id FROM traffic_records WHERE plan_id = ?)
            """,
            (plan_id, plan_id),
        )
        conn.execute("DELETE FROM traffic_plans WHERE id = ?", (plan_id,))
    return {"deleted": True}


def archive_plan(plan_id: str) -> dict[str, Any]:
    with database.connect() as conn:
        active = conn.execute(
            """
            SELECT 1 FROM traffic_runs
            WHERE plan_id = ? AND status IN ('queued', 'running')
            LIMIT 1
            """,
            (plan_id,),
        ).fetchone()
        if active:
            raise ValueError("该计划仍有运行中的批次，请先停止批次后再归档")
        return _set_plan_archived(conn, plan_id, True)


def restore_plan(plan_id: str) -> dict[str, Any]:
    with database.connect() as conn:
        return _set_plan_archived(conn, plan_id, False)


def _set_plan_archived(conn: Any, plan_id: str, archived: bool) -> dict[str, Any]:
    cursor = conn.execute(
        "UPDATE traffic_plans SET archived = ?, updated_at = datetime('now', 'localtime') WHERE id = ?",
        (1 if archived else 0, plan_id),
    )
    if cursor.rowcount == 0:
        raise ValueError("引流计划不存在")
    row = conn.execute("SELECT * FROM traffic_plans WHERE id = ?", (plan_id,)).fetchone()
    return _format_plan(row)


def create_run(plan_id: str) -> dict[str, Any]:
    plan = get_plan(plan_id)
    if not plan:
        raise ValueError("引流计划不存在")
    if plan.get("archived"):
        raise ValueError("该计划已归档，请先恢复后再启动")
    _validate_run_plan(plan)
    run_id = uuid.uuid4().hex
    with database.connect() as conn:
        _insert_run(conn, run_id, plan_id)
        _insert_log(
            conn,
            run_id,
            "info",
            "probe",
            "批次已创建，等待打开平台页面。",
            "任务已进入队列",
            "请保持网络正常，不要关闭本地服务。",
            {"plan_id": plan_id},
        )
    run = get_run(run_id)
    assert run is not None
    return run


def _insert_run(conn: Any, run_id: str, plan_id: str) -> None:
    columns = {"id": run_id, "plan_id": plan_id, "status": "queued"}
    table_columns = {str(row["name"]) for row in conn.execute("PRAGMA table_info(traffic_runs)").fetchall()}
    if "campaign_id" in table_columns:
        # 早期原型表保留了 NOT NULL 旧列；写入占位值即可，不参与新版逻辑。
        columns.update({"campaign_id": _legacy_campaign_id(conn), "counts": "{}", "error": ""})
    active_columns = [key for key in columns if key in table_columns]
    placeholders = ", ".join("?" for _ in active_columns)
    conn.execute(
        f"INSERT INTO traffic_runs({', '.join(active_columns)}) VALUES({placeholders})",
        [columns[key] for key in active_columns],
    )


def _legacy_campaign_id(conn: Any) -> int:
    table = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'traffic_campaigns'").fetchone()
    if not table:
        return 0
    row = conn.execute("SELECT id FROM traffic_campaigns WHERE name = ? LIMIT 1", ("__traffic_plan_bridge__",)).fetchone()
    if row:
        return int(row["id"])
    return int(conn.execute(
        """
        INSERT INTO traffic_campaigns(name, mode, source_type, keyword, action_like, action_follow, action_comment)
        VALUES('__traffic_plan_bridge__', 'random_feed', 'compat', '', 0, 0, 0)
        """
    ).lastrowid)


def list_runs(include_archived: bool = False) -> list[dict[str, Any]]:
    with database.connect() as conn:
        where = "" if include_archived else "WHERE r.archived = 0"
        rows = conn.execute(
            f"""
            SELECT r.*, p.name AS plan_name, p.platform, p.source_mode
            FROM traffic_runs r
            JOIN traffic_plans p ON p.id = r.plan_id
            {where}
            ORDER BY r.created_at DESC
            """
        ).fetchall()
    return [_format_run(row) for row in rows]


def get_run(run_id: str) -> dict[str, Any] | None:
    with database.connect() as conn:
        row = conn.execute(
            """
            SELECT r.*, p.name AS plan_name, p.platform, p.source_mode
            FROM traffic_runs r
            JOIN traffic_plans p ON p.id = r.plan_id
            WHERE r.id = ?
            """,
            (run_id,),
        ).fetchone()
        if not row:
            return None
        run = _format_run(row)
        run["logs"] = database.rows_to_dicts(
            conn.execute(
                "SELECT * FROM traffic_action_logs WHERE run_id = ? ORDER BY id",
                (run_id,),
            ).fetchall()
        )
        run["items"] = database.rows_to_dicts(
            conn.execute(
                "SELECT * FROM traffic_run_items WHERE run_id = ? ORDER BY id DESC",
                (run_id,),
            ).fetchall()
        )
        run["records"] = [
            _format_record(row)
            for row in conn.execute(
                """
                SELECT tr.*, p.name AS plan_name, r.status AS run_status
                FROM traffic_records tr
                LEFT JOIN traffic_plans p ON p.id = tr.plan_id
                LEFT JOIN traffic_runs r ON r.id = tr.run_id
                WHERE tr.run_id = ?
                ORDER BY tr.created_at DESC, tr.id DESC
                """,
                (run_id,),
            ).fetchall()
        ]
    return run


def stop_run(run_id: str) -> dict[str, Any]:
    with database.connect() as conn:
        row = conn.execute("SELECT status FROM traffic_runs WHERE id = ?", (run_id,)).fetchone()
        if not row:
            raise ValueError("引流批次不存在")
        conn.execute(
            """
            UPDATE traffic_runs
            SET stop_requested = 1,
                status = CASE WHEN status IN ('running', 'queued') THEN 'stopped' ELSE status END,
                stop_reason = CASE WHEN status IN ('running', 'queued') THEN '用户手动停止任务' ELSE stop_reason END,
                stop_suggestion = CASE WHEN status IN ('running', 'queued') THEN '可以在操作记录查看已经处理的视频；如需继续，请重新启动批次。' ELSE stop_suggestion END,
                finished_at = CASE WHEN status IN ('running', 'queued') THEN datetime('now', 'localtime') ELSE finished_at END,
                updated_at = datetime('now', 'localtime')
            WHERE id = ?
            """,
            (run_id,),
        )
        if row["status"] in {"running", "queued"}:
            _insert_log(
                conn,
                run_id,
                "warning",
                "stop",
                "任务已按你的要求停止。",
                "用户手动停止任务",
                "可以在操作记录查看已经处理的视频；如需继续，请重新启动批次。",
                {},
            )
    return get_run(run_id) or {}


def archive_run(run_id: str) -> dict[str, Any]:
    with database.connect() as conn:
        return _set_run_archived(conn, run_id, True)


def restore_run(run_id: str) -> dict[str, Any]:
    with database.connect() as conn:
        return _set_run_archived(conn, run_id, False)


def _set_run_archived(conn: Any, run_id: str, archived: bool) -> dict[str, Any]:
    row = conn.execute("SELECT status FROM traffic_runs WHERE id = ?", (run_id,)).fetchone()
    if not row:
        raise ValueError("引流批次不存在")
    if archived and row["status"] in ACTIVE_RUN_STATUSES:
        raise ValueError("批次仍在运行或排队，请先停止后再归档")
    conn.execute(
        "UPDATE traffic_runs SET archived = ?, updated_at = datetime('now', 'localtime') WHERE id = ?",
        (1 if archived else 0, run_id),
    )
    updated = conn.execute(
        """
        SELECT r.*, p.name AS plan_name, p.platform, p.source_mode
        FROM traffic_runs r
        JOIN traffic_plans p ON p.id = r.plan_id
        WHERE r.id = ?
        """,
        (run_id,),
    ).fetchone()
    return _format_run(updated)


def delete_run(run_id: str) -> dict[str, Any]:
    with database.connect() as conn:
        row = conn.execute("SELECT status FROM traffic_runs WHERE id = ?", (run_id,)).fetchone()
        if not row:
            raise ValueError("引流批次不存在")
        if row["status"] in ACTIVE_RUN_STATUSES:
            raise ValueError("批次仍在运行或排队，请先停止后再删除")
        # 单个批次硬删除时同步清理防重复账本，避免记录删了但动作仍被判重。
        conn.execute(
            """
            DELETE FROM traffic_dedup_ledger
            WHERE run_id = ?
               OR record_id IN (SELECT id FROM traffic_records WHERE run_id = ?)
            """,
            (run_id, run_id),
        )
        conn.execute("DELETE FROM traffic_runs WHERE id = ?", (run_id,))
    return {"deleted": True}


def list_logs(run_id: str) -> list[dict[str, Any]]:
    with database.connect() as conn:
        rows = conn.execute(
            "SELECT * FROM traffic_action_logs WHERE run_id = ? ORDER BY id",
            (run_id,),
        ).fetchall()
    return database.rows_to_dicts(rows)


def list_records(
    *,
    query: str = "",
    platform: str = "",
    status: str = "",
    action: str = "",
    page: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    page = max(1, page)
    page_size = min(100, max(1, page_size))
    clauses: list[str] = []
    params: list[Any] = []
    if query:
        clauses.append("(tr.video_desc LIKE ? OR tr.author_name LIKE ? OR tr.comment_text LIKE ?)")
        like = f"%{query}%"
        params.extend([like, like, like])
    if platform:
        clauses.append("tr.platform = ?")
        params.append(platform)
    if status:
        clauses.append("tr.status = ?")
        params.append(status)
    if action:
        terms = ACTION_FILTER_TERMS.get(action, [action])
        clauses.append("(" + " OR ".join("tr.actions LIKE ?" for _ in terms) + ")")
        params.extend(f"%{term}%" for term in terms)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with database.connect() as conn:
        total = conn.execute(f"SELECT COUNT(*) AS c FROM traffic_records tr {where}", params).fetchone()["c"]
        rows = conn.execute(
            f"""
            SELECT tr.*, p.name AS plan_name, r.status AS run_status
            FROM traffic_records tr
            LEFT JOIN traffic_plans p ON p.id = tr.plan_id
            LEFT JOIN traffic_runs r ON r.id = tr.run_id
            {where}
            ORDER BY tr.created_at DESC, tr.id DESC
            LIMIT ? OFFSET ?
            """,
            [*params, page_size, (page - 1) * page_size],
        ).fetchall()
    return {
        "rows": [_format_record(row) for row in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": max(1, (total + page_size - 1) // page_size),
    }


def clear_records() -> dict[str, Any]:
    with database.connect() as conn:
        active = conn.execute(
            "SELECT 1 FROM traffic_runs WHERE status IN ('queued', 'running') LIMIT 1"
        ).fetchone()
        if active:
            raise ValueError("仍有运行中的引流批次，请先停止批次后再清除引流数据")
        counts = {
            "plans": conn.execute("SELECT COUNT(*) AS c FROM traffic_plans").fetchone()["c"],
            "records": conn.execute("SELECT COUNT(*) AS c FROM traffic_records").fetchone()["c"],
            "runs": conn.execute("SELECT COUNT(*) AS c FROM traffic_runs").fetchone()["c"],
            "dedup": conn.execute("SELECT COUNT(*) AS c FROM traffic_dedup_ledger").fetchone()["c"],
        }
        legacy_campaigns = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'traffic_campaigns'"
        ).fetchone()
        if legacy_campaigns:
            counts["campaigns"] = conn.execute("SELECT COUNT(*) AS c FROM traffic_campaigns").fetchone()["c"]
        conn.execute("DELETE FROM traffic_dedup_ledger")
        conn.execute("DELETE FROM traffic_records")
        conn.execute("DELETE FROM traffic_action_logs")
        conn.execute("DELETE FROM traffic_run_items")
        conn.execute("DELETE FROM traffic_runs")
        conn.execute("DELETE FROM traffic_plans")
        if legacy_campaigns:
            conn.execute("DELETE FROM traffic_campaigns")
        conn.execute("UPDATE traffic_material_texts SET used_count = 0")
        conn.execute("UPDATE traffic_material_images SET used_count = 0")
        conn.execute("DELETE FROM settings WHERE key = ?", (TRAFFIC_LAST_VIDEO_URL_KEY,))
    return {"cleared": True, **counts}


def get_settings() -> dict[str, Any]:
    with database.connect() as conn:
        values = {
            key: database.get_setting(conn, key, default)
            for key, default in TRAFFIC_SETTING_KEYS.items()
        }
        texts = database.rows_to_dicts(
            conn.execute("SELECT * FROM traffic_material_texts ORDER BY id DESC").fetchall()
        )
        images = [
            _format_material_image(row)
            for row in conn.execute("SELECT * FROM traffic_material_images ORDER BY id DESC").fetchall()
        ]
    return {"values": values, "texts": texts, "images": images}


def update_settings(payload: TrafficSettingsUpdate) -> dict[str, Any]:
    with database.connect() as conn:
        for key, default in TRAFFIC_SETTING_KEYS.items():
            if key in payload.values:
                database.set_setting(conn, key, payload.values.get(key, default))
        conn.execute("DELETE FROM traffic_material_texts")
        conn.execute("DELETE FROM traffic_material_images")
        for text, enabled in _clean_material_rows(payload.texts, "text"):
            conn.execute("INSERT INTO traffic_material_texts(text, enabled) VALUES(?, ?)", (text, int(enabled)))
        for image, enabled in _clean_material_rows(payload.images, "path"):
            conn.execute("INSERT INTO traffic_material_images(path, enabled) VALUES(?, ?)", (image, int(enabled)))
    return get_settings()


def save_material_image(filename: str, content: bytes) -> dict[str, Any]:
    suffix = Path(filename).suffix.lower()
    if suffix not in TRAFFIC_IMAGE_EXTENSIONS:
        raise ValueError("只支持 jpg、png、gif、webp 图片")
    if not content:
        raise ValueError("图片文件为空")
    if len(content) > TRAFFIC_IMAGE_MAX_BYTES:
        raise ValueError("图片不能超过 8MB")

    TRAFFIC_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    path = TRAFFIC_IMAGE_DIR / f"{uuid.uuid4().hex}{suffix}"
    # ponytail: 本地工作台只存 runtime 文件；需要多设备共享时再换对象存储。
    path.write_bytes(content)
    return {"path": str(path), "preview_url": _image_preview_url(path)}


def material_image_path(name: str) -> Path:
    path = TRAFFIC_IMAGE_DIR / Path(name).name
    if path.suffix.lower() not in TRAFFIC_IMAGE_EXTENSIONS or not path.exists():
        raise ValueError("图片不存在")
    return path


def environment_check() -> dict[str, Any]:
    playwright_ok = importlib.util.find_spec("playwright") is not None
    cloakbrowser_ok = importlib.util.find_spec("cloakbrowser") is not None
    cloakbrowser_binary = _check_cloakbrowser_binary() if cloakbrowser_ok else _env_item(False, "需要先安装 CloakBrowser Python 包")
    TRAFFIC_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    image_dir_ok = TRAFFIC_IMAGE_DIR.exists() and TRAFFIC_IMAGE_DIR.is_dir()
    items = {
        "python": _env_item(True, str(sys.executable)),
        "playwright": _env_item(playwright_ok, "已安装" if playwright_ok else "缺少 Python Playwright 依赖"),
        "cloakbrowser": _env_item(cloakbrowser_ok, "已安装" if cloakbrowser_ok else "缺少 Python CloakBrowser 依赖"),
        "cloakbrowser_binary": cloakbrowser_binary,
        "image_dir": _env_item(image_dir_ok, str(TRAFFIC_IMAGE_DIR)),
    }
    ok = all(item["ok"] for item in items.values())
    return {
        "ok": ok,
        "items": items,
        "summary": "引流执行环境正常" if ok else "引流执行环境缺少依赖",
        "suggestion": "可以启动引流批次" if ok else "点击“检查并自动安装”安装缺失依赖",
    }


def install_environment() -> dict[str, Any]:
    if getattr(sys, "frozen", False):
        check = environment_check()
        return {
            "ok": check["ok"],
            "steps": [{"command": "内置运行环境", "ok": check["ok"], "output": "打包版本已内置依赖，无需在线安装"}],
            "check": check,
        }
    steps = [
        _run_install_step([sys.executable, "-m", "pip", "install", "-r", str(database.BACKEND_ROOT / "requirements.txt")]),
        _run_install_step([sys.executable, "-m", "cloakbrowser", "install"]),
    ]
    return {"ok": all(step["ok"] for step in steps), "steps": steps, "check": environment_check()}


def open_platform_login_window(platform: str) -> dict[str, Any]:
    target = PLATFORM_LOGIN_TARGETS.get(platform)
    if not target:
        raise ValueError("不支持的平台登录配置")
    if importlib.util.find_spec("playwright") is None:
        raise ValueError("缺少 Python Playwright 依赖，请先在引流设置执行环境检查并自动安装")
    if importlib.util.find_spec("cloakbrowser") is None:
        raise ValueError("缺少 Python CloakBrowser 依赖，请先在引流设置执行环境检查并自动安装")
    active = profile_manager.status()
    if active["interactive_active"]:
        active_target = PLATFORM_LOGIN_TARGETS.get(str(active["interactive_platform"])) or target
        return {
            "ok": True,
            "message": f"{active_target['label']}登录窗口已经打开。三个平台共用登录会话，请关闭当前窗口后再打开其它平台。",
        }

    runtime_dir = database.get_data_root()
    runtime_dir.mkdir(parents=True, exist_ok=True)
    log_path = runtime_dir / f"platform_{platform}_login.log"
    # 打包后 sys.executable 是主程序本身，只能通过内部参数启动登录子进程。
    command = (
        [sys.executable, "--internal-platform-login", platform]
        if getattr(sys, "frozen", False)
        else [
            sys.executable,
            "-c",
            f"from app.services.traffic_workbench import _hold_platform_login_window; _hold_platform_login_window('{platform}')",
        ]
    )
    result = profile_manager.launch_interactive(
        platform,
        command,
        cwd=database.BACKEND_ROOT,
        log_path=log_path,
    )
    if not result["started"]:
        return {"ok": True, "message": f"{target['label']}登录窗口已经打开。"}
    return {
        "ok": True,
        "message": f"{target['label']}登录窗口已打开。登录完成后可手动关闭窗口。",
    }


def open_douyin_login_window() -> dict[str, Any]:
    return open_platform_login_window("dy")


def source_keywords() -> list[dict[str, Any]]:
    # 关键词直接来自拓客工作台已入库内容，计划页只负责点击填入。
    with database.connect() as conn:
        rows = conn.execute(
            """
            SELECT source_keyword AS keyword, COUNT(*) AS content_count
            FROM contents
            WHERE platform = 'dy' AND source_keyword <> ''
            GROUP BY source_keyword
            ORDER BY content_count DESC, keyword
            """
        ).fetchall()
    return database.rows_to_dicts(rows)


def source_competitor_videos() -> list[dict[str, Any]]:
    with database.connect() as conn:
        rows = conn.execute(
            """
            SELECT
                c.id, c.content_id, c.title, c.description, c.content_url,
                c.like_count, c.comment_count, u.nickname AS author_name
            FROM contents c
            LEFT JOIN user_accounts u ON u.id = c.author_account_id
            WHERE c.platform = 'dy'
              AND u.competitor_status = '竞品'
              AND (c.content_url LIKE '%douyin.com/video/%' OR c.content_url LIKE '%douyin.com/note/%' OR c.content_id <> '')
            ORDER BY c.updated_at DESC
            """
        ).fetchall()
    return database.rows_to_dicts(rows)


def run_traffic_run(run_id: str) -> None:
    # 后台任务只写批次状态和日志，前端通过执行监控轮询读取。
    run = get_run(run_id)
    if not run:
        return
    if str(run.get("status")) not in ACTIVE_RUN_STATUSES:
        return
    plan = get_plan(str(run["plan_id"]))
    if not plan:
        return
    _mark_run_running(run_id)
    try:
        with browser_queue.browser_slot(
            f"traffic:{run_id}",
            on_wait=lambda message: _append_log(
                run_id,
                "info",
                "browser_queue",
                message,
                "浏览器环境一次只能由一个任务使用。",
                "系统会自动排队执行，不需要重复点击启动。",
                {},
            ),
            should_stop=lambda: _traffic_run_stop_requested(run_id),
        ):
            completion = _run_with_playwright(run_id, plan) or {}
        _finish_run(
            run_id,
            "completed",
            completion.get("message", "已达到本轮上限，任务已自动完成。"),
            completion.get("reason", "本轮执行已完成"),
            completion.get("suggestion", "可以在操作记录查看每个视频的处理结果。"),
        )
    except TrafficStop as exc:
        _finish_run(
            run_id,
            "failed" if exc.phase != "stop" else "stopped",
            exc.message,
            exc.reason,
            exc.suggestion,
            exc.phase,
            exc.details or {},
        )
    except browser_queue.BrowserQueueCancelled:
        _finish_run(
            run_id,
            "stopped",
            "任务已按你的要求停止。",
            "任务等待浏览器资源时收到停止请求。",
            "可以在执行监控查看已经写入的日志。",
            "stop",
            {},
        )
    except Exception as exc:
        _finish_run(
            run_id,
            "failed",
            "引流任务异常停止。",
            str(exc),
            "请查看技术详情；如果重复出现，请暂停使用并反馈日志。",
            "stop",
            {"error": repr(exc)},
        )


def _run_with_playwright(run_id: str, plan: dict[str, Any]) -> dict[str, str]:
    # 真实浏览器自动化集中在这里，所有异常都转为用户可读停机原因。
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    except ImportError as exc:
        raise TrafficStop(
            "缺少 Playwright 依赖，任务已停止。",
            "本地后端没有安装浏览器自动化依赖。",
            "请先在后端环境安装 requirements.txt，并执行 Playwright 浏览器安装。",
            "probe",
            {"error": str(exc)},
        ) from exc

    settings = get_settings()["values"]
    limit = int(plan.get("round_video_limit") or 5)
    daily_action_limit = _int_setting(settings, "traffic_daily_action_limit", 50, 0, 1000)
    min_watch = _int_setting(settings, "traffic_min_watch_seconds", 3, 0, 120)
    max_watch = _int_setting(settings, "traffic_max_watch_seconds", 8, min_watch, 300)
    stop_after_failures = _int_setting(settings, "traffic_stop_after_failures", 3, 1, 10)
    author_cooldown_hours = _int_setting(settings, "traffic_author_cooldown_hours", 24, 0, 720)
    close_browser_on_failure = _bool_setting(settings, "traffic_close_browser_on_failure", True)
    headless = _bool_setting(settings, "traffic_headless", False)
    action_probability = _int_setting(settings, "traffic_action_probability", 60, 0, 100)
    if plan.get("platform") == "ks":
        return _run_kuaishou_with_playwright(run_id, plan, PlaywrightTimeoutError)
    action_budget = max(0, daily_action_limit - _daily_action_count("dy"))
    no_progress_count = 0
    project_author_keys: set[str] = set()

    context = None
    close_context = True
    try:
        context = _launch_context(TRAFFIC_DOUYIN_PROFILE_DIR, headless)
        page = context.pages[0] if context.pages else context.new_page()
        video_cache = _setup_video_data_cache(page)
        try:
            target_url = _target_url(plan)
            _append_log(run_id, "info", "login", "正在打开抖音页面。", "准备执行引流批次", "如果弹出登录，请先到引流设置完成扫码登录。", {"url": target_url})
            if not _goto_with_timeout_tolerance(page, target_url, 60_000):
                _append_log(run_id, "warning", "probe", "抖音页面加载超时，继续检查当前页面。", "页面可能已经可用，但浏览器没有收到加载完成信号", "系统会继续识别当前页面；如果确实没有内容，会再给出明确原因。", {"url": target_url, "current_url": page.url})
            _wait_for_douyin_ready_signal(page, run_id, 4000)
            _ensure_page_ready(page)
            _navigate_to_executable_video(page, run_id, plan["source_mode"], video_cache, author_cooldown_hours, project_author_keys, plan.get("source_value", ""))
            if plan["actions"] and action_budget <= 0:
                _append_log(run_id, "success", "stop", "已达到每日动作上限，任务已自动完成。", "今日真实互动动作数量已经达到设置值", "可以明天继续，或到引流设置调整每日动作上限。", {"daily_action_limit": daily_action_limit})
                return {
                    "message": "已达到每日动作上限，任务已自动完成。",
                    "reason": "今日真实互动动作数量已经达到设置值",
                    "suggestion": "可以明天继续，或到引流设置调整每日动作上限。",
                }
            handled_count = 0
            seen_count = 0
            warmup_skip_count = WARMUP_VIDEO_SKIP_COUNT if plan["source_mode"] == "random_feed" else 0
            max_seen_count = limit * 10 + warmup_skip_count
            while handled_count < limit:
                _raise_if_stop_requested(run_id)
                video = _read_active_video(page, video_cache)
                if not video["video_id"]:
                    no_progress_count += 1
                    if no_progress_count >= stop_after_failures:
                        raise TrafficStop(
                            f"连续 {no_progress_count} 次没有找到可执行的视频，任务已停止。",
                            "当前页面没有可识别的视频 ID。",
                            "请确认抖音已经登录，且首页或搜索页能正常打开视频。",
                            "probe",
                            {"url": page.url, "mode": _detect_douyin_page_mode(page)},
                        )
                    _append_log(run_id, "warning", "probe", "当前页面没有识别到视频，正在重新进入来源。", "页面可能还停留在首页、搜索结果或加载中的视频页", "系统会自动重新进入视频；连续失败才会停机。", {"url": page.url})
                    _navigate_to_executable_video(page, run_id, plan["source_mode"], video_cache, author_cooldown_hours, project_author_keys, plan.get("source_value", ""))
                    continue
                no_progress_count = 0
                seen_count += 1
                if seen_count <= warmup_skip_count:
                    if not _next_video(page, run_id, plan, video["video_id"], video_cache, author_cooldown_hours, project_author_keys, silent=True):
                        no_progress_count += 1
                        if no_progress_count >= stop_after_failures:
                            raise TrafficStop(
                                f"连续 {no_progress_count} 次没有切换到新视频，任务已停止。",
                                "预热跳过视频时无法切换到下一条。",
                                "请到引流设置重新打开抖音并确认推荐流可以正常切换。",
                                "advance",
                                {"video_id": video["video_id"], "url": page.url},
                            )
                    continue
                skip_reason = _video_skip_reason(page, video)
                if skip_reason:
                    _append_log(run_id, "warning", "browse", f"当前视频已跳过：{skip_reason}。", skip_reason, "无需手动处理，系统会继续切换下一条视频。", {"video_id": video["video_id"], "aweme_type": video.get("aweme_type"), "url": page.url})
                    _record_video(run_id, plan, video, ["跳过"], "", "", "skipped", skip_reason)
                    if not _next_video(page, run_id, plan, video["video_id"], video_cache, author_cooldown_hours, project_author_keys):
                        no_progress_count += 1
                        if no_progress_count >= stop_after_failures:
                            raise TrafficStop(
                                f"连续 {no_progress_count} 次没有切换到新视频，任务已停止。",
                                "翻页后视频 ID 没有变化。",
                                "请到引流设置重新打开抖音并确认推荐流可以正常切换。",
                                "advance",
                                {"video_id": video["video_id"], "url": page.url},
                            )
                    continue
                watch_seconds = random.randint(min_watch, max_watch)
                if not plan["actions"]:
                    _append_log(run_id, "info", "browse", f"正在浏览视频：{video['video_desc'][:36] or video['video_id']}。", "按设置随机停留", f"本次停留约 {watch_seconds} 秒。", {"video_id": video["video_id"]})
                _wait_or_stop(page, run_id, watch_seconds * 1000)
                done_actions, comment_text, image_path, skipped_by_error = _execute_actions_with_retry(run_id, plan, page, video, action_budget, action_probability)
                action_budget = max(0, action_budget - _real_action_count(done_actions))
                if skipped_by_error:
                    _record_video(run_id, plan, video, ["跳过"], comment_text, image_path, "skipped", "连续动作失败，已跳过当前视频")
                    if not _next_video(page, run_id, plan, video["video_id"], video_cache, author_cooldown_hours, project_author_keys):
                        raise TrafficStop(
                            "连续 3 次没有切换到新视频，任务已停止。可能是页面没有进入推荐流。",
                            "跳过异常视频后仍无法切换到新视频。",
                            "请到引流设置重新打开抖音并确认推荐流可以正常切换。",
                            "advance",
                            {"video_id": video["video_id"], "url": page.url},
                        )
                    continue
                if plan["actions"] and not done_actions:
                    if seen_count >= max_seen_count:
                        raise TrafficStop(
                            "连续跳过的视频过多，任务已停止。",
                            "本轮没有凑够可执行互动的视频。",
                            "请降低防重复限制、提高操作执行概率，或稍后再试。",
                            "advance",
                            {"seen_count": seen_count, "target_count": limit},
                        )
                    if not _next_video(page, run_id, plan, video["video_id"], video_cache, author_cooldown_hours, project_author_keys, silent=True):
                        raise TrafficStop(
                            "连续 3 次没有切换到新视频，任务已停止。可能是页面没有进入推荐流。",
                            "无操作视频后无法切换到新视频。",
                            "请到引流设置重新打开抖音并确认推荐流可以正常切换。",
                            "advance",
                            {"video_id": video["video_id"], "url": page.url},
                        )
                    continue
                record_actions = done_actions or ["仅浏览"]
                _record_video(run_id, plan, video, record_actions, comment_text, image_path, "browsed" if not plan["actions"] or record_actions == ["仅浏览"] else "done")
                handled_count += 1
                if plan["actions"] and action_budget <= 0:
                    _append_log(run_id, "success", "stop", "已达到每日动作上限，任务已自动完成。", "今日真实互动动作数量已经达到设置值", "可以明天继续，或到引流设置调整每日动作上限。", {"daily_action_limit": daily_action_limit})
                    return {
                        "message": "已达到每日动作上限，任务已自动完成。",
                        "reason": "今日真实互动动作数量已经达到设置值",
                        "suggestion": "可以明天继续，或到引流设置调整每日动作上限。",
                    }
                if handled_count < limit and not _next_video(page, run_id, plan, video["video_id"], video_cache, author_cooldown_hours, project_author_keys):
                    raise TrafficStop(
                        "连续 3 次没有切换到新视频，任务已停止。可能是页面没有进入推荐流。",
                        "翻页后视频 ID 没有变化。",
                        "请到引流设置重新打开抖音并确认推荐流可以正常切换。",
                        "advance",
                        {"video_id": video["video_id"], "url": page.url},
                    )
        except PlaywrightTimeoutError as exc:
            if not close_browser_on_failure:
                close_context = False
                _append_log(run_id, "warning", "stop", "任务已停止，浏览器已保留用于复盘。", "引流设置关闭了“失败后关闭浏览器”", "请复盘完成后手动关闭浏览器，再启动新的引流批次。", {"url": getattr(page, "url", "")})
            raise TrafficStop(
                "抖音页面加载超时，任务已停止。",
                "浏览器等待页面响应超时。",
                "请检查网络和抖音页面是否能手动打开。",
                "probe",
                {"error": str(exc)},
            ) from exc
        except Exception as exc:
            if _is_browser_closed_error(exc):
                raise TrafficStop(
                    "浏览器窗口已关闭，任务已停止。",
                    "执行中的抖音浏览器被手动关闭。",
                    "如需继续，请重新启动批次；如果只是想复盘，请在任务停止后再关闭窗口。",
                    "stop",
                    {"error": repr(exc)},
                ) from exc
            if not close_browser_on_failure:
                close_context = False
                _append_log(run_id, "warning", "stop", "任务已停止，浏览器已保留用于复盘。", "引流设置关闭了“失败后关闭浏览器”", "请复盘完成后手动关闭浏览器，再启动新的引流批次。", {"url": getattr(page, "url", "")})
            raise
    finally:
        if close_context:
            if context is not None:
                try:
                    context.close()
                except Exception as exc:
                    if not _is_browser_closed_error(exc):
                        raise
        elif context is not None:
            TRAFFIC_REVIEW_SESSIONS.append({"context": context})
    return {}


def _run_kuaishou_with_playwright(run_id: str, plan: dict[str, Any], timeout_error: Any) -> dict[str, str]:
    settings = get_settings()["values"]
    limit = int(plan.get("round_video_limit") or 5)
    daily_action_limit = _int_setting(settings, "traffic_daily_action_limit", 50, 0, 1000)
    min_watch = _int_setting(settings, "traffic_min_watch_seconds", 3, 0, 120)
    max_watch = _int_setting(settings, "traffic_max_watch_seconds", 8, min_watch, 300)
    stop_after_failures = _int_setting(settings, "traffic_stop_after_failures", 3, 1, 10)
    close_browser_on_failure = _bool_setting(settings, "traffic_close_browser_on_failure", True)
    headless = _bool_setting(settings, "traffic_headless", False)
    action_probability = _int_setting(settings, "traffic_action_probability", 60, 0, 100)
    action_budget = max(0, daily_action_limit - _daily_action_count("ks"))
    context = None
    close_context = True
    try:
        context = _launch_context(TRAFFIC_KUAISHOU_PROFILE_DIR, headless)
        page = context.pages[0] if context.pages else context.new_page()
        video_cache = _setup_kuaishou_data_cache(page)
        target_url = _target_url(plan)
        try:
            _append_log(run_id, "info", "login", "正在打开快手推荐流。", "准备执行快手引流批次", "如果弹出登录，请先到引流设置打开快手登录窗口完成登录。", {"url": target_url})
            if not _goto_with_timeout_tolerance(page, target_url, 60_000):
                _append_log(run_id, "warning", "probe", "快手页面加载超时，继续检查当前页面。", "页面可能已经可用，但浏览器没有收到加载完成信号", "系统会继续识别当前页面；如果确实没有内容，会再给出明确原因。", {"url": target_url, "current_url": page.url})
            _wait_or_stop(page, run_id, 5000)
            _ensure_kuaishou_page_ready(page)
            if plan["actions"] and action_budget <= 0:
                _append_log(run_id, "success", "stop", "已达到每日动作上限，任务已自动完成。", "今日真实互动动作数量已经达到设置值", "可以明天继续，或到引流设置调整每日动作上限。", {"daily_action_limit": daily_action_limit})
                return {"message": "已达到每日动作上限，任务已自动完成。", "reason": "今日真实互动动作数量已经达到设置值", "suggestion": "可以明天继续，或到引流设置调整每日动作上限。"}

            handled_count = 0
            seen_count = 0
            no_progress_count = 0
            max_seen_count = limit * 10 + WARMUP_VIDEO_SKIP_COUNT
            while handled_count < limit:
                _raise_if_stop_requested(run_id)
                video = _read_kuaishou_active_video(page, video_cache)
                if not video["video_id"]:
                    no_progress_count += 1
                    if no_progress_count >= stop_after_failures:
                        raise TrafficStop(
                            f"连续 {no_progress_count} 次没有找到可执行的快手视频，任务已停止。",
                            "当前快手页面没有可识别的视频 ID。",
                            "请确认快手已经登录，并且推荐流能正常播放视频。",
                            "probe",
                            {"url": page.url},
                        )
                    _advance_kuaishou_video(page, "", video_cache)
                    continue
                no_progress_count = 0
                seen_count += 1
                if seen_count <= WARMUP_VIDEO_SKIP_COUNT:
                    _advance_kuaishou_video(page, video["video_id"], video_cache)
                    continue
                skip_reason = _kuaishou_video_skip_reason(video)
                if skip_reason:
                    _record_video(run_id, plan, video, ["跳过"], "", "", "skipped", skip_reason)
                    _advance_kuaishou_video(page, video["video_id"], video_cache)
                    continue
                watch_seconds = random.randint(min_watch, max_watch)
                if not plan["actions"]:
                    _append_log(run_id, "info", "browse", f"正在浏览快手视频：{video['video_desc'][:36] or video['video_id']}。", "按设置随机停留", f"本次停留约 {watch_seconds} 秒。", {"video_id": video["video_id"]})
                _wait_or_stop(page, run_id, watch_seconds * 1000)
                done_actions, comment_text, image_path, skipped_by_error = _execute_kuaishou_actions_with_retry(run_id, plan, page, video, action_budget, action_probability)
                action_budget = max(0, action_budget - _real_action_count(done_actions))
                if skipped_by_error:
                    _record_video(run_id, plan, video, ["跳过"], comment_text, image_path, "skipped", "连续动作失败，已跳过当前视频")
                    _advance_kuaishou_video(page, video["video_id"], video_cache)
                    continue
                if plan["actions"] and not done_actions:
                    if seen_count >= max_seen_count:
                        raise TrafficStop(
                            "连续跳过的视频过多，任务已停止。",
                            "本轮没有凑够可执行互动的快手视频。",
                            "请降低防重复限制、提高操作执行概率，或稍后再试。",
                            "advance",
                            {"seen_count": seen_count, "target_count": limit},
                        )
                    _advance_kuaishou_video(page, video["video_id"], video_cache)
                    continue
                record_actions = done_actions or ["仅浏览"]
                _record_video(run_id, plan, video, record_actions, comment_text, image_path, "browsed" if not plan["actions"] or record_actions == ["仅浏览"] else "done")
                handled_count += 1
                if plan["actions"] and action_budget <= 0:
                    _append_log(run_id, "success", "stop", "已达到每日动作上限，任务已自动完成。", "今日真实互动动作数量已经达到设置值", "可以明天继续，或到引流设置调整每日动作上限。", {"daily_action_limit": daily_action_limit})
                    return {"message": "已达到每日动作上限，任务已自动完成。", "reason": "今日真实互动动作数量已经达到设置值", "suggestion": "可以明天继续，或到引流设置调整每日动作上限。"}
                if handled_count < limit and not _advance_kuaishou_video(page, video["video_id"], video_cache):
                    raise TrafficStop(
                        "连续 3 次没有切换到新的快手视频，任务已停止。",
                        "翻页后视频 ID 没有变化。",
                        "请到引流设置重新打开快手并确认推荐流可以正常切换。",
                        "advance",
                        {"video_id": video["video_id"], "url": page.url},
                    )
        except timeout_error as exc:
            if not close_browser_on_failure:
                close_context = False
                _append_log(run_id, "warning", "stop", "任务已停止，浏览器已保留用于复盘。", "引流设置关闭了“失败后关闭浏览器”", "请复盘完成后手动关闭浏览器，再启动新的引流批次。", {"url": getattr(page, "url", "")})
            raise TrafficStop("快手页面加载超时，任务已停止。", "浏览器等待页面响应超时。", "请检查网络和快手页面是否能手动打开。", "probe", {"error": str(exc)}) from exc
        except Exception as exc:
            if _is_browser_closed_error(exc):
                raise TrafficStop("浏览器窗口已关闭，任务已停止。", "执行中的快手浏览器被手动关闭。", "如需继续，请重新启动批次；如果只是想复盘，请在任务停止后再关闭窗口。", "stop", {"error": repr(exc)}) from exc
            if not close_browser_on_failure:
                close_context = False
                _append_log(run_id, "warning", "stop", "任务已停止，浏览器已保留用于复盘。", "引流设置关闭了“失败后关闭浏览器”", "请复盘完成后手动关闭浏览器，再启动新的引流批次。", {"url": getattr(page, "url", "")})
            raise
    finally:
        if close_context:
            if context is not None:
                try:
                    context.close()
                except Exception as exc:
                    if not _is_browser_closed_error(exc):
                        raise
        elif context is not None:
            TRAFFIC_REVIEW_SESSIONS.append({"context": context})
    return {}


def _bool_setting(settings: dict[str, Any], key: str, default: bool) -> bool:
    value = str(settings.get(key, "true" if default else "false")).strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return default


def _hold_douyin_login_window() -> None:
    _hold_platform_login_window("dy")


def _hold_platform_login_window(platform: str) -> None:
    target = PLATFORM_LOGIN_TARGETS.get(platform)
    if not target:
        raise ValueError("不支持的平台登录配置")
    # 登录和安全验证必须可见，执行批次才允许无头。
    context = _launch_context(_traffic_profile_dir(platform), False)
    page = context.pages[0] if context.pages else context.new_page()
    page.goto(str(target["url"]), wait_until="domcontentloaded", timeout=60_000)
    while True:
        # 登录窗口只负责保活，不检测登录状态，避免扫码后自动化读页触发窗口关闭。
        page.wait_for_timeout(1000)


def _traffic_profile_dir(platform: str) -> Path:
    return TRAFFIC_KUAISHOU_PROFILE_DIR if platform == "ks" else TRAFFIC_DOUYIN_PROFILE_DIR


def _launch_context(profile_dir: Path, headless: bool = False) -> Any:
    profile_dir.mkdir(parents=True, exist_ok=True)
    try:
        from cloakbrowser import launch_persistent_context
    except ImportError as exc:
        raise TrafficStop(
            "缺少 CloakBrowser 依赖，任务已停止。",
            "本地后端没有安装 CloakBrowser 浏览器依赖。",
            "请到引流设置执行“检查并自动安装”，安装完成后再重试。",
            "probe",
            {"error": str(exc)},
        ) from exc
    try:
        # CloakBrowser 内部会启动 Playwright，并在 context.close() 时清理驱动。
        return launch_persistent_context(
            str(profile_dir),
            headless=headless,
            viewport=None if not headless else {"width": 1440, "height": 900},
            locale="zh-CN",
            args=["--start-maximized"] if not headless else [],
        )
    except Exception as exc:
        raise TrafficStop(
            "CloakBrowser 浏览器启动失败，任务已停止。",
            "CloakBrowser 无法启动专用浏览器内核。",
            "请到引流设置执行环境检查和自动安装；如果仍失败，请查看安装输出。",
            "probe",
            {"error": repr(exc), "profile_dir": str(profile_dir)},
        ) from exc


def _target_url(plan: dict[str, Any]) -> str:
    if plan.get("platform") == "ks":
        return KUAISHOU_RECO_URL
    if plan["source_mode"] in KEYWORD_SOURCE_MODES and plan["source_value"]:
        return f"https://www.douyin.com/search/{quote(plan['source_value'])}"
    return "https://www.douyin.com/?recommend=1"


def _goto_with_timeout_tolerance(page: Any, url: str, timeout_ms: int) -> bool:
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        return True
    except Exception as exc:
        if _is_recoverable_playwright_error(exc):
            return False
        raise


def _is_recoverable_playwright_error(exc: Exception) -> bool:
    name = exc.__class__.__name__.lower()
    message = str(exc).lower()
    return "timeout" in name or "timeout" in message or "element is not attached" in message


def _is_browser_closed_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return "browser has been closed" in message or "target page, context or browser has been closed" in message


def _wait_or_stop(page: Any, run_id: str, timeout_ms: int) -> None:
    remaining = max(0, int(timeout_ms))
    while remaining > 0:
        _raise_if_stop_requested(run_id)
        step = min(1000, remaining)
        page.wait_for_timeout(step)
        remaining -= step
    _raise_if_stop_requested(run_id)


def _wait_for_douyin_ready_signal(page: Any, run_id: str, timeout_ms: int) -> bool:
    """Return as soon as the page exposes a video or a visible login/verify state."""
    deadline = time.monotonic() + max(0, timeout_ms) / 1000
    while time.monotonic() < deadline:
        _raise_if_stop_requested(run_id)
        ready = bool(page.evaluate(
            r"""
            () => {
              const text = (document.body?.innerText || '').replace(/\s+/g, ' ');
              return Boolean(
                document.querySelector('[data-e2e="feed-active-video"]') ||
                document.querySelector('a[href*="/video/"], a[href*="/note/"]') ||
                /安全验证|人机验证|扫码登录|手机号登录/.test(text)
              );
            }
            """
        ))
        if ready:
            return True
        page.wait_for_timeout(min(200, max(1, int((deadline - time.monotonic()) * 1000))))
    _raise_if_stop_requested(run_id)
    return False


def _wait_for_douyin_video_signal(page: Any, run_id: str, timeout_ms: int = 3000) -> bool:
    deadline = time.monotonic() + max(0, timeout_ms) / 1000
    while time.monotonic() < deadline:
        _raise_if_stop_requested(run_id)
        state = _douyin_page_state(page)
        if state.get("activeVideoId"):
            return True
        if state.get("loginPrompt") or state.get("verifyPrompt"):
            return False
        page.wait_for_timeout(min(200, max(1, int((deadline - time.monotonic()) * 1000))))
    _raise_if_stop_requested(run_id)
    return False


def _ensure_page_ready(page: Any) -> None:
    state = _douyin_page_state(page)
    if state.get("verifyPrompt"):
        raise TrafficStop(
            "抖音出现安全验证，任务已停止。请在浏览器中手动完成验证，不会自动绕过。",
            "页面出现安全验证或人机验证。",
            "手动完成验证后，再重新启动引流任务。",
            "login",
            state,
        )
    if state.get("loginPrompt"):
        raise TrafficStop(
            "抖音登录已失效，任务已停止。请到引流设置重新扫码登录后再启动。",
            "页面出现登录提示。",
            "请到引流设置打开抖音登录窗口，扫码登录后再重试。",
            "login",
            state,
        )


def _douyin_page_state(page: Any) -> dict[str, Any]:
    return dict(page.evaluate(
        """
        () => {
          const text = (document.body?.innerText || '').replace(/\\s+/g, ' ');
          const active = document.querySelector('[data-e2e="feed-active-video"]');
          const pathId = location.pathname.match(/\\/(?:video|note)\\/([^/?#]+)/)?.[1] || '';
          return {
            url: location.href,
            title: document.title,
            activeVideoId: active?.getAttribute('data-e2e-vid') || pathId,
            loginPrompt: /扫码登录|立即登录|登录后|手机号登录/.test(text),
            verifyPrompt: /安全验证|人机验证|验证码中间页|verify_check|secsdk-captcha|captcha/.test(location.href + text),
          };
        }
        """
    ))


def _detect_douyin_page_mode(page: Any) -> str:
    state = _douyin_page_state(page)
    if state.get("verifyPrompt"):
        return "captcha_required"
    if state.get("loginPrompt"):
        return "login_required"
    url = str(state.get("url") or "")
    if "modal_id=" in url and "douyin.com/jingxuan" in url:
        return "jingxuan_modal_feed"
    if "/video/" in url or "/note/" in url:
        return "video_detail"
    if "/search/" in url:
        return "search_result"
    if "douyin.com" in url:
        return "home_grid"
    return "unsupported"


def _navigate_to_executable_video(page: Any, run_id: str, source_mode: str, video_cache: dict[str, Any] | None = None, cooldown_hours: int = 24, project_author_keys: set[str] | None = None, source_value: str = "") -> str:
    if _read_active_video(page, video_cache)["video_id"]:
        return "active"
    if source_mode == "random_feed":
        if _open_random_visible_video(page, run_id, video_cache):
            return "page"
        raise TrafficStop(
            "随机推荐流没有找到可点击的视频，任务已停止。",
            "当前抖音首页或精选页没有可见的视频卡片。",
            "请确认已经登录抖音，并且首页能正常显示视频后再重新启动随机引流。",
            "probe",
            {"url": page.url},
        )
    if source_mode in KEYWORD_SOURCE_MODES:
        if _open_search_result_video(page, run_id, video_cache, project_author_keys):
            return "search"
        raise TrafficStop(
            "搜索页没有找到可点击的视频，任务已停止。",
            "当前搜索结果页没有可见视频卡片。",
            "请换一个关键词，或确认抖音搜索页能正常显示视频结果。",
            "probe",
            {"url": page.url, "source_value": source_value},
        )
    if source_mode == "competitor_videos":
        project = _random_project_video_candidate(cooldown_hours, project_author_keys, source_value)
        if project:
            _remember_project_author(project_author_keys, project)
            _goto_video_candidate(page, run_id, project["url"], "已从项目库按来源队列跳转一个视频。")
            return "project"
        raise TrafficStop(
            "项目库没有可执行的视频，任务已停止。",
            "没有找到符合来源、作者冷却和防重复条件的视频。",
            "请先通过拓客工作台采集视频，或调整关键词/作者冷却时间后重试。",
            "probe",
            {"source_mode": source_mode, "source_value": source_value},
        )
    if "douyin.com/jingxuan" in page.url:
        candidates = _visible_video_links(page)
        if candidates:
            _goto_video_candidate(page, run_id, random.choice(candidates[:8])["href"], "已从精选页随机跳转一个视频。")
            return "page"
        raise TrafficStop(
            "抖音停留在精选页，任务已停止。",
            "精选页没有可跳转的视频链接，项目库也没有可用抖音视频。",
            "请先通过拓客工作台采集一些抖音视频，或在引流设置打开登录窗口后进入任意视频。",
            "probe",
            {"url": page.url, "saved_video_url": _last_douyin_video_url()},
        )
    candidates = _visible_video_links(page)
    if not candidates:
        return "none"
    _goto_video_candidate(page, run_id, random.choice(candidates[:8])["href"], "已从当前页面随机跳转一个视频。")
    return "page"


def _open_random_visible_video(page: Any, run_id: str, video_cache: dict[str, Any] | None = None) -> bool:
    if "/video/" in page.url or "/note/" in page.url:
        _goto_with_timeout_tolerance(page, "https://www.douyin.com/?recommend=1", 60_000)
        page.wait_for_timeout(1500)
    for _ in range(4):
        candidates = _visible_video_candidates(page)
        random.shuffle(candidates)
        for candidate in candidates[:6]:
            if _click_video_candidate(page, run_id, candidate, "已从抖音首页随机点击一个视频。", True) and (
                _read_active_video(page, video_cache)["video_id"] or _is_douyin_video_url(page.url)
            ):
                return True
        page.mouse.wheel(0, random.randint(500, 1100))
        page.wait_for_timeout(1200)
    return False


def _open_search_result_video(page: Any, run_id: str, video_cache: dict[str, Any] | None = None, seen_source_keys: set[str] | None = None) -> bool:
    for _ in range(3):
        candidates = _visible_video_candidates(page) or _visible_video_links(page)
        candidates = [item for item in candidates if _source_candidate_key(item) not in (seen_source_keys or set())]
        for candidate in candidates[:12]:
            if _click_video_candidate(page, run_id, candidate, "已从搜索结果点击一个视频。") and _read_active_video(page, video_cache)["video_id"]:
                if seen_source_keys is not None:
                    seen_source_keys.add(_source_candidate_key(candidate))
                return True
        page.mouse.wheel(0, random.randint(600, 1200))
        page.wait_for_timeout(1000)
    return False


def _click_video_candidate(page: Any, run_id: str, candidate: dict[str, Any], message: str, prefer_modal: bool = False) -> bool:
    # ponytail: 随机引流必须来自当前抖音页面，不能退回项目库视频。
    url = str(candidate.get("href") or page.url)
    target_url = _modal_feed_url(url) if prefer_modal else ""
    if target_url:
        url = target_url
    _append_log(run_id, "info", "probe", message, "当前页面不是可执行视频流", "系统会进入具体视频后继续执行。", {"from_url": page.url, "target_url": url})
    clicked = False
    if target_url:
        if not _goto_with_timeout_tolerance(page, target_url, 60_000):
            _append_log(run_id, "warning", "probe", "视频页面加载超时，继续检查当前页面。", "抖音可能已经打开视频，但没有返回加载完成信号", "系统会继续识别当前页面；如果不能执行，会自动换下一个视频。", {"target_url": target_url, "current_url": page.url})
        clicked = True
    elif isinstance(candidate.get("x"), (int, float)) and isinstance(candidate.get("y"), (int, float)):
        page.mouse.click(float(candidate["x"]), float(candidate["y"]))
        clicked = True
    if not clicked and candidate.get("href"):
        clicked = page.evaluate(
            """
            href => {
              const link = Array.from(document.querySelectorAll('a[href*="/video/"], a[href*="/note/"]'))
                .find(item => item.href === href);
              if (!link) return false;
              link.scrollIntoView({block: 'center', inline: 'center'});
              link.click();
              return true;
            }
            """,
            url,
        )
    if not clicked and not _goto_with_timeout_tolerance(page, url, 60_000):
        _append_log(run_id, "warning", "probe", "视频页面加载超时，继续检查当前页面。", "抖音可能已经打开视频，但没有返回加载完成信号", "系统会继续识别当前页面；如果不能执行，会自动换下一个视频。", {"target_url": url, "current_url": page.url})
    _wait_for_douyin_video_signal(page, run_id)
    _ensure_page_ready(page)
    return True


def _modal_feed_url(url: str) -> str:
    match = re.search(r"/(?:video|note)/(\d+)", url)
    return f"https://www.douyin.com/jingxuan?modal_id={match.group(1)}" if match else ""


def _next_video(page: Any, run_id: str, plan: dict[str, Any], previous_video_id: str, video_cache: dict[str, Any] | None, cooldown_hours: int, project_author_keys: set[str] | None = None, silent: bool = False) -> bool:
    if plan["source_mode"] in KEYWORD_SOURCE_MODES:
        search_url = f"https://www.douyin.com/search/{quote(str(plan.get('source_value') or ''))}"
        if not _goto_with_timeout_tolerance(page, search_url, 60_000):
            return False
        _wait_for_douyin_ready_signal(page, run_id, 4_000)
        if not _open_search_result_video(page, run_id, video_cache, project_author_keys):
            return False
        next_id = _read_active_video(page, video_cache)["video_id"]
        return bool(next_id and next_id != previous_video_id)
    if plan["source_mode"] == "competitor_videos":
        project = _random_project_video_candidate(cooldown_hours, project_author_keys, str(plan.get("source_value") or ""))
        if not project:
            return False
        _remember_project_author(project_author_keys, project)
        _goto_video_candidate(page, run_id, project["url"], "已按作者冷却随机切换到项目库另一个视频。", silent)
        next_id = _read_active_video(page, video_cache)["video_id"]
        return bool(next_id and next_id != previous_video_id)
    if _advance_video(page, previous_video_id, video_cache):
        return True
    # 推荐流翻页偶尔会卡在已加载视频末尾，重新进入首页比直接终止整批任务更稳。
    if plan["source_mode"] == "random_feed" and _goto_with_timeout_tolerance(page, "https://www.douyin.com/?recommend=1", 60_000):
        _wait_for_douyin_ready_signal(page, run_id, 4_000)
        if _open_random_visible_video(page, run_id, video_cache):
            next_id = str(_read_active_video(page, video_cache).get("video_id") or "")
            return bool(next_id and next_id != previous_video_id)
    return False


def _source_candidate_key(candidate: dict[str, Any]) -> str:
    url = str(candidate.get("href") or "")
    match = re.search(r"/(?:video|note)/([^/?#]+)", url)
    if match:
        return match.group(1)
    return url or f"{candidate.get('kind', '')}:{candidate.get('text', '')}:{candidate.get('x', '')}:{candidate.get('y', '')}"


def _goto_video_candidate(page: Any, run_id: str, url: str, message: str, silent: bool = False) -> None:
    # ponytail: 先复用已有视频入口；后续需要纯随机推荐再接入接口队列。
    if not silent:
        _append_log(run_id, "info", "probe", message, "当前页面不是可执行视频流", "系统会进入具体视频后继续执行。", {"from_url": page.url, "target_url": url})
    if not _goto_with_timeout_tolerance(page, url, 60_000):
        if not silent:
            _append_log(run_id, "warning", "probe", "视频页面加载超时，继续检查当前页面。", "抖音可能已经打开视频，但没有返回加载完成信号", "系统会继续识别当前页面；如果不能执行，会自动换下一个视频。", {"target_url": url, "current_url": page.url})
    _wait_for_douyin_video_signal(page, run_id)
    _ensure_page_ready(page)


def _random_project_video_url() -> str:
    candidate = _random_project_video_candidate(0)
    return candidate["url"] if candidate else ""


def _split_source_values(value: str) -> list[str]:
    values: list[str] = []
    for item in re.split(r"[,\r\n]+", str(value or "")):
        item = item.strip()
        if item and item not in values:
            values.append(item)
    return values


def _source_value_content_id(value: str) -> str:
    match = re.search(r"/(?:video|note)/([^/?#]+)", value)
    return match.group(1) if match else (value if not value.startswith("http") else "")


def _random_project_video_candidate(cooldown_hours: int, exclude_author_keys: set[str] | None = None, selected_sources: str = "") -> dict[str, str] | None:
    modifier = f"-{max(cooldown_hours, 0)} hours"
    selected = _split_source_values(selected_sources)
    selected_ids = [_source_value_content_id(item) for item in selected]
    selected_ids = [item for item in selected_ids if item]
    selected_clause = ""
    params: list[Any] = []
    if selected:
        url_placeholders = ",".join("?" for _ in selected)
        id_placeholders = ",".join("?" for _ in selected_ids) or "NULL"
        selected_clause = f"AND (c.content_url IN ({url_placeholders}) OR c.content_id IN ({id_placeholders}))"
        params.extend(selected)
        params.extend(selected_ids)
    params.append(modifier)
    author_cooldown_clause = "" if selected else """
                    OR (u.platform_user_id <> '' AND tr.author_id = u.platform_user_id)
                    OR (u.nickname <> '' AND tr.author_name = u.nickname)
    """
    order_clause = "ORDER BY c.updated_at DESC" if selected else "ORDER BY RANDOM() LIMIT 20"
    with database.connect() as conn:
        rows = conn.execute(
            f"""
            SELECT c.content_url, c.content_id,
                   COALESCE(u.platform_user_id, '') AS author_id,
                   COALESCE(u.nickname, '') AS author_name
            FROM contents c
            LEFT JOIN user_accounts u ON u.id = c.author_account_id
            WHERE c.platform = 'dy'
              AND u.competitor_status = '竞品'
              {selected_clause}
              AND (
                c.content_url LIKE '%douyin.com/video/%'
                OR c.content_url LIKE '%douyin.com/note/%'
                OR c.content_id <> ''
              )
              AND NOT EXISTS (
                SELECT 1 FROM traffic_records tr
                WHERE tr.platform = 'dy'
                  AND tr.created_at >= datetime('now', 'localtime', ?)
                  AND (
                    (c.content_id <> '' AND tr.video_id = c.content_id)
                    OR (c.content_url <> '' AND tr.video_url = c.content_url)
                    {author_cooldown_clause}
                  )
              )
            {order_clause}
            """,
            params,
        ).fetchall()
    candidates = [candidate for row in rows if (candidate := _project_video_candidate_from_row(row))]
    matched = {str(row["content_url"] or "") for row in rows} | {str(row["content_id"] or "") for row in rows}
    for value in selected:
        content_id = _source_value_content_id(value)
        if value in matched or content_id in matched:
            continue
        if _is_douyin_video_url(value):
            candidate = {"url": value, "author_id": "", "author_name": ""}
        elif content_id:
            candidate = {"url": f"https://www.douyin.com/video/{content_id}", "author_id": "", "author_name": ""}
        else:
            continue
        if not _project_video_recently_processed(candidate, modifier):
            candidates.append(candidate)
    for candidate in candidates:
        if selected or _project_author_key(candidate) not in (exclude_author_keys or set()):
            return candidate
    return None


def _project_video_recently_processed(candidate: dict[str, str], modifier: str) -> bool:
    content_id = _source_value_content_id(candidate.get("url", ""))
    with database.connect() as conn:
        row = conn.execute(
            """
            SELECT 1 FROM traffic_records
            WHERE platform = 'dy'
              AND created_at >= datetime('now', 'localtime', ?)
              AND ((? <> '' AND video_id = ?) OR video_url = ?)
            LIMIT 1
            """,
            (modifier, content_id, content_id, candidate.get("url", "")),
        ).fetchone()
    return row is not None


def _project_video_candidate_from_row(row: Any) -> dict[str, str] | None:
    url = str(row["content_url"] or "")
    author_id = str(row["author_id"] or "")
    author_name = str(row["author_name"] or "")
    if _is_douyin_video_url(url):
        return {"url": url, "author_id": author_id, "author_name": author_name}
    content_id = str(row["content_id"] or "")
    return {"url": f"https://www.douyin.com/video/{content_id}", "author_id": author_id, "author_name": author_name} if content_id else None


def _remember_project_author(project_author_keys: set[str] | None, candidate: dict[str, str]) -> None:
    key = _project_author_key(candidate)
    if project_author_keys is not None and key:
        project_author_keys.add(key)


def _project_author_key(candidate: dict[str, str]) -> str:
    return candidate.get("author_id") or candidate.get("author_name") or ""


def _visible_video_links(page: Any) -> list[dict[str, Any]]:
    return list(page.evaluate(
        """
        () => {
          const normalize = value => (value || '').replace(/\\s+/g, ' ').trim();
          return Array.from(document.querySelectorAll('a[href*="/video/"], a[href*="/note/"]'))
            .map(link => {
              const rect = link.getBoundingClientRect();
              const style = window.getComputedStyle(link);
              return {
                href: link.href,
                text: normalize(link.textContent).slice(0, 160),
                x: rect.left + rect.width / 2,
                y: rect.top + rect.height / 2,
                visible: rect.width > 0 && rect.height > 0
                  && rect.bottom > 0 && rect.top < window.innerHeight
                  && rect.right > 0 && rect.left < window.innerWidth
                  && style.display !== 'none' && style.visibility !== 'hidden',
              };
            })
            .filter(item => item.href && item.visible)
            .slice(0, 20);
        }
        """
    ))


def _visible_video_candidates(page: Any) -> list[dict[str, Any]]:
    return list(page.evaluate(
        """
        () => {
          const normalize = value => (value || '').replace(/\\s+/g, ' ').trim();
          const isVisible = (node, rect) => {
            const style = window.getComputedStyle(node);
            const centerX = rect.left + rect.width / 2;
            const leftGuard = Math.min(260, Math.max(180, window.innerWidth * 0.16));
            return rect.width >= 120 && rect.height >= 90
              && rect.bottom > 80 && rect.top < window.innerHeight - 20
              && rect.right > 80 && rect.left < window.innerWidth - 20
              && centerX > leftGuard
              && rect.width < window.innerWidth * 0.95
              && rect.height < window.innerHeight * 0.95
              && style.display !== 'none' && style.visibility !== 'hidden' && style.opacity !== '0';
          };
          const asItem = (node, kind) => {
            const rect = node.getBoundingClientRect();
            if (!isVisible(node, rect)) return null;
            const link = node.closest('a[href*="/video/"], a[href*="/note/"]') || node.querySelector?.('a[href*="/video/"], a[href*="/note/"]');
            return {
              href: link?.href || '',
              kind,
              text: normalize(node.textContent).slice(0, 160),
              x: Math.min(Math.max(rect.left + rect.width / 2, 24), window.innerWidth - 24),
              y: Math.min(Math.max(rect.top + rect.height / 2, 24), window.innerHeight - 24),
            };
          };
          const items = [
            ...Array.from(document.querySelectorAll('a[href*="/video/"], a[href*="/note/"]')).map(node => asItem(node, 'link')),
            ...Array.from(document.querySelectorAll('article, li, section, div')).map(node => {
              const style = window.getComputedStyle(node);
              const mediaCount = node.querySelectorAll('video, img, picture, canvas').length + (style.backgroundImage !== 'none' ? 1 : 0);
              if (mediaCount < 1 || mediaCount > 3) return null;
              return asItem(node, 'card');
            }),
          ].filter(Boolean);
          const seen = new Set();
          return items
            .filter(item => {
              const key = `${Math.round(item.x)}:${Math.round(item.y)}:${item.href}`;
              if (seen.has(key)) return false;
              seen.add(key);
              return true;
            })
            .sort((a, b) => Number(Boolean(b.href)) - Number(Boolean(a.href)))
            .slice(0, 30);
        }
        """
    ))


def _last_douyin_video_url() -> str:
    with database.connect() as conn:
        url = database.get_setting(conn, TRAFFIC_LAST_VIDEO_URL_KEY, "")
    return url if _is_douyin_video_url(url) else ""


def _save_last_douyin_video_url(url: str) -> None:
    if not _is_douyin_video_url(url):
        return
    with database.connect() as conn:
        database.set_setting(conn, TRAFFIC_LAST_VIDEO_URL_KEY, url)


def _is_douyin_video_url(url: str) -> bool:
    return "douyin.com/video/" in url or "douyin.com/note/" in url


def _setup_video_data_cache(page: Any) -> dict[str, Any]:
    cache: dict[str, Any] = {}

    def remember(response: Any) -> None:
        url = response.url
        if "aweme/v1/web/tab/feed/" not in url and "aweme/v1/web/aweme/detail/" not in url:
            return
        try:
            payload = response.json()
        except Exception:
            return
        for item in payload.get("aweme_list", []) or []:
            if item.get("aweme_id"):
                cache[str(item["aweme_id"])] = item
        detail = payload.get("aweme_detail") or payload.get("aweme")
        if isinstance(detail, dict) and detail.get("aweme_id"):
            cache[str(detail["aweme_id"])] = detail

    # laizan 的可靠点：用接口数据缓存视频，再用当前活跃 ID 反查，不靠 DOM 文本猜。
    page.on("response", remember)
    return cache


def _read_active_video(page: Any, video_cache: dict[str, Any] | None = None) -> dict[str, Any]:
    data = page.evaluate(
        """
        () => {
          const active = document.querySelector('[data-e2e="feed-active-video"]')
            || document.querySelector('[data-e2e-vid]')
            || document.querySelector('video')?.closest('[data-e2e-vid]')
            || document.querySelector('video')?.parentElement;
          const normalize = value => (value || '').replace(/\\s+/g, ' ').trim();
          const root = active || document;
          const pathId = location.pathname.match(/\\/(?:video|note)\\/([^/?#]+)/)?.[1] || '';
          const desc = root.querySelector?.('[data-e2e="video-desc"], [data-e2e="detail-video-desc"], .title, h1')?.textContent || active?.textContent || document.title || '';
          const author = root.querySelector?.('[data-e2e="feed-video-nickname"], .account-name, [data-e2e="user-info"]')?.textContent || '';
          const like = root.querySelector?.('[data-e2e="video-player-digg"]')?.textContent || document.querySelector('[data-e2e="video-player-digg"]')?.textContent || '';
          const comment = root.querySelector?.('[data-e2e="feed-comment-icon"]')?.textContent || document.querySelector('[data-e2e="feed-comment-icon"]')?.textContent || '';
          return {
            video_id: active?.getAttribute('data-e2e-vid') || pathId,
            video_url: location.href,
            author_id: '',
            author_name: normalize(author).replace(/^@/, ''),
            video_desc: normalize(desc).slice(0, 800),
            like_count: Number(String(like).replace(/[^0-9]/g, '')) || null,
            comment_count: Number(String(comment).replace(/[^0-9]/g, '')) || null,
          };
        }
        """
    )
    video = dict(data)
    cached = (video_cache or {}).get(str(video.get("video_id") or ""))
    if isinstance(cached, dict):
        return _video_from_aweme(cached, video["video_url"])
    return video


def _video_from_aweme(item: dict[str, Any], fallback_url: str) -> dict[str, Any]:
    stats = item.get("statistics") or {}
    author = item.get("author") or {}
    share_info = item.get("share_info") or {}
    return {
        "video_id": str(item.get("aweme_id") or ""),
        "video_url": item.get("share_url") or share_info.get("share_url") or fallback_url,
        "author_id": str(author.get("uid") or author.get("sec_uid") or ""),
        "author_name": str(author.get("nickname") or ""),
        "video_desc": str(item.get("desc") or "").strip()[:800],
        "like_count": _int_or_none(stats.get("digg_count")),
        "comment_count": _int_or_none(stats.get("comment_count")),
        "aweme_type": item.get("aweme_type"),
        "is_ads": bool(item.get("is_ads") or item.get("is_ad") or item.get("ad_info")),
        "is_live": bool(item.get("live_room") or item.get("is_live")),
    }


def _setup_kuaishou_data_cache(page: Any) -> dict[str, Any]:
    cache: dict[str, Any] = {"_order": []}

    def remember(response: Any) -> None:
        if "/rest/v/feed/hot" not in str(getattr(response, "url", "")):
            return
        try:
            payload = response.json()
        except Exception:
            return
        for feed in payload.get("feeds", []) or []:
            photo = feed.get("photo") or {}
            photo_id = str(photo.get("id") or photo.get("photoId") or "")
            if not photo_id:
                continue
            cache[photo_id] = feed
            if photo_id not in cache["_order"]:
                cache["_order"].append(photo_id)

    page.on("response", remember)
    return cache


def _read_kuaishou_active_video(page: Any, video_cache: dict[str, Any] | None = None) -> dict[str, Any]:
    data = dict(page.evaluate(
        """
        () => {
          const visibleVideos = Array.from(document.querySelectorAll('video')).map(video => {
            const rect = video.getBoundingClientRect();
            const visibleWidth = Math.max(0, Math.min(rect.right, innerWidth) - Math.max(rect.left, 0));
            const visibleHeight = Math.max(0, Math.min(rect.bottom, innerHeight) - Math.max(rect.top, 0));
            return {src: video.currentSrc || video.src || '', area: visibleWidth * visibleHeight};
          }).filter(item => item.src && item.area > 1000).sort((a, b) => b.area - a.area);
          return {
            video_url: location.href,
            video_src: visibleVideos[0]?.src || '',
            page_text: (document.body?.innerText || document.title || '').replace(/\\s+/g, ' ').trim().slice(0, 800),
          };
        }
        """
    ))
    feed = _kuaishou_feed_for_src(video_cache or {}, str(data.get("video_src") or ""))
    if feed:
        return _video_from_kuaishou_feed(feed, str(data.get("video_url") or KUAISHOU_RECO_URL))
    return {
        "platform": "ks",
        "video_id": _hash_text(str(data.get("video_src") or data.get("video_url") or ""))[:16] if data.get("video_src") else "",
        "video_url": str(data.get("video_url") or KUAISHOU_RECO_URL),
        "author_id": "",
        "author_name": "",
        "video_desc": _clean_kuaishou_text(data.get("page_text", ""))[:800],
        "like_count": None,
        "comment_count": None,
    }


def _kuaishou_feed_for_src(cache: dict[str, Any], src: str) -> dict[str, Any] | None:
    if not src:
        return None
    for photo_id in cache.get("_order", []):
        feed = cache.get(photo_id)
        if not isinstance(feed, dict):
            continue
        if photo_id in src or any(_same_video_url(url, src) for url in _kuaishou_feed_urls(feed)):
            return feed
    return None


def _same_video_url(url: str, src: str) -> bool:
    left = str(url or "").split("?", 1)[0]
    right = str(src or "").split("?", 1)[0]
    return bool(left and right and (left == right or left in right or right in left))


def _kuaishou_feed_urls(feed: dict[str, Any]) -> list[str]:
    urls: list[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key in {"url", "backupUrl"}:
                    walk(item)
                elif isinstance(item, (dict, list)):
                    walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
        elif isinstance(value, str) and value.startswith("http"):
            urls.append(value)

    walk(feed.get("photo") or {})
    return urls[:40]


def _video_from_kuaishou_feed(feed: dict[str, Any], fallback_url: str) -> dict[str, Any]:
    photo = feed.get("photo") or {}
    author = feed.get("author") or {}
    photo_id = str(photo.get("id") or photo.get("photoId") or "")
    return {
        "platform": "ks",
        "video_id": photo_id,
        "video_url": f"https://www.kuaishou.com/short-video/{photo_id}" if photo_id else fallback_url,
        "author_id": str(author.get("id") or ""),
        "author_name": _clean_kuaishou_text(author.get("name", "")),
        "video_desc": _clean_kuaishou_text(photo.get("caption", ""))[:800],
        "like_count": _int_or_none(photo.get("likeCount")),
        "comment_count": _int_or_none((feed.get("comment") or {}).get("count")),
        "is_live": bool((author.get("livingInfo") or {}).get("living")),
        "is_ads": bool(photo.get("ad") or photo.get("adInfo") or feed.get("ad")),
    }


def _clean_kuaishou_text(value: Any) -> str:
    text = str(value or "").strip()
    try:
        # 快手部分接口在 Playwright 中会按 latin1 展示 UTF-8 字节，修正后再入库。
        return text.encode("latin1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text


def _ensure_kuaishou_page_ready(page: Any) -> None:
    state = dict(page.evaluate(
        """
        () => {
          const text = (document.body?.innerText || '').replace(/\\s+/g, ' ');
          return {
            url: location.href,
            title: document.title,
            loginPrompt: /扫码登录|手机号登录|立即登录|登录后/.test(text) && !/退出登录/.test(text),
            verifyPrompt: /安全验证|人机验证|验证码|captcha|verify/.test(location.href + text),
          };
        }
        """
    ))
    if state.get("verifyPrompt"):
        raise TrafficStop("快手出现安全验证，任务已停止。请在浏览器中手动完成验证，不会自动绕过。", "页面出现安全验证或人机验证。", "手动完成验证后，再重新启动引流任务。", "login", state)
    if state.get("loginPrompt"):
        raise TrafficStop("快手登录已失效，任务已停止。请到引流设置重新登录后再启动。", "页面出现登录提示。", "请到引流设置打开快手登录窗口，登录后再重试。", "login", state)


def _kuaishou_video_skip_reason(video: dict[str, Any]) -> str:
    if video.get("is_live"):
        return "直播视频"
    if video.get("is_ads"):
        return "广告视频"
    return ""


def _advance_kuaishou_video(page: Any, previous_video_id: str, video_cache: dict[str, Any] | None = None) -> bool:
    _close_kuaishou_comment_panel(page)
    for action in ("next_button", "arrow_down", "wheel", "page_down"):
        if action == "next_button":
            if not _click_kuaishou_next_video(page):
                continue
        elif action == "arrow_down":
            page.keyboard.press("ArrowDown")
        elif action == "wheel":
            page.mouse.wheel(0, 1600)
        else:
            page.keyboard.press("PageDown")
        if _wait_for_video_change(page, previous_video_id, _read_kuaishou_active_video, video_cache):
            return True
    return False


def _click_kuaishou_next_video(page: Any) -> bool:
    # 用真实鼠标点右侧下一条按钮，避免 DOM click 被遮挡或焦点状态吞掉。
    point = page.evaluate(
        """
        () => {
          const candidates = [...document.querySelectorAll('.next, .hover-tip.nextVideo')];
          for (const el of candidates) {
            const r = el.getBoundingClientRect();
            if (r.width > 0 && r.height > 0 && r.bottom > 0 && r.right > 0) {
              return {x: r.x + r.width / 2, y: r.y + r.height / 2};
            }
          }
          return null;
        }
        """
    )
    if not point:
        return False
    page.mouse.click(point["x"], point["y"])
    return True


def _is_regular_video(video: dict[str, Any]) -> bool:
    aweme_type = _int_or_none(video.get("aweme_type"))
    return aweme_type in (None, 0)


def _video_skip_reason(page: Any, video: dict[str, Any]) -> str:
    if not _is_regular_video(video):
        return "不是常规视频"
    if video.get("is_live"):
        return "直播视频"
    if video.get("is_ads"):
        return "广告视频"
    flags = _current_video_flags(page, video.get("video_id", ""))
    if flags.get("is_live"):
        return "直播视频"
    if flags.get("is_ad"):
        return "广告视频"
    return ""


def _current_video_flags(page: Any, video_id: str) -> dict[str, bool]:
    try:
        return dict(page.evaluate(
            """
            videoId => {
              const active = document.querySelector('[data-e2e="feed-active-video"]');
              const detail = videoId ? document.querySelector(`[class*="video_${videoId}"]`) : null;
              const root = active || detail || document.body || document;
              const text = (root.innerText || document.body?.innerText || '').replace(/\\s+/g, ' ');
              const href = location.href;
              const hasAdCta = /广告|了解详情|立即购买|去购买|领取优惠|查看详情/.test(text);
              return {
                is_live: /直播中|进入直播间|正在直播|\\/live\\//.test(href + ' ' + text),
                is_ad: hasAdCta && /广告|赞助|立即购买|去购买|了解详情/.test(text),
              };
            }
            """,
            str(video_id or ""),
        ))
    except Exception:
        return {"is_live": False, "is_ad": False}


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _execute_kuaishou_actions_with_retry(run_id: str, plan: dict[str, Any], page: Any, video: dict[str, Any], action_budget: int | None = None, action_probability: int = 100) -> tuple[list[str], str, str, bool]:
    if not plan["actions"]:
        return [], "", "", False
    done: list[str] = []
    comment_text = ""
    remaining = action_budget
    failed_twice = False
    attempted = False
    probability_skipped = False
    if remaining is not None and remaining <= 0:
        _append_log(run_id, "warning", "stop", "今日动作上限已用完，当前视频只浏览不互动。", "每日动作上限已达到", "系统会结束本轮任务，避免超过设置的互动频率。", {"video_id": video.get("video_id")})
        return done, comment_text, "", False
    for action, label, phase in (
        ("like", "点赞视频", "like"),
        ("collect", "收藏视频", "collect"),
        ("follow", "关注作者", "follow"),
    ):
        if action not in plan["actions"]:
            continue
        if _skip_action_by_probability(run_id, video, label, phase, action_probability):
            probability_skipped = True
            continue
        attempted = True
        ok, failed = _run_action_with_retry(run_id, page, video, label, phase, lambda action=action, label=label: _execute_kuaishou_action(run_id, page, video, action, label))
        failed_twice = failed_twice or failed
        if ok:
            done.append(label)
            remaining = None if remaining is None else remaining - 1
        elif failed and not done:
            return done, comment_text, "", True
        if remaining is not None and remaining <= 0:
            return done, comment_text, "", False
    if "comment_text" in plan["actions"]:
        if _skip_action_by_probability(run_id, video, "评论", "comment", action_probability):
            probability_skipped = True
        else:
            attempted = True
            comment_text = _pick_text()
            ok, failed = _run_action_with_retry(run_id, page, video, "评论", "comment", lambda: _execute_kuaishou_comment(run_id, page, video, comment_text))
            failed_twice = failed_twice or failed
            if ok:
                done.append("评论")
            elif failed and not done:
                return done, comment_text, "", True
    if not done and probability_skipped and not attempted:
        return [], comment_text, "", False
    return done, comment_text, "", bool(failed_twice and not done)


def _execute_kuaishou_action(run_id: str, page: Any, video: dict[str, Any], action: str, label: str) -> bool:
    if _dedup_exists(video, action, ""):
        _append_log(run_id, "warning", action, f"这个快手视频已经执行过{label}，本次跳过。", "防重复命中", "系统已自动跳过，不需要处理。", {"video_id": video["video_id"]})
        return False
    point = _kuaishou_visible_center(page, "favorite" if action == "collect" else action)
    if not point:
        _append_log(run_id, "warning", action, f"没有找到快手{label}按钮，已跳过。", "当前视频没有可点击的操作按钮", "系统会继续处理后续视频。", {"video_id": video["video_id"]})
        return False
    payload = _kuaishou_response_after_click(page, _kuaishou_action_endpoint(action), lambda: page.mouse.click(point["x"], point["y"]))
    if payload is None or not _kuaishou_action_confirmed(payload):
        _append_log(run_id, "warning", action, f"没有收到快手服务端确认，已跳过{label}记录。", "前端按钮变化不能证明账号已经真实落账", "系统不会把未确认动作写为成功；如果频繁出现，请降低执行频率或检查账号状态。", {"video_id": video["video_id"], "point": point})
        return False
    _append_log(run_id, "success", action, f"{label}已执行。", "已收到快手服务端成功响应", "可以在操作记录中查看本视频结果。", {"video_id": video["video_id"], "response": _compact_payload(payload)})
    _insert_dedup(video, action, "", "done")
    return True


def _execute_kuaishou_comment(run_id: str, page: Any, video: dict[str, Any], text: str) -> bool:
    if not text:
        return False
    content_hash = _hash_text(text)
    if _dedup_exists(video, "comment", content_hash):
        _append_log(run_id, "warning", "comment", "这个快手视频已经发送过相同评论，已跳过。", "防重复命中", "系统已自动跳过，不需要处理。", {"video_id": video["video_id"]})
        return False
    point = _kuaishou_visible_center(page, "comment")
    if not point:
        _append_log(run_id, "warning", "comment", "没有找到快手评论按钮，已跳过评论。", "当前视频没有可点击的评论入口", "系统会继续处理后续视频。", {"video_id": video["video_id"]})
        return False
    page.mouse.click(point["x"], point["y"])
    editor = page.locator(".comment-input input").last
    try:
        editor.wait_for(state="visible", timeout=2_000)
    except Exception:
        _append_log(run_id, "warning", "comment", "没有找到快手评论输入框，已跳过评论。", "评论面板没有正常打开", "系统会继续处理后续视频。", {"video_id": video["video_id"]})
        return False
    editor.fill(text)
    payload = _kuaishou_response_after_click(page, "/rest/v/photo/comment/add", lambda: _click_kuaishou_comment_send(page))
    if payload is None or not _payload_status_ok(payload):
        _append_log(run_id, "warning", "comment", "快手评论没有确认发送成功，已跳过记录。", "没有捕获到评论发布成功响应", "系统不会把未确认评论写为成功；请降低执行频率或检查账号状态。", {"video_id": video["video_id"], "text": text})
        return False
    _append_log(run_id, "success", "comment", "快手评论已发送。", "评论发布接口返回成功", "可以在操作记录中查看实际文案。", {"video_id": video["video_id"], "text": text, "response": _compact_payload(payload)})
    _insert_dedup(video, "comment", content_hash, "done")
    return True


def _kuaishou_visible_center(page: Any, kind: str) -> dict[str, Any] | None:
    point = page.evaluate(
        """
        (kind) => {
          const visible = [...document.querySelectorAll('*')].map(el => {
            const r = el.getBoundingClientRect();
            const cls = String(el.className || '');
            const text = (el.innerText || el.textContent || '').trim().replace(/\\s+/g, ' ');
            return {el, r, cls, text};
          }).filter(x =>
            x.r.width > 5 && x.r.height > 5 && x.r.bottom > 0 && x.r.right > 0 &&
            x.r.y > 250 && x.r.y < 760 && x.r.x > 1000
          );
          let hit = null;
          if (kind === 'follow') hit = visible.find(x => x.cls === 'btn' && x.r.width >= 15 && x.r.height >= 10 && x.r.y > 300 && x.r.y < 460);
          else if (kind === 'like') hit = visible.find(x => x.cls.includes('hover-tip like')) || visible.find(x => x.cls.includes('like-btn'));
          else if (kind === 'favorite') hit = visible.find(x => x.cls.includes('hover-tip favorite')) || visible.find(x => x.cls.includes('star'));
          else if (kind === 'comment') hit = visible.find(x => x.cls.includes('commentPanel'));
          if (!hit) return null;
          return {x: hit.r.x + hit.r.width / 2, y: hit.r.y + hit.r.height / 2, cls: hit.cls, text: hit.text, box: [hit.r.x, hit.r.y, hit.r.width, hit.r.height]};
        }
        """,
        kind,
    )
    return dict(point) if point else None


def _kuaishou_action_endpoint(action: str) -> str:
    return {
        "like": "/rest/v/photo/like",
        "collect": "/rest/v/photo/collect",
        "follow": "/rest/v/relation/follow",
    }[action]


def _kuaishou_response_after_click(page: Any, endpoint: str, click: Any) -> dict[str, Any] | None:
    try:
        with page.expect_response(lambda response: endpoint in str(getattr(response, "url", "")), timeout=ACTION_RESPONSE_TIMEOUT_MS) as response_info:
            if click() is False:
                raise RuntimeError("action control unavailable")
        payload = _response_json(response_info.value)
        payload["_request_post_data"] = _request_post_data(response_info.value)
        payload["_confirm_url"] = _response_path(response_info.value)
        return payload
    except Exception:
        return None


def _kuaishou_action_confirmed(payload: dict[str, Any]) -> bool:
    if not _payload_status_ok(payload):
        return False
    post_data = str(payload.get("_request_post_data") or "").replace(" ", "").lower()
    return '"cancel":1' not in post_data and '"cancel":true' not in post_data


def _click_kuaishou_comment_send(page: Any) -> bool:
    send = page.evaluate(
        """
        () => {
          const el = document.querySelector('.comment-input .send-btn');
          if (!el) return null;
          const r = el.getBoundingClientRect();
          return {x: r.x + r.width / 2, y: r.y + r.height / 2};
        }
        """
    )
    if not send:
        return False
    page.mouse.click(send["x"], send["y"])
    return True


def _close_kuaishou_comment_panel(page: Any) -> None:
    try:
        if page.locator(".comment-input input").last.is_visible():
            page.keyboard.press("Escape")
    except Exception:
        return


def _execute_actions_with_retry(run_id: str, plan: dict[str, Any], page: Any, video: dict[str, Any], action_budget: int | None = None, action_probability: int = 100) -> tuple[list[str], str, str, bool]:
    if not plan["actions"]:
        return [], "", "", False
    done: list[str] = []
    comment_text = ""
    image_path = ""
    failed_twice = False
    attempted = False
    probability_skipped = False
    remaining = action_budget
    if remaining is not None and remaining <= 0:
        _append_log(run_id, "warning", "stop", "今日动作上限已用完，当前视频只浏览不互动。", "每日动作上限已达到", "系统会结束本轮任务，避免超过设置的互动频率。", {"video_id": video.get("video_id")})
        return done, comment_text, image_path, False
    if "like" in plan["actions"]:
        if _skip_action_by_probability(run_id, video, "点赞视频", "like", action_probability):
            probability_skipped = True
        else:
            attempted = True
            ok, failed = _run_action_with_retry(run_id, page, video, "点赞视频", "like", lambda: _execute_click_action(run_id, page, video, "like", '[data-e2e="video-player-digg"]', "点赞视频"))
            failed_twice = failed_twice or failed
            if ok:
                done.append("点赞视频")
                remaining = None if remaining is None else remaining - 1
            elif failed and not done:
                return done, comment_text, image_path, True
    if remaining is not None and remaining <= 0:
        return done, comment_text, image_path, False
    if "collect" in plan["actions"]:
        if _skip_action_by_probability(run_id, video, "收藏视频", "collect", action_probability):
            probability_skipped = True
        else:
            attempted = True
            ok, failed = _run_action_with_retry(run_id, page, video, "收藏视频", "collect", lambda: _execute_click_action(run_id, page, video, "collect", '[data-e2e="video-player-collect"]', "收藏视频"))
            failed_twice = failed_twice or failed
            if ok:
                done.append("收藏视频")
                remaining = None if remaining is None else remaining - 1
            elif failed and not done:
                return done, comment_text, image_path, True
    if remaining is not None and remaining <= 0:
        return done, comment_text, image_path, False
    if "follow" in plan["actions"]:
        if _skip_action_by_probability(run_id, video, "关注作者", "follow", action_probability):
            probability_skipped = True
        else:
            attempted = True
            ok, failed = _run_action_with_retry(run_id, page, video, "关注作者", "follow", lambda: _execute_follow(run_id, page, video))
            failed_twice = failed_twice or failed
            if ok:
                done.append("关注作者")
                remaining = None if remaining is None else remaining - 1
            elif failed and not done:
                return done, comment_text, image_path, True
    if remaining is not None and remaining <= 0:
        return done, comment_text, image_path, False
    if "comment_text" in plan["actions"] or "comment_image" in plan["actions"]:
        if _skip_action_by_probability(run_id, video, "评论", "comment", action_probability):
            probability_skipped = True
        else:
            attempted = True
            comment_text, image_path = _pick_comment_materials(plan["actions"])
            ok, failed = _run_action_with_retry(run_id, page, video, "评论", "comment", lambda: _execute_comment(run_id, page, video, comment_text, image_path))
            failed_twice = failed_twice or failed
            if ok:
                done.append("评论")
                remaining = None if remaining is None else remaining - 1
            elif failed and not done:
                return done, comment_text, image_path, True
    if not done and probability_skipped and not attempted:
        return [], comment_text, image_path, False
    return done, comment_text, image_path, bool(failed_twice and not done)


def _pick_comment_materials(actions: list[str]) -> tuple[str, str]:
    has_text = "comment_text" in actions
    has_image = "comment_image" in actions
    if has_text and has_image:
        # 同时启用时随机评论形态，避免每次都文案和图片一起发。
        mode = random.choice(("text", "image", "both"))
        return (_pick_text() if mode in {"text", "both"} else "", _pick_image() if mode in {"image", "both"} else "")
    return (_pick_text() if has_text else "", _pick_image() if has_image else "")


def _skip_action_by_probability(run_id: str, video: dict[str, Any], label: str, phase: str, probability: int) -> bool:
    chance = max(0, min(100, int(probability)))
    if chance >= 100 or random.randint(1, 100) <= chance:
        return False
    return True


def _run_action_with_retry(run_id: str, page: Any, video: dict[str, Any], label: str, phase: str, action: Any) -> tuple[bool, bool]:
    for attempt in range(2):
        try:
            return bool(action()), False
        except Exception as exc:
            if not _is_recoverable_playwright_error(exc):
                raise
            if attempt == 0:
                _append_log(run_id, "warning", phase, f"{label}操作超时，正在重试一次。", "页面响应慢或当前视频不支持互动", "系统会再试一次；如果仍失败，会自动下滑跳过。", {"video_id": video.get("video_id"), "error": str(exc)})
                page.wait_for_timeout(250)
                continue
            _append_log(run_id, "warning", "advance", f"{label}连续 2 次失败，当前视频已跳过。", "可能是广告、页面加载异常或当前视频不支持互动", "无需手动处理，系统会继续下滑处理后续视频。", {"video_id": video.get("video_id"), "error": str(exc)})
            return False, True
    return False, True


def _daily_action_count(platform: str = "dy") -> int:
    with database.connect() as conn:
        rows = conn.execute(
            """
            SELECT actions FROM traffic_records
            WHERE platform = ?
              AND status = 'done'
              AND created_at >= date('now', 'localtime')
            """,
            (platform,),
        ).fetchall()
    total = 0
    for row in rows:
        try:
            actions = json.loads(row["actions"])
        except Exception:
            actions = []
        total += _real_action_count(actions)
    return total


def _real_action_count(actions: list[str]) -> int:
    return len([item for item in actions if item not in ("仅浏览", "跳过")])


def _execute_click_action(run_id: str, page: Any, video: dict[str, Any], action: str, selector: str, label: str) -> bool:
    if _dedup_exists(video, action, ""):
        _append_log(run_id, "warning", action, f"这个视频已经执行过{label}，本次跳过。", "防重复命中", "系统已自动跳过，不需要处理。", {"video_id": video["video_id"]})
        return False
    payload = _click_and_confirm_action_response(page, video, action, [selector], allow_like_shortcut=action == "like")
    if payload is None:
        _append_log(run_id, "warning", action, f"没有收到抖音服务端确认，已跳过{label}记录。", "前端按钮变化不能证明账号已真实落账", "系统不会把未确认动作写为成功；如果频繁出现，请降低频率或检查账号风控。", {"selector": selector, "video_id": video["video_id"]})
        return False
    _append_log(run_id, "success", action, f"{label}已执行。", "已收到抖音服务端成功响应", "可以在操作记录中查看本视频结果。", {"video_id": video["video_id"], "response": _compact_payload(payload)})
    _insert_dedup(video, action, "", "done")
    return True


def _execute_follow(run_id: str, page: Any, video: dict[str, Any]) -> bool:
    if _dedup_exists(video, "follow", ""):
        _append_log(run_id, "warning", "follow", "这个作者已经关注过或处理过，本次跳过。", "防重复命中", "系统已自动跳过，不需要处理。", {"video_id": video["video_id"]})
        return False
    payload = _click_and_confirm_action_response(page, video, "follow", _follow_selectors())
    if payload is None:
        _append_log(run_id, "warning", "follow", "没有收到抖音服务端关注确认，已跳过关注。", "当前视频可能是广告、直播、已关注作者、账号受限或页面没有关注按钮", "系统不会把未确认关注写为成功；如果计划包含其它动作，会继续处理其它动作。", {"video_id": video["video_id"]})
        return False
    _append_log(run_id, "success", "follow", "已关注作者。", "已收到抖音服务端成功响应", "可以在操作记录中查看本次关注。", {"video_id": video["video_id"], "response": _compact_payload(payload)})
    _insert_dedup(video, "follow", "", "done")
    return True


def _execute_comment(run_id: str, page: Any, video: dict[str, Any], text: str, image_path: str) -> bool:
    content_hash = _hash_text(f"{text}|{image_path}")
    if _dedup_exists(video, "comment", content_hash):
        _append_log(run_id, "warning", "comment", "这个视频已经发送过相同评论，已跳过。", "防重复命中", "系统已自动跳过，不需要处理。", {"video_id": video["video_id"]})
        return False
    try:
        _open_comment_panel(page, video.get("video_id", ""))
        composer = _activate_comment_composer(page)
        if composer is None:
            _append_log(run_id, "warning", "comment", "没有找到评论输入框，已跳过评论。", "评论区没有打开或当前视频不支持评论", "系统会继续浏览后续视频。", {"video_id": video["video_id"]})
            return False
        if text:
            _fill_comment_text(page, composer, text)
            actual_text = _current_comment_text(page, composer)
            if not _comment_text_matches(actual_text, text):
                _force_set_comment_text(page, text)
                actual_text = _current_comment_text(page, composer)
            if not _comment_text_matches(actual_text, text):
                _append_log(run_id, "warning", "comment", "评论文案没有完整写入，已取消发送。", "输入框内容和文案库内容不一致", "系统不会发送半截文案；请稍后重试或降低执行频率。", {"expected": text, "actual": actual_text})
                return False
        if image_path:
            if Path(image_path).exists():
                uploader = page.locator('.comment-input-inner-container input[type="file"]').first
                uploader.set_input_files(image_path, timeout=2_000)
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline and not _comment_image_ready(page):
                    page.wait_for_timeout(100)
                if not _comment_image_ready(page):
                    _append_log(run_id, "warning", "comment", "评论图片没有完成预览，已取消发送。", "图片上传后没有出现预览", "请到引流设置检查图片格式或换一张图片。", {"image_path": image_path})
                    return False
            else:
                _append_log(run_id, "warning", "comment", "评论图片文件不存在，已取消本次评论。", "图片路径无效", "请到引流设置检查图片路径。", {"image_path": image_path})
                return False
        payload = _publish_comment_and_confirm(page, text)
        if payload is None:
            _append_log(run_id, "warning", "comment", "评论没有确认发送成功，已跳过记录。", "没有捕获到评论发布成功响应", "系统不会把未确认评论写为成功；如果频繁出现，请检查账号限制或安全验证。", {"video_id": video["video_id"], "text": text, "image_path": image_path})
            return False
        _append_log(run_id, "success", "comment", "评论已发送。", "评论发布接口已返回成功", "可以在操作记录中查看实际文案和图片。", {"video_id": video["video_id"], "text": text, "image_path": image_path, "response": _compact_payload(payload)})
        _insert_dedup(video, "comment", content_hash, "done")
        return True
    finally:
        _close_comment_panel(page)


def _click_and_confirm_action_response(page: Any, video: dict[str, Any], action: str, selectors: list[str], allow_like_shortcut: bool = False) -> dict[str, Any] | None:
    video_id = str(video.get("video_id") or "")
    try:
        with page.expect_response(lambda response: _is_action_response(response, action, video), timeout=ACTION_RESPONSE_TIMEOUT_MS) as response_info:
            clicked = _click_current_control(page, video_id, action, selectors, require_confirm=False)
            if not clicked and allow_like_shortcut:
                page.keyboard.press("z")
                clicked = True
            if not clicked:
                raise RuntimeError("action control unavailable")
        payload = _response_json(response_info.value)
        payload["_confirm_url"] = _response_path(response_info.value)
        return payload if _payload_status_ok(payload) else None
    except Exception:
        return None


def _is_action_response(response: Any, action: str, video: dict[str, Any] | None = None) -> bool:
    url = str(getattr(response, "url", "")).lower()
    raw = f"{url}\n{_request_post_data(response)}".lower()
    target = str((video or {}).get("author_id" if action == "follow" else "video_id") or "")
    if action != "follow" and target and target not in raw:
        return False
    if action == "like":
        return "digg" in url and ("aweme" in url or "commit" in url) and _is_positive_action_request(response)
    if action == "collect":
        return ("collect" in url or "favorite" in url) and ("aweme" in url or "commit" in url) and _is_positive_action_request(response)
    if action == "follow":
        return ("follow" in url or "relation" in url) and ("user" in url or "commit" in url) and _is_positive_action_request(response)
    return False


def _is_positive_action_request(response: Any) -> bool:
    params = _response_params(response)
    negative_keys = {"type", "action_type", "digg_type", "collect_type", "follow_type", "action", "is_cancel", "to_status"}
    for key in negative_keys:
        for value in params.get(key, []):
            normalized = str(value).strip().lower()
            if normalized in {"0", "false", "cancel", "unfollow", "uncollect", "undigg"}:
                return False
    positive_keys = {"type", "action_type", "digg_type", "collect_type", "follow_type", "action", "to_status"}
    return any(str(value).strip().lower() in {"1", "true", "follow", "collect", "digg"} for key in positive_keys for value in params.get(key, []))


def _follow_selectors() -> list[str]:
    return [
        '[data-e2e="feed-follow"]',
        '[data-e2e="feed-follow-icon"] span',
        '[data-e2e="feed-follow-icon"] svg',
        '[data-e2e="feed-follow-icon"]',
    ]


def _response_params(response: Any) -> dict[str, list[str]]:
    parsed = urlparse(str(getattr(response, "url", "")))
    params = {key: list(value) for key, value in parse_qs(parsed.query).items()}
    post_data = _request_post_data(response)
    if post_data:
        for key, value in parse_qs(post_data).items():
            params.setdefault(key, []).extend(value)
    return params


def _request_post_data(response: Any) -> str:
    try:
        return str(getattr(response.request, "post_data", "") or "")
    except Exception:
        return ""


def _response_path(response: Any) -> str:
    try:
        url = str(getattr(response, "url", ""))
        parsed = urlparse(url)
        return f"{parsed.path}?{parsed.query}"[:500]
    except Exception:
        return ""


def _response_json(response: Any) -> dict[str, Any]:
    try:
        payload = response.json()
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _payload_status_ok(payload: dict[str, Any]) -> bool:
    status = payload.get("status_code", payload.get("status"))
    return status in (0, "0") or payload.get("result") in (1, "1", True)


def _compact_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: payload.get(key) for key in ("status_code", "status_msg", "message", "result", "log_pb") if key in payload}


def _comment_composer(page: Any) -> Any:
    return page.locator(COMMENT_EDITOR_VISIBLE_SELECTOR).first


def _activate_comment_composer(page: Any) -> Any | None:
    composer = _comment_composer(page)
    if composer.is_visible():
        return composer
    container = page.locator(COMMENT_CONTAINER_SELECTOR).first
    try:
        container.wait_for(state="visible", timeout=1_200)
        container.click(timeout=1_200)
        composer = _comment_composer(page)
        composer.wait_for(state="visible", timeout=1_200)
        return composer
    except Exception:
        return None


def _fill_comment_text(page: Any, composer: Any, text: str) -> None:
    composer.click(timeout=5000)
    # ponytail: 中文评论用整条插入，避免逐字键入时焦点丢失只留下末尾字符。
    page.keyboard.press("Control+A")
    page.keyboard.press("Backspace")
    page.keyboard.insert_text(text)


def _force_set_comment_text(page: Any, text: str) -> None:
    try:
        page.evaluate(
            """
            text => {
              const active = document.activeElement;
              const editor = active?.matches?.('textarea, [contenteditable="true"]')
                ? active
                : active?.closest?.('.comment-input-inner-container [contenteditable="true"]')
                  || document.querySelector('#videoSideCard .comment-input-inner-container textarea, #videoSideCard .comment-input-inner-container [contenteditable="true"], #videoSideBar .comment-input-inner-container textarea, #videoSideBar .comment-input-inner-container [contenteditable="true"]');
              if (!editor) return false;
              editor.focus();
              if ('value' in editor) {
                editor.value = text;
                editor.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText', data: text }));
                editor.dispatchEvent(new Event('change', { bubbles: true }));
                return true;
              }
              document.execCommand('selectAll', false);
              document.execCommand('insertText', false, text);
              editor.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText', data: text }));
              return true;
            }
            """,
            text,
        )
    except Exception:
        return None


def _click_current_control(page: Any, video_id: str, action: str, extra_selectors: list[str] | None = None, require_confirm: bool = True) -> bool:
    before = _current_control_snapshot(page, video_id, action) if require_confirm and action != "comment" else {}
    handle = page.evaluate_handle(
        """
        ({ videoId, action, extraSelectors }) => {
          const actionSelectors = {
            like: ['[data-e2e="feed-like-icon"]', '[data-e2e="video-player-digg"]'],
            collect: ['[data-e2e="video-player-collect"]'],
            follow: ['[data-e2e="feed-follow"]', '[data-e2e="feed-follow-icon"] span', '[data-e2e="feed-follow-icon"] svg', '[data-e2e="feed-follow-icon"]', 'button'],
            comment: ['[data-e2e="feed-comment-icon"]'],
          };
          const selectors = [...(actionSelectors[action] || []), ...(extraSelectors || [])];
          const active = document.querySelector('[data-e2e="feed-active-video"]');
          const detail = videoId ? document.querySelector(`[class*="video_${videoId}"]`) : null;
          const scopedRoots = [active, detail].filter(Boolean);
          const roots = scopedRoots.length ? scopedRoots : [document];
          const visible = el => {
            const rect = el.getBoundingClientRect();
            const style = window.getComputedStyle(el);
            return rect.width > 8 && rect.height > 8 && rect.bottom > 0 && rect.top < innerHeight
              && rect.right > 0 && rect.left < innerWidth && style.display !== 'none'
              && style.visibility !== 'hidden' && Number(style.opacity || 1) > 0;
          };
          const clickable = el => {
            const chain = [el, el.closest('button'), el.closest('[role="button"]'), el.parentElement, el.parentElement?.parentElement].filter(Boolean);
            return chain.find(visible);
          };
          for (const root of roots) {
            for (const selector of selectors) {
              for (const el of Array.from(root.querySelectorAll(selector))) {
                if (action === 'follow') {
                  const isIcon = Boolean(el.closest('[data-e2e="feed-follow-icon"]') || el.matches('[data-e2e="feed-follow-icon"]'));
                  if (!isIcon && !/关注/.test(el.innerText || el.textContent || '')) continue;
                }
                const target = clickable(el);
                if (target) {
                  return target;
                }
              }
            }
          }
          return null;
        }
        """,
        {"videoId": str(video_id or ""), "action": action, "extraSelectors": extra_selectors or []},
    )
    target = handle.as_element()
    if target is None:
        handle.dispose()
        return False
    try:
        # ElementHandle.click 会重新等待元素稳定，避免页面位移后点到旧坐标。
        target.click(timeout=2_000)
    finally:
        handle.dispose()
    if action == "comment" or not require_confirm:
        return True
    deadline = time.monotonic() + VIDEO_CHANGE_TIMEOUT_MS / 1000
    while time.monotonic() < deadline:
        if _control_changed(before, _current_control_snapshot(page, video_id, action)):
            return True
        page.wait_for_timeout(100)
    return False


def _current_control_snapshot(page: Any, video_id: str, action: str) -> dict[str, str]:
    try:
        return dict(page.evaluate(
            """
            ({ videoId, action }) => {
              const selectors = {
                like: ['[data-e2e="feed-like-icon"]', '[data-e2e="video-player-digg"]'],
                collect: ['[data-e2e="video-player-collect"]'],
                follow: ['[data-e2e="feed-follow"]', '[data-e2e="feed-follow-icon"] span', '[data-e2e="feed-follow-icon"] svg', '[data-e2e="feed-follow-icon"]', 'button'],
                comment: ['[data-e2e="feed-comment-icon"]'],
              }[action] || [];
              const active = document.querySelector('[data-e2e="feed-active-video"]');
              const detail = videoId ? document.querySelector(`[class*="video_${videoId}"]`) : null;
              const scopedRoots = [active, detail].filter(Boolean);
              const roots = scopedRoots.length ? scopedRoots : [document];
              for (const root of roots) {
                for (const selector of selectors) {
                  for (const el of Array.from(root.querySelectorAll(selector))) {
                    if (action === 'follow') {
                      const isIcon = Boolean(el.closest('[data-e2e="feed-follow-icon"]') || el.matches('[data-e2e="feed-follow-icon"]'));
                      if (!isIcon && !/关注/.test(el.innerText || el.textContent || '')) continue;
                    }
                    const target = el.closest('button') || el.closest('[role="button"]') || el.parentElement || el;
                    return {
                      text: (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim(),
                      cls: String(target.className || ''),
                      aria: target.getAttribute('aria-pressed') || target.getAttribute('aria-label') || '',
                    };
                  }
                }
              }
              return { text: '', cls: '', aria: '' };
            }
            """,
            {"videoId": str(video_id or ""), "action": action},
        ))
    except Exception:
        return {"text": "", "cls": "", "aria": ""}


def _control_changed(before: dict[str, str], after: dict[str, str]) -> bool:
    return bool(after.get("text") or after.get("cls") or after.get("aria")) and before != after


def _open_comment_panel(page: Any, video_id: str) -> None:
    if _is_comment_panel_open(page):
        return
    if not _click_current_control(page, video_id, "comment", ['[data-e2e="feed-comment-icon"]']):
        page.keyboard.press("x")


def _close_comment_panel(page: Any) -> None:
    if not _is_comment_panel_open(page):
        return
    # ponytail: 先移出评论输入框焦点，否则 x/方向键可能被输入框吞掉。
    try:
        page.evaluate("() => document.activeElement?.blur?.()")
    except Exception:
        pass
    page.keyboard.press("x")
    deadline = time.monotonic() + 0.5
    while time.monotonic() < deadline and _is_comment_panel_open(page):
        page.wait_for_timeout(50)
    if _is_comment_panel_open(page):
        _click_current_control(page, "", "comment", ['[data-e2e="feed-comment-icon"]'])


def _is_comment_panel_open(page: Any) -> bool:
    try:
        return bool(page.evaluate(
            """
            () => {
              return Array.from(document.querySelectorAll('#videoSideCard .comment-input-inner-container, #videoSideBar .comment-input-inner-container'))
                .some(el => {
                  const rect = el.getBoundingClientRect();
                  const style = window.getComputedStyle(el);
                  return rect.width > 20 && rect.height > 12 && rect.top < innerHeight
                    && style.display !== 'none' && style.visibility !== 'hidden';
                });
            }
            """
        ))
    except Exception:
        return False


def _current_comment_text(page: Any, composer: Any) -> str:
    try:
        value = composer.evaluate(
            """
            el => {
              const visible = node => {
                if (!node) return false;
                const rect = node.getBoundingClientRect();
                const style = window.getComputedStyle(node);
                return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
              };
              const editorOf = node => {
                if (!node) return null;
                if (node.matches?.('textarea, [contenteditable="true"]')) return node;
                return node.closest?.('[contenteditable="true"]') || null;
              };
              const pick = node => (node && ('value' in node ? node.value : (node.innerText || node.textContent || ''))) || '';
              const activeEditor = editorOf(document.activeElement);
              const scoped = Array.from(document.querySelectorAll('#videoSideCard .comment-input-inner-container textarea, #videoSideCard .comment-input-inner-container [contenteditable="true"], #videoSideBar .comment-input-inner-container textarea, #videoSideBar .comment-input-inner-container [contenteditable="true"]')).find(visible);
              return pick(activeEditor) || pick(editorOf(el)) || pick(scoped);
            }
            """
        )
    except Exception:
        try:
            value = page.evaluate("() => document.activeElement?.value || document.activeElement?.innerText || ''")
        except Exception:
            value = ""
    return _normalize_comment_text(value)


def _comment_text_matches(actual: str, expected: str) -> bool:
    return _normalize_comment_text(actual) == _normalize_comment_text(expected)


def _normalize_comment_text(value: Any) -> str:
    text = str(value or "")
    text = re.sub(r"[\u200b\u200c\u200d\ufeff]", "", text)
    text = text.replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip()


def _comment_image_ready(page: Any) -> bool:
    try:
        return bool(page.evaluate(
            """
            () => Array.from(document.querySelectorAll('.comment-input-inner-container img, .comment-input-inner-container [style*="background-image"]'))
              .some(el => {
                const rect = el.getBoundingClientRect();
                return rect.width > 16 && rect.height > 16 && rect.top < innerHeight;
              })
            """
        ))
    except Exception:
        return False


def _publish_comment_and_confirm(page: Any, text: str = "") -> dict[str, Any] | None:
    send_point = _comment_send_button_point(page)
    submit = (lambda: _click_comment_send_button(page, send_point)) if send_point else (lambda: _press_comment_submit_key(page, "Control+Enter"))
    # 每条评论只提交一次，避免响应较慢时再按回车造成重复评论。
    payload = _comment_publish_response(page, submit, 2_500)
    if payload is None:
        return None
    if not _payload_status_ok(payload) or not _comment_publish_has_result(payload, text):
        return None
    return payload


def _comment_publish_response(page: Any, action: Any, timeout: int) -> dict[str, Any] | None:
    try:
        with page.expect_response(lambda response: "aweme/v1/web/comment/publish" in response.url, timeout=timeout) as response_info:
            if not action():
                raise RuntimeError("comment submit control unavailable")
        payload = _response_json(response_info.value)
        return payload
    except Exception:
        return None


def _press_comment_submit_key(page: Any, key: str) -> bool:
    page.keyboard.press(key)
    return True


def _comment_send_button_point(page: Any) -> dict[str, float] | None:
    point = page.evaluate(
        """
        () => {
          const visible = el => {
            if (!el) return false;
            const rect = el.getBoundingClientRect();
            const style = window.getComputedStyle(el);
            return rect.width > 8 && rect.height > 8 && rect.top < innerHeight
              && style.display !== 'none' && style.visibility !== 'hidden';
          };
          const disabled = el => Boolean(el?.disabled) || el?.getAttribute?.('aria-disabled') === 'true'
            || /disabled|disable/.test(String(el?.className || '').toLowerCase());
          const textOf = el => (el?.innerText || el?.textContent || el?.getAttribute?.('aria-label') || '').replace(/\\s+/g, '');
          const clickableOf = el => el?.closest?.('button, [role="button"]') || el;
          const containers = Array.from(document.querySelectorAll('.comment-input-inner-container')).filter(visible);
          for (const container of containers) {
            const iconSend = Array.from(container.querySelectorAll('.commentInput-right-ct > div > span'))
              .find(el => el.querySelector('path[fill="#FE2C55"], path[fill="#fe2c55"]'));
            const candidates = [iconSend, ...Array.from(container.querySelectorAll('button, [role="button"], span'))].filter(Boolean);
            for (const el of candidates) {
              const text = textOf(el);
              if (el !== iconSend && !/^(发布|发送|发布评论|发送评论)$/.test(text)) continue;
              const target = clickableOf(el);
              if (!visible(target) || disabled(target)) continue;
              target.scrollIntoView({block: 'center', inline: 'center'});
              const rect = target.getBoundingClientRect();
              return {x: rect.left + rect.width / 2, y: rect.top + rect.height / 2};
            }
          }
          return null;
        }
        """
    )
    return dict(point) if point else None


def _click_comment_send_button(page: Any, point: dict[str, float]) -> bool:
    page.mouse.click(point["x"], point["y"])
    return True


def _comment_publish_has_result(payload: dict[str, Any], text: str) -> bool:
    if text and _payload_contains_text(payload, text):
        return True
    return _payload_has_any_key(payload, {"cid", "comment_id", "comment_id_str", "reply_id"})


def _payload_contains_text(value: Any, text: str) -> bool:
    if isinstance(value, dict):
        return any(_payload_contains_text(item, text) for item in value.values())
    if isinstance(value, list):
        return any(_payload_contains_text(item, text) for item in value)
    return text in str(value or "")


def _payload_has_any_key(value: Any, keys: set[str]) -> bool:
    if isinstance(value, dict):
        return any(key in value and value.get(key) for key in keys) or any(_payload_has_any_key(item, keys) for item in value.values())
    if isinstance(value, list):
        return any(_payload_has_any_key(item, keys) for item in value)
    return False


def _advance_video(page: Any, previous_video_id: str, video_cache: dict[str, Any] | None = None) -> bool:
    _close_comment_panel(page)
    mode = _detect_douyin_page_mode(page)
    actions = ("detail_next", "arrow_down", "wheel", "page_down", "visible_link") if mode in {"video_detail", "jingxuan_modal_feed"} else ("arrow_down", "wheel", "page_down")
    for action in actions:
        if action == "arrow_down":
            page.keyboard.press("ArrowDown")
        elif action == "wheel":
            page.mouse.wheel(0, 1600)
        elif action == "page_down":
            page.keyboard.press("PageDown")
        elif action == "detail_next":
            point = page.evaluate(
                """
                () => {
                  for (const button of document.querySelectorAll('[data-e2e="video-switch-next-arrow"]')) {
                    const rect = button.getBoundingClientRect();
                    if (rect.width > 0 && rect.height > 0 && rect.top >= 0 && rect.bottom <= innerHeight) {
                      return {x: rect.left + rect.width / 2, y: rect.top + rect.height / 2};
                    }
                  }
                  return null;
                }
                """
            )
            if not point:
                continue
            page.mouse.click(point["x"], point["y"])
        else:
            links = [item for item in _visible_video_links(page) if previous_video_id not in str(item.get("href", ""))]
            if links:
                page.goto(random.choice(links[:6])["href"], wait_until="domcontentloaded", timeout=60_000)
        if _wait_for_video_change(page, previous_video_id, _read_active_video, video_cache):
            return True
    return False


def _wait_for_video_change(page: Any, previous_video_id: str, reader: Any, video_cache: dict[str, Any] | None) -> bool:
    deadline = time.monotonic() + VIDEO_CHANGE_TIMEOUT_MS / 1000
    while time.monotonic() < deadline:
        next_id = str(reader(page, video_cache).get("video_id") or "")
        if next_id and next_id != previous_video_id:
            return True
        page.wait_for_timeout(100)
    return False


def _record_video(
    run_id: str,
    plan: dict[str, Any],
    video: dict[str, Any],
    actions: list[str],
    comment_text: str,
    image_path: str,
    status: str,
    reason: str = "",
) -> None:
    # 只有计入批次的视频才落表；预热和无操作视频不走这里。
    with database.connect() as conn:
        conn.execute(
            """
            INSERT INTO traffic_run_items(
                run_id, video_id, video_url, author_id, author_name,
                video_desc, like_count, comment_count, status, actions_done, skip_reason
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                video["video_id"],
                video["video_url"],
                video.get("author_id", ""),
                video.get("author_name", ""),
                video.get("video_desc", ""),
                video.get("like_count"),
                video.get("comment_count"),
                status,
                json.dumps(actions, ensure_ascii=False),
                reason,
            ),
        )
        conn.execute(
            """
            INSERT INTO traffic_records(
                run_id, plan_id, platform, video_id, video_url, video_desc,
                author_id, author_name, like_count, comment_count, actions,
                comment_text, comment_image_path, status, reason
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                plan["id"],
                plan["platform"],
                video["video_id"],
                video["video_url"],
                video.get("video_desc", ""),
                video.get("author_id", ""),
                video.get("author_name", ""),
                video.get("like_count"),
                video.get("comment_count"),
                json.dumps(actions, ensure_ascii=False),
                comment_text,
                image_path,
                status,
                reason,
            ),
        ).lastrowid
        conn.execute(
            """
            UPDATE traffic_runs
            SET total_videos = total_videos + 1,
                browsed_count = browsed_count + 1,
                action_success_count = action_success_count + ?,
                skipped_count = skipped_count + ?,
                updated_at = datetime('now', 'localtime')
            WHERE id = ?
            """,
            (0 if actions == ["仅浏览"] or status == "skipped" else len(actions), 1 if status == "skipped" else 0, run_id),
        )


def _validate_run_plan(plan: dict[str, Any]) -> None:
    if plan["platform"] == "xhs":
        raise ValueError("小红书引流正在开发，当前只能启动抖音或快手计划")
    if plan["platform"] == "ks" and plan["source_mode"] != "random_feed":
        raise ValueError("快手引流当前只支持随机推荐流，暂不支持项目库视频或关键词来源")
    if plan["platform"] == "ks" and "comment_image" in plan["actions"]:
        raise ValueError("快手 Web 端暂不支持评论图片，请取消“评论图片”后再启动")
    settings = get_settings()
    if "comment_text" in plan["actions"] and not [item for item in settings["texts"] if item.get("enabled")]:
        raise ValueError("已选择评论文案，但引流设置里还没有可用文案")
    if "comment_image" in plan["actions"] and not [item for item in settings["images"] if item.get("enabled")]:
        raise ValueError("已选择评论图片，但引流设置里还没有可用图片")


def _normalize_plan(payload: TrafficPlanCreate, plan_id: str = "") -> dict[str, Any]:
    name = payload.name.strip()
    if not name or name == "随机推荐引流":
        name = _default_plan_name(payload.source_mode, plan_id)
    source_value = payload.source_value.strip()
    if payload.source_mode in KEYWORD_SOURCE_MODES and not source_value:
        raise ValueError("关键词模式必须填写一个搜索关键词")
    if payload.source_mode == "competitor_videos":
        source_value = "\n".join(_split_source_values(source_value))
    return {
        "name": name,
        "platform": payload.platform,
        "source_mode": payload.source_mode,
        "source_value": source_value,
        "action_like": payload.action_like,
        "action_collect": payload.action_collect,
        "action_follow": payload.action_follow,
        "action_comment_text": payload.action_comment_text,
        "action_comment_image": payload.action_comment_image,
        "round_video_limit": payload.round_video_limit,
        "enabled": payload.enabled,
    }


def _default_plan_name(source_mode: str, plan_id: str) -> str:
    source = SOURCE_MODE_LABELS.get(source_mode, source_mode or "引流计划")
    return f"{source}-{plan_id[:8]}" if plan_id else source


def _format_plan(row: Any) -> dict[str, Any]:
    data = database.row_to_dict(row) or {}
    data["action_like"] = bool(data.get("action_like"))
    data["action_collect"] = bool(data.get("action_collect"))
    data["action_follow"] = bool(data.get("action_follow"))
    data["action_comment_text"] = bool(data.get("action_comment_text"))
    data["action_comment_image"] = bool(data.get("action_comment_image"))
    data["round_video_limit"] = int(data.get("round_video_limit") or 5)
    data["enabled"] = bool(data.get("enabled"))
    data["archived"] = bool(data.get("archived"))
    data["automation_managed"] = bool(data.get("automation_managed"))
    data["actions"] = _plan_actions(data)
    data["action_label"] = "、".join(_action_label(action) for action in data["actions"]) or "仅浏览"
    return data


def _format_run(row: Any) -> dict[str, Any]:
    data = database.row_to_dict(row) or {}
    if "archived" in data:
        data["archived"] = bool(data.get("archived"))
    return data


def _format_record(row: Any) -> dict[str, Any]:
    data = database.row_to_dict(row) or {}
    try:
        data["actions"] = json.loads(data.get("actions") or "[]")
    except json.JSONDecodeError:
        data["actions"] = []
    # 评论图片来自本地素材库时，前端可直接用预览地址展示缩略图。
    preview_url = _image_preview_url(Path(str(data.get("comment_image_path") or "")))
    if preview_url:
        data["comment_image_preview_url"] = preview_url
    return data


def _format_material_image(row: Any) -> dict[str, Any]:
    data = database.row_to_dict(row) or {}
    preview_url = _image_preview_url(Path(str(data.get("path") or "")))
    if preview_url:
        data["preview_url"] = preview_url
    return data


def _image_preview_url(path: Path) -> str:
    try:
        if path.resolve().parent != TRAFFIC_IMAGE_DIR.resolve():
            return ""
    except OSError:
        return ""
    return f"/api/traffic/material-images/{path.name}"


def _env_item(ok: bool, message: str) -> dict[str, Any]:
    return {"ok": ok, "message": message}


def _check_cloakbrowser_binary() -> dict[str, Any]:
    if getattr(sys, "frozen", False):
        bundled_binary = Path(os.environ.get("CLOAKBROWSER_BINARY_PATH", ""))
        if bundled_binary.is_file():
            return _env_item(True, str(bundled_binary))
        return _env_item(False, "打包内置的 CloakBrowser 浏览器不存在")
    try:
        # Windows 下 chrome --version 偶发卡住，这里只检查 CloakBrowser 内核是否已安装。
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "cloakbrowser",
                "doctor",
                "--quick",
                "--json",
            ],
            cwd=str(database.BACKEND_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        return _env_item(False, "CloakBrowser 内核检查超时")
    text = _tail((result.stdout or "") + "\n" + (result.stderr or ""))
    if result.returncode != 0:
        return _env_item(False, text or "CloakBrowser 内核检查失败")
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return _env_item(False, text or "CloakBrowser 内核检查结果无法解析")
    binary = payload.get("binary") or {}
    if not binary.get("installed"):
        return _env_item(False, "CloakBrowser 浏览器内核未安装")
    return _env_item(True, str(binary.get("path") or "CloakBrowser 浏览器内核可用"))


def _run_install_step(command: list[str]) -> dict[str, Any]:
    try:
        result = subprocess.run(
            command,
            cwd=str(database.BACKEND_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=INSTALL_TIMEOUT_SECONDS,
        )
        return {"command": " ".join(command), "ok": result.returncode == 0, "output": _tail(result.stdout)}
    except subprocess.TimeoutExpired as exc:
        return {"command": " ".join(command), "ok": False, "output": _tail(exc.stdout or "安装超时")}


def _tail(text: str, limit: int = 2000) -> str:
    return str(text or "").strip()[-limit:]


def _plan_actions(plan: dict[str, Any]) -> list[str]:
    actions = []
    if plan.get("action_like"):
        actions.append("like")
    if plan.get("action_collect"):
        actions.append("collect")
    if plan.get("action_follow"):
        actions.append("follow")
    if plan.get("action_comment_text"):
        actions.append("comment_text")
    if plan.get("action_comment_image"):
        actions.append("comment_image")
    return actions


def _action_label(action: str) -> str:
    return {
        "like": "点赞视频",
        "collect": "收藏视频",
        "follow": "关注作者",
        "comment_text": "评论文案",
        "comment_image": "评论图片",
    }.get(action, action)


def _mark_run_running(run_id: str) -> None:
    with database.connect() as conn:
        conn.execute(
            """
            UPDATE traffic_runs
            SET status = 'running',
                started_at = COALESCE(started_at, datetime('now', 'localtime')),
                updated_at = datetime('now', 'localtime')
            WHERE id = ?
            """,
            (run_id,),
        )
        _insert_log(conn, run_id, "info", "probe", "任务已启动，正在检查平台页面。", "开始执行批次", "请不要关闭浏览器窗口或本地服务。", {})


def _finish_run(
    run_id: str,
    status: str,
    message: str,
    reason: str,
    suggestion: str,
    phase: str = "stop",
    details: dict[str, Any] | None = None,
) -> None:
    level = "success" if status == "completed" else "error" if status == "failed" else "warning"
    with database.connect() as conn:
        cursor = conn.execute(
            """
            UPDATE traffic_runs
            SET status = ?, stop_reason = ?, stop_suggestion = ?,
                finished_at = datetime('now', 'localtime'),
                updated_at = datetime('now', 'localtime')
            WHERE id = ?
              AND NOT (status = 'stopped' AND stop_requested = 1)
            """,
            (status, reason, suggestion, run_id),
        )
        if cursor.rowcount:
            _insert_log(conn, run_id, level, phase, message, reason, suggestion, details or {})


def _append_log(run_id: str, level: str, phase: str, message: str, reason: str, suggestion: str, details: dict[str, Any]) -> None:
    with database.connect() as conn:
        _insert_log(conn, run_id, level, phase, message, reason, suggestion, details)


def _insert_log(conn: Any, run_id: str, level: str, phase: str, message: str, reason: str, suggestion: str, details: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO traffic_action_logs(run_id, level, phase, message, reason, suggestion, details)
        VALUES(?, ?, ?, ?, ?, ?, ?)
        """,
        (run_id, level, phase, message, reason, suggestion, json.dumps(details, ensure_ascii=False)),
    )


def _raise_if_stop_requested(run_id: str) -> None:
    with database.connect() as conn:
        row = conn.execute("SELECT stop_requested FROM traffic_runs WHERE id = ?", (run_id,)).fetchone()
    if row and int(row["stop_requested"] or 0):
        raise TrafficStop("任务已按你的要求停止。", "用户手动停止任务", "可以在操作记录查看已经处理的视频。", "stop", {})


def _traffic_run_stop_requested(run_id: str) -> bool:
    with database.connect() as conn:
        row = conn.execute("SELECT stop_requested FROM traffic_runs WHERE id = ?", (run_id,)).fetchone()
    return bool(row and int(row["stop_requested"] or 0))


def _pick_text() -> str:
    with database.connect() as conn:
        rows = conn.execute("SELECT * FROM traffic_material_texts WHERE enabled = 1").fetchall()
        if not rows:
            return ""
        row = random.choice(rows)
        conn.execute("UPDATE traffic_material_texts SET used_count = used_count + 1 WHERE id = ?", (row["id"],))
    return str(row["text"])


def _pick_image() -> str:
    with database.connect() as conn:
        rows = conn.execute("SELECT * FROM traffic_material_images WHERE enabled = 1").fetchall()
        if not rows:
            return ""
        row = random.choice(rows)
        conn.execute("UPDATE traffic_material_images SET used_count = used_count + 1 WHERE id = ?", (row["id"],))
    return str(row["path"])


def _dedup_exists(video: dict[str, Any], action: str, content_hash: str) -> bool:
    platform = str(video.get("platform") or "dy")
    video_id, author_id = _dedup_scope(video, action)
    with database.connect() as conn:
        row = conn.execute(
            """
            SELECT id FROM traffic_dedup_ledger
            WHERE platform = ? AND video_id = ? AND author_id = ?
              AND action_type = ? AND content_hash = ?
            """,
            (platform, video_id, author_id, action, content_hash),
        ).fetchone()
    return row is not None


def _insert_dedup(video: dict[str, Any], action: str, content_hash: str, status: str) -> None:
    with database.connect() as conn:
        _insert_dedup_with_conn(conn, video, action, content_hash, status, None, None)


def _insert_dedup_with_conn(conn: Any, video: dict[str, Any], action: str, content_hash: str, status: str, run_id: str | None, record_id: int | None) -> None:
    platform = str(video.get("platform") or "dy")
    video_id, author_id = _dedup_scope(video, action)
    conn.execute(
        """
        INSERT OR IGNORE INTO traffic_dedup_ledger(platform, video_id, author_id, action_type, content_hash, run_id, record_id, status)
        VALUES(?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (platform, video_id, author_id, action, content_hash, run_id, record_id, status),
    )


def _dedup_scope(video: dict[str, Any], action: str) -> tuple[str, str]:
    video_id = str(video.get("video_id") or "")
    author_id = str(video.get("author_id") or video.get("author_name") or "")
    if action == "follow":
        return "", author_id or video_id
    if action in {"like", "collect", "comment"}:
        return video_id, ""
    return video_id, author_id


def _hash_text(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()


def _clean_material_rows(values: list[Any], value_key: str) -> list[tuple[str, bool]]:
    rows: list[tuple[str, bool]] = []
    for item in values:
        if isinstance(item, dict):
            value = str(item.get(value_key) or item.get("value") or "").strip()
            enabled = _material_enabled(item.get("enabled", True))
        else:
            value = str(item).strip()
            enabled = True
        if value:
            rows.append((value, enabled))
    return rows


def _material_enabled(value: Any) -> bool:
    return str(value).lower() not in {"0", "false", "no", "off"}


def _int_setting(settings: dict[str, Any], key: str, default: int, minimum: int, maximum: int) -> int:
    value = int(str(settings.get(key, default) or default))
    return max(minimum, min(maximum, value))
