from __future__ import annotations

import base64
import json
import os
import platform
import re
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from app import database
from app.schemas import TrafficAssetCreate, TrafficCampaignCreate
from app.services import crawler_adapter, license_service


TRAFFIC_SETTING_KEYS = (
    "traffic_per_run_limit",
    "traffic_daily_limit",
    "traffic_stay_seconds_min",
    "traffic_stay_seconds_max",
    "traffic_action_interval_seconds_min",
    "traffic_action_interval_seconds_max",
    "traffic_action_like",
    "traffic_action_follow",
    "traffic_action_comment",
    "traffic_comment_templates",
    "traffic_only_active_video",
    "traffic_active_comment_min",
    "traffic_video_block_keywords",
    "traffic_author_block_keywords",
    "traffic_rule_relation",
    "traffic_match_rules",
)
RULE_FIELDS = {"author", "title", "keyword"}
COMMENT_ACTION_TYPE = "comment"
RUNNING_TRAFFIC_PROCESSES: dict[str, subprocess.Popen[str]] = {}


def get_settings() -> dict[str, Any]:
    with database.connect() as conn:
        values = {key: database.get_setting(conn, key) for key in TRAFFIC_SETTING_KEYS}
    values["traffic_action_like"] = values.get("traffic_action_like", "true") == "true"
    values["traffic_action_follow"] = values.get("traffic_action_follow", "false") == "true"
    values["traffic_action_comment"] = values.get("traffic_action_comment", "true") == "true"
    values["traffic_only_active_video"] = values.get("traffic_only_active_video", "false") == "true"
    values["traffic_comment_templates"] = _json_list(values.get("traffic_comment_templates"), ["想了解一下，方便看下主页吗？"])
    values["traffic_video_block_keywords"] = _text_list(_json_list(values.get("traffic_video_block_keywords"), []))
    values["traffic_author_block_keywords"] = _text_list(_json_list(values.get("traffic_author_block_keywords"), []))
    values["traffic_rule_relation"] = values.get("traffic_rule_relation") if values.get("traffic_rule_relation") in {"and", "or"} else "or"
    values["traffic_match_rules"] = _normalize_rules(_json_list(values.get("traffic_match_rules"), []))
    for key in ("traffic_per_run_limit", "traffic_daily_limit"):
        values[key] = _safe_int(values.get(key), 20 if key == "traffic_per_run_limit" else 100, 1, 500)
    values["traffic_active_comment_min"] = _safe_int(values.get("traffic_active_comment_min"), 5, 0, 100000)
    for key in (
        "traffic_stay_seconds_min",
        "traffic_stay_seconds_max",
        "traffic_action_interval_seconds_min",
        "traffic_action_interval_seconds_max",
    ):
        values[key] = _safe_float(values.get(key), 1.0, 0.0, 300.0)
    return values


def update_settings(values: dict[str, Any]) -> dict[str, Any]:
    with database.connect() as conn:
        for key in TRAFFIC_SETTING_KEYS:
            if key not in values:
                continue
            value = values[key]
            if key == "traffic_comment_templates":
                value = json.dumps(_text_list(value), ensure_ascii=False)
            elif key in {"traffic_video_block_keywords", "traffic_author_block_keywords"}:
                value = json.dumps(_text_list(value), ensure_ascii=False)
            elif key == "traffic_match_rules":
                value = json.dumps(_normalize_rules(value), ensure_ascii=False)
            elif key == "traffic_rule_relation":
                value = value if value in {"and", "or"} else "or"
            elif isinstance(value, bool):
                value = "true" if value else "false"
            database.set_setting(conn, key, value)
    return get_settings()


def dashboard() -> dict[str, Any]:
    with database.connect() as conn:
        summary = {
            "campaigns": _count(conn, "SELECT COUNT(*) FROM traffic_campaigns"),
            "pending_targets": _count(conn, "SELECT COUNT(*) FROM traffic_targets WHERE status = 'pending'"),
            "running_runs": _count(conn, "SELECT COUNT(*) FROM traffic_runs WHERE status = 'running'"),
            "today_done": _count(
                conn,
                "SELECT COUNT(*) FROM traffic_targets WHERE status = 'succeeded' AND date(last_action_at) = date('now', 'localtime')",
            ),
        }
        campaigns = list_campaigns(conn=conn)
        runs = list_runs(conn=conn)
    return {"summary": summary, "campaigns": campaigns, "runs": runs}


def list_keywords() -> list[dict[str, Any]]:
    """返回可用于定向引流的抖音关键词视频分组。"""
    with database.connect() as conn:
        rows = conn.execute(
            """
            SELECT source_keyword AS keyword,
                   COUNT(*) AS content_count,
                   SUM(CASE WHEN content_url != '' THEN 1 ELSE 0 END) AS target_count,
                   MAX(updated_at) AS latest_at
            FROM contents
            WHERE platform = 'dy'
              AND NULLIF(source_keyword, '') IS NOT NULL
            GROUP BY source_keyword
            ORDER BY target_count DESC, latest_at DESC
            """
        ).fetchall()
    return database.rows_to_dicts(rows)


def list_campaigns(conn=None) -> list[dict[str, Any]]:
    if conn is not None:
        return _list_campaigns(conn)
    with database.connect() as owned_conn:
        return _list_campaigns(owned_conn)


def create_campaign(payload: TrafficCampaignCreate) -> dict[str, Any]:
    settings = get_settings()
    if payload.mode == "random":
        source_type = "random_feed"
    else:
        source_type = payload.source_type if payload.source_type in {"competitor", "keyword", "search_keyword"} else "competitor"
    if source_type == "search_keyword" and not payload.keyword.strip():
        raise ValueError("搜索关键词引流必须填写关键词")
    action_like = _setting_or_payload(payload.action_like, settings["traffic_action_like"])
    action_follow = _setting_or_payload(payload.action_follow, settings["traffic_action_follow"])
    action_comment = _setting_or_payload(payload.action_comment, settings["traffic_action_comment"])
    action_image = bool(payload.action_image)
    templates = _template_list(payload.comment_templates if payload.comment_templates is not None else settings["traffic_comment_templates"])
    if action_comment and not templates:
        raise ValueError("至少需要一条引流文案")
    if action_image and not payload.image_asset_ids:
        raise ValueError("发送图片时至少选择一张图片素材")
    stay_min, stay_max = _ordered_pair(
        payload.stay_seconds_min if payload.stay_seconds_min is not None else settings["traffic_stay_seconds_min"],
        payload.stay_seconds_max if payload.stay_seconds_max is not None else settings["traffic_stay_seconds_max"],
    )
    interval_min, interval_max = _ordered_pair(
        payload.action_interval_seconds_min if payload.action_interval_seconds_min is not None else settings["traffic_action_interval_seconds_min"],
        payload.action_interval_seconds_max if payload.action_interval_seconds_max is not None else settings["traffic_action_interval_seconds_max"],
    )
    per_run_limit = _safe_int(payload.per_run_limit if payload.per_run_limit is not None else settings["traffic_per_run_limit"], 20, 1, 100)
    daily_limit = _safe_int(payload.daily_limit if payload.daily_limit is not None else settings["traffic_daily_limit"], 100, 1, 500)
    rule_config = _rule_config_from_payload(payload, settings)
    name = payload.name.strip() or ("随机引流" if payload.mode == "random" else f"定向引流-{source_type}")
    with database.connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO traffic_campaigns(
                name, platform, mode, source_type, keyword, action_like, action_follow, action_comment, action_image,
                comment_templates, image_asset_ids, per_run_limit, daily_limit,
                stay_seconds_min, stay_seconds_max, action_interval_seconds_min, action_interval_seconds_max, rule_config
            )
            VALUES(?, 'dy', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                name,
                payload.mode,
                source_type,
                payload.keyword.strip(),
                int(action_like),
                int(action_follow),
                int(action_comment),
                int(action_image),
                json.dumps(templates, ensure_ascii=False),
                json.dumps(payload.image_asset_ids, ensure_ascii=False),
                per_run_limit,
                daily_limit,
                stay_min,
                stay_max,
                interval_min,
                interval_max,
                json.dumps(rule_config, ensure_ascii=False),
            ),
        )
        campaign_id = int(cur.lastrowid)
    return get_campaign(campaign_id)


def get_campaign(campaign_id: int) -> dict[str, Any]:
    with database.connect() as conn:
        row = conn.execute("SELECT * FROM traffic_campaigns WHERE id = ?", (campaign_id,)).fetchone()
        if not row:
            raise ValueError("引流计划不存在")
        return _campaign_dict(row)


def delete_campaign(campaign_id: int) -> dict[str, int]:
    with database.connect() as conn:
        row = conn.execute("SELECT id FROM traffic_campaigns WHERE id = ?", (campaign_id,)).fetchone()
        if not row:
            raise ValueError("引流计划不存在")
        running = _count(conn, "SELECT COUNT(*) FROM traffic_runs WHERE campaign_id = ? AND status IN ('pending', 'running')", (campaign_id,))
        if running:
            raise ValueError("该计划已有引流批次正在运行，请先停止批次")
        deleted = conn.execute("DELETE FROM traffic_campaigns WHERE id = ?", (campaign_id,)).rowcount
    return {"deleted": int(deleted or 0)}


def build_targets(campaign_id: int, limit: int = 50) -> dict[str, Any]:
    campaign = get_campaign(campaign_id)
    if campaign["mode"] == "random":
        return {"created": 0, "skipped": 0, "message": "随机引流在运行时从推荐流写入目标"}
    if campaign["source_type"] == "search_keyword":
        return {"created": 0, "skipped": 0, "message": "搜索关键词引流在运行时从搜索结果写入目标"}
    safe_limit = max(1, min(int(limit or 50), 500))
    rule_config = _rule_config_from_settings(get_settings())
    with database.connect() as conn:
        if campaign["source_type"] == "competitor":
            rows = conn.execute(
                """
                SELECT c.id, c.platform, c.content_id, c.content_url, c.title, c.description, c.source_keyword,
                       c.like_count, c.comment_count, ua.id AS author_account_id, ua.nickname AS author_name
                FROM contents c
                JOIN user_accounts ua ON ua.id = c.author_account_id
                WHERE c.platform = 'dy'
                  AND ua.competitor_status = '竞品'
                  AND c.content_url != ''
                ORDER BY COALESCE(c.comment_count, 0) DESC, COALESCE(c.like_count, 0) DESC, c.updated_at DESC, c.id DESC
                LIMIT ?
                """,
                (safe_limit,),
            ).fetchall()
        elif campaign["source_type"] == "keyword":
            keyword = str(campaign["keyword"] or "").strip()
            if not keyword:
                raise ValueError("关键词定向引流必须填写关键词")
            rows = conn.execute(
                """
                SELECT c.id, c.platform, c.content_id, c.content_url, c.title, c.description, c.source_keyword,
                       c.like_count, c.comment_count, ua.id AS author_account_id, ua.nickname AS author_name
                FROM contents c
                LEFT JOIN user_accounts ua ON ua.id = c.author_account_id
                WHERE c.platform = 'dy'
                  AND c.source_keyword = ?
                  AND c.content_url != ''
                ORDER BY COALESCE(c.comment_count, 0) DESC, COALESCE(c.like_count, 0) DESC, c.updated_at DESC, c.id DESC
                LIMIT ?
                """,
                (keyword, safe_limit),
            ).fetchall()
        else:
            raise ValueError("不支持的引流来源")
        created = 0
        skipped = 0
        duplicated = 0
        for row in rows:
            row_data = database.row_to_dict(row) or {}
            target_key = normalize_target_key("dy", row_data.get("content_id"), row_data.get("content_url"))
            if not target_key:
                skipped += 1
                continue
            if has_comment_record("dy", target_key, conn=conn):
                duplicated += 1
                continue
            allowed, _reason = _target_allowed(row_data, rule_config)
            if not allowed:
                skipped += 1
                continue
            title = _join_text(row["title"], row["description"])
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO traffic_targets(
                    campaign_id, platform, source_type, target_key, content_row_id, content_url,
                    author_account_id, author_name, title, keyword, selected_comment
                )
                VALUES(?, 'dy', ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    campaign_id,
                    campaign["source_type"],
                    target_key,
                    int(row["id"]),
                    row["content_url"] or "",
                    row["author_account_id"],
                    row["author_name"] or "",
                    title,
                    row["source_keyword"] or campaign["keyword"] or "",
                    "",
                ),
            )
            if cur.rowcount:
                created += 1
            else:
                skipped += 1
    return {"created": created, "skipped": skipped, "duplicated": duplicated, "campaign_id": campaign_id}


def list_targets(campaign_id: int, status: str = "", page: int = 1, page_size: int = 30) -> dict[str, Any]:
    page = max(1, int(page or 1))
    page_size = max(1, min(int(page_size or 30), 100))
    where = "WHERE campaign_id = ?"
    params: list[Any] = [campaign_id]
    if status:
        where += " AND status = ?"
        params.append(status)
    with database.connect() as conn:
        total = int(conn.execute(f"SELECT COUNT(*) AS c FROM traffic_targets {where}", params).fetchone()["c"])
        rows = conn.execute(
            f"""
            SELECT *
            FROM traffic_targets
            {where}
            ORDER BY
              CASE status WHEN 'pending' THEN 0 WHEN 'running' THEN 1 WHEN 'failed' THEN 2 ELSE 3 END,
              updated_at DESC, id DESC
            LIMIT ? OFFSET ?
            """,
            (*params, page_size, (page - 1) * page_size),
        ).fetchall()
    return {"rows": database.rows_to_dicts(rows), "total": total, "page": page, "page_size": page_size}


def create_run(campaign_id: int, limit: int | None = None) -> dict[str, Any]:
    license_service.ensure_traffic_authorized()
    _sync_campaign_runtime_settings(campaign_id)
    campaign = get_campaign(campaign_id)
    if campaign["platform"] != "dy":
        raise ValueError("V1 只支持抖音引流执行")
    safe_limit = max(1, min(int(limit or campaign["per_run_limit"] or 20), int(campaign["per_run_limit"] or 20), 100))
    today_done = _today_done_count(campaign_id)
    if today_done >= int(campaign["daily_limit"]):
        raise ValueError("今日引流数量已达到该计划每日上限")
    if campaign["mode"] == "targeted" and campaign["source_type"] != "search_keyword":
        build_targets(campaign_id, safe_limit)
        pending = _pending_target_count(campaign_id)
        if pending <= 0:
            raise ValueError("当前计划没有待执行视频，请先补充采集或调整筛选条件")
    _ensure_no_running_run(campaign_id)
    run_id = f"TRF-{uuid.uuid4().hex[:10].upper()}"
    with database.connect() as conn:
        conn.execute(
            """
            INSERT INTO traffic_runs(id, campaign_id, status, per_run_limit, daily_limit, counts)
            VALUES(?, ?, 'pending', ?, ?, '{}')
            """,
            (run_id, campaign_id, safe_limit, int(campaign["daily_limit"])),
        )
    try:
        process = _start_executor(run_id)
    except Exception as exc:
        with database.connect() as conn:
            conn.execute(
                "UPDATE traffic_runs SET status = 'failed', error = ?, finished_at = datetime('now', 'localtime'), updated_at = datetime('now', 'localtime') WHERE id = ?",
                (str(exc), run_id),
            )
        raise
    RUNNING_TRAFFIC_PROCESSES[run_id] = process
    with database.connect() as conn:
        conn.execute("UPDATE traffic_runs SET status = 'running', process_id = ?, started_at = datetime('now', 'localtime'), updated_at = datetime('now', 'localtime') WHERE id = ?", (process.pid, run_id))
        _log_event(conn, run_id, None, "system", "info", f"引流执行器已启动，PID={process.pid}")
    threading.Thread(target=_monitor_process, args=(run_id, process), daemon=True).start()
    return get_run(run_id)


def get_run(run_id: str) -> dict[str, Any]:
    with database.connect() as conn:
        row = conn.execute("SELECT r.*, c.name AS campaign_name FROM traffic_runs r LEFT JOIN traffic_campaigns c ON c.id = r.campaign_id WHERE r.id = ?", (run_id,)).fetchone()
        if not row:
            raise ValueError("引流批次不存在")
        events = database.rows_to_dicts(
            conn.execute(
                "SELECT * FROM traffic_action_events WHERE run_id = ? ORDER BY id DESC LIMIT 200",
                (run_id,),
            ).fetchall()
        )
    result = database.row_to_dict(row) or {}
    result["counts"] = _json_dict(result.get("counts"))
    result["events"] = events
    return result


def list_runs(conn=None) -> list[dict[str, Any]]:
    if conn is not None:
        return _list_runs(conn)
    with database.connect() as owned_conn:
        return _list_runs(owned_conn)


def cancel_run(run_id: str) -> dict[str, Any]:
    process = RUNNING_TRAFFIC_PROCESSES.get(run_id)
    if process and process.poll() is None:
        _terminate_process_tree(process)
    with database.connect() as conn:
        conn.execute(
            """
            UPDATE traffic_runs
            SET status = 'cancelled', finished_at = datetime('now', 'localtime'), updated_at = datetime('now', 'localtime')
            WHERE id = ?
            """,
            (run_id,),
        )
        conn.execute("UPDATE traffic_targets SET status = 'pending', run_id = '', updated_at = datetime('now', 'localtime') WHERE run_id = ? AND status = 'running'", (run_id,))
        _log_event(conn, run_id, None, "system", "warning", "用户请求取消引流批次")
    return get_run(run_id)


def recover_interrupted_running_runs() -> int:
    with database.connect() as conn:
        rows = conn.execute("SELECT id FROM traffic_runs WHERE status = 'running'").fetchall()
        for row in rows:
            run_id = str(row["id"])
            message = "服务启动时发现引流批次仍处于 running；上一次后端可能重启或中断，已标记失败"
            conn.execute(
                "UPDATE traffic_runs SET status = 'failed', error = ?, finished_at = datetime('now', 'localtime'), updated_at = datetime('now', 'localtime') WHERE id = ?",
                (message, run_id),
            )
            conn.execute("UPDATE traffic_targets SET status = 'pending', run_id = '', updated_at = datetime('now', 'localtime') WHERE run_id = ? AND status = 'running'", (run_id,))
            _log_event(conn, run_id, None, "system", "error", message)
    return len(rows)


def list_assets() -> list[dict[str, Any]]:
    with database.connect() as conn:
        return database.rows_to_dicts(conn.execute("SELECT * FROM traffic_assets ORDER BY id DESC").fetchall())


def create_asset(payload: TrafficAssetCreate) -> dict[str, Any]:
    mime_type, data = _decode_data_url(payload.data_url)
    suffix = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}.get(mime_type)
    if not suffix:
        raise ValueError("只支持 PNG、JPG、WEBP 图片")
    assets_dir = database.WORKSPACE_ROOT / "runtime" / "traffic_assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    file_name = f"{uuid.uuid4().hex}{suffix}"
    path = assets_dir / file_name
    path.write_bytes(data)
    name = payload.name.strip() or file_name
    with database.connect() as conn:
        cur = conn.execute(
            "INSERT INTO traffic_assets(name, mime_type, file_name, path, size_bytes) VALUES(?, ?, ?, ?, ?)",
            (name, mime_type, file_name, str(path), len(data)),
        )
        row = conn.execute("SELECT * FROM traffic_assets WHERE id = ?", (int(cur.lastrowid),)).fetchone()
    return database.row_to_dict(row) or {}


# 评论账本既给数据表展示，也用于跨计划防止同一视频重复评论。
def list_comment_records(page: int = 1, page_size: int = 30, query: str = "") -> dict[str, Any]:
    page = max(1, int(page or 1))
    page_size = max(1, min(int(page_size or 30), 100))
    where = "WHERE l.action_type = ? AND l.status = 'succeeded'"
    params: list[Any] = [COMMENT_ACTION_TYPE]
    if query.strip():
        where += " AND (l.video_intro LIKE ? OR l.comment_text LIKE ? OR l.author_name LIKE ?)"
        pattern = f"%{query.strip()}%"
        params.extend([pattern, pattern, pattern])
    with database.connect() as conn:
        total = int(conn.execute(f"SELECT COUNT(*) FROM traffic_action_ledger l {where}", params).fetchone()[0])
        rows = conn.execute(
            f"""
            SELECT l.*, c.name AS campaign_name
            FROM traffic_action_ledger l
            LEFT JOIN traffic_campaigns c ON c.id = l.campaign_id
            {where}
            ORDER BY l.commented_at DESC, l.id DESC
            LIMIT ? OFFSET ?
            """,
            (*params, page_size, (page - 1) * page_size),
        ).fetchall()
    return {"rows": database.rows_to_dicts(rows), "total": total, "page": page, "page_size": page_size}


def clear_comment_records() -> dict[str, int]:
    with database.connect() as conn:
        deleted = conn.execute("DELETE FROM traffic_action_ledger WHERE action_type = ?", (COMMENT_ACTION_TYPE,)).rowcount
    return {"deleted": int(deleted or 0)}


# 优先使用平台视频 ID，避免同一视频换 URL 后重复入队。
def normalize_target_key(platform: str, content_id: Any = "", content_url: Any = "") -> str:
    raw_id = str(content_id or "").strip()
    if raw_id:
        return f"{platform}:video:{raw_id}"
    parsed_id = _video_id_from_url(str(content_url or ""))
    if parsed_id:
        return f"{platform}:video:{parsed_id}"
    return ""


def has_comment_record(platform: str, target_key: str, conn=None) -> bool:
    if not target_key:
        return False
    sql = """
        SELECT 1 FROM traffic_action_ledger
        WHERE platform = ? AND target_key = ? AND action_type = ?
          AND status IN ('running', 'succeeded', 'uncertain')
        LIMIT 1
    """
    params = (platform, target_key, COMMENT_ACTION_TYPE)
    if conn is not None:
        return conn.execute(sql, params).fetchone() is not None
    with database.connect() as owned_conn:
        return owned_conn.execute(sql, params).fetchone() is not None


def claim_comment_action(campaign: dict[str, Any], target: dict[str, Any], run_id: str) -> bool:
    target_key = str(target.get("target_key") or "")
    if not target_key:
        return False
    with database.connect() as conn:
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO traffic_action_ledger(
                platform, target_key, action_type, campaign_id, run_id, target_id,
                content_url, video_intro, author_name, like_count, status
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'running')
            """,
            (
                str(target.get("platform") or campaign.get("platform") or "dy"),
                target_key,
                COMMENT_ACTION_TYPE,
                int(campaign["id"]),
                run_id,
                int(target["id"]),
                str(target.get("content_url") or ""),
                str(target.get("title") or ""),
                str(target.get("author_name") or ""),
                _safe_int(target.get("like_count"), 0, 0, 100000000),
            ),
        )
    return bool(cur.rowcount)


def complete_comment_action(target: dict[str, Any], comment_text: str) -> None:
    with database.connect() as conn:
        conn.execute(
            """
            UPDATE traffic_action_ledger
            SET status = 'succeeded', comment_text = ?, commented_at = datetime('now', 'localtime')
            WHERE platform = ? AND target_key = ? AND action_type = ?
            """,
            (comment_text, str(target.get("platform") or "dy"), str(target.get("target_key") or ""), COMMENT_ACTION_TYPE),
        )
        conn.execute(
            "UPDATE traffic_targets SET selected_comment = ? WHERE id = ?",
            (comment_text, int(target["id"])),
        )


def release_comment_action(target: dict[str, Any]) -> None:
    with database.connect() as conn:
        conn.execute(
            "DELETE FROM traffic_action_ledger WHERE platform = ? AND target_key = ? AND action_type = ? AND status = 'running'",
            (str(target.get("platform") or "dy"), str(target.get("target_key") or ""), COMMENT_ACTION_TYPE),
        )


def release_run_comment_claims(run_id: str) -> None:
    with database.connect() as conn:
        conn.execute(
            "DELETE FROM traffic_action_ledger WHERE run_id = ? AND action_type = ? AND status = 'running'",
            (run_id, COMMENT_ACTION_TYPE),
        )


def _start_executor(run_id: str) -> subprocess.Popen[str]:
    with database.connect() as conn:
        media_path = crawler_adapter.normalize_path(database.get_setting(conn, "media_crawler_path"))
    media_dir = Path(media_path)
    python_path = media_dir / ".venv" / "Scripts" / "python.exe"
    if not python_path.exists():
        raise RuntimeError("引流执行器需要 MediaCrawler/.venv/Scripts/python.exe 和 Playwright 依赖")
    crawler_adapter._ensure_cdp_browser_for_existing_mode(media_dir, headless=False)
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["AI_CUSTOMER_DB"] = str(database.get_db_path())
    site_packages = media_dir / ".venv" / "Lib" / "site-packages"
    pythonpath_items = [str(database.BACKEND_ROOT)]
    if site_packages.exists():
        pythonpath_items.append(str(site_packages))
    if env.get("PYTHONPATH"):
        pythonpath_items.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(pythonpath_items)
    script = database.BACKEND_ROOT / "app" / "traffic_executor.py"
    return subprocess.Popen(
        [str(python_path), str(script), run_id],
        cwd=str(database.WORKSPACE_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if platform.system() == "Windows" else 0,
    )


def _monitor_process(run_id: str, process: subprocess.Popen[str]) -> None:
    assert process.stdout is not None
    for line in process.stdout:
        message = line.strip()
        if not message:
            continue
        with database.connect() as conn:
            _log_event(conn, run_id, None, "log", "info", message)
    return_code = process.wait()
    RUNNING_TRAFFIC_PROCESSES.pop(run_id, None)
    with database.connect() as conn:
        row = conn.execute("SELECT status FROM traffic_runs WHERE id = ?", (run_id,)).fetchone()
        if not row or row["status"] not in {"running", "pending"}:
            return
        if return_code == 0:
            conn.execute("UPDATE traffic_runs SET status = 'succeeded', finished_at = datetime('now', 'localtime'), updated_at = datetime('now', 'localtime') WHERE id = ?", (run_id,))
        else:
            message = f"引流执行器退出码异常：{return_code}"
            conn.execute("UPDATE traffic_runs SET status = 'failed', error = ?, finished_at = datetime('now', 'localtime'), updated_at = datetime('now', 'localtime') WHERE id = ?", (message, run_id))
            _log_event(conn, run_id, None, "system", "error", message)


def _terminate_process_tree(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    if platform.system().lower() == "windows":
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        return
    process.terminate()


def _campaign_dict(row: Any) -> dict[str, Any]:
    item = database.row_to_dict(row) or {}
    item["action_like"] = bool(item.get("action_like"))
    item["action_follow"] = bool(item.get("action_follow"))
    item["action_comment"] = bool(item.get("action_comment"))
    item["action_image"] = bool(item.get("action_image"))
    item["comment_templates"] = _json_list(item.get("comment_templates"), [])
    item["image_asset_ids"] = [int(value) for value in _json_list(item.get("image_asset_ids"), []) if str(value).isdigit()]
    item["rule_config"] = _json_dict(item.get("rule_config"))
    return item


def _list_campaigns(conn) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT c.*,
               (SELECT COUNT(*) FROM traffic_targets t WHERE t.campaign_id = c.id) AS target_count,
               (SELECT COUNT(*) FROM traffic_targets t WHERE t.campaign_id = c.id AND t.status = 'pending') AS pending_count,
               (SELECT COUNT(*) FROM traffic_targets t WHERE t.campaign_id = c.id AND t.status = 'succeeded') AS succeeded_count
        FROM traffic_campaigns c
        ORDER BY c.updated_at DESC, c.id DESC
        """
    ).fetchall()
    return [_campaign_dict(row) for row in rows]


def _list_runs(conn) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT r.*, c.name AS campaign_name
        FROM traffic_runs r
        LEFT JOIN traffic_campaigns c ON c.id = r.campaign_id
        ORDER BY r.created_at DESC
        LIMIT 20
        """
    ).fetchall()
    result = []
    for row in rows:
        item = database.row_to_dict(row) or {}
        item["counts"] = _json_dict(item.get("counts"))
        result.append(item)
    return result


def _sync_campaign_runtime_settings(campaign_id: int) -> None:
    settings = get_settings()
    templates = _template_list(settings["traffic_comment_templates"])
    stay_min, stay_max = _ordered_pair(settings["traffic_stay_seconds_min"], settings["traffic_stay_seconds_max"])
    interval_min, interval_max = _ordered_pair(settings["traffic_action_interval_seconds_min"], settings["traffic_action_interval_seconds_max"])
    rule_config = _rule_config_from_settings(settings)
    with database.connect() as conn:
        row = conn.execute("SELECT action_comment FROM traffic_campaigns WHERE id = ?", (campaign_id,)).fetchone()
        if row and row["action_comment"] and not templates:
            raise ValueError("发送文案时至少需要一条引流文案")
        conn.execute(
            """
            UPDATE traffic_campaigns
            SET action_like = ?, action_follow = ?, comment_templates = ?,
                per_run_limit = ?, daily_limit = ?, stay_seconds_min = ?, stay_seconds_max = ?,
                action_interval_seconds_min = ?, action_interval_seconds_max = ?, rule_config = ?,
                updated_at = datetime('now', 'localtime')
            WHERE id = ?
            """,
            (
                int(settings["traffic_action_like"]),
                int(settings["traffic_action_follow"]),
                json.dumps(templates, ensure_ascii=False),
                _safe_int(settings["traffic_per_run_limit"], 20, 1, 100),
                _safe_int(settings["traffic_daily_limit"], 100, 1, 500),
                stay_min,
                stay_max,
                interval_min,
                interval_max,
                json.dumps(rule_config, ensure_ascii=False),
                campaign_id,
            ),
        )


def _rule_config_from_payload(payload: TrafficCampaignCreate, settings: dict[str, Any]) -> dict[str, Any]:
    # 引流规则保持扁平结构；当前计划层级不需要递归规则组。
    config = _rule_config_from_settings(settings)
    if payload.only_active_video is not None:
        config["only_active_video"] = bool(payload.only_active_video)
    if payload.active_comment_min is not None:
        config["active_comment_min"] = _safe_int(payload.active_comment_min, 5, 0, 100000)
    if payload.video_block_keywords is not None:
        config["video_block_keywords"] = _text_list(payload.video_block_keywords)
    if payload.author_block_keywords is not None:
        config["author_block_keywords"] = _text_list(payload.author_block_keywords)
    if payload.rule_relation is not None:
        config["rule_relation"] = payload.rule_relation if payload.rule_relation in {"and", "or"} else "or"
    if payload.match_rules is not None:
        config["match_rules"] = _normalize_rules(payload.match_rules)
    return config


def _rule_config_from_settings(settings: dict[str, Any]) -> dict[str, Any]:
    return {
        "only_active_video": bool(settings.get("traffic_only_active_video")),
        "active_comment_min": _safe_int(settings.get("traffic_active_comment_min"), 5, 0, 100000),
        "video_block_keywords": _text_list(settings.get("traffic_video_block_keywords")),
        "author_block_keywords": _text_list(settings.get("traffic_author_block_keywords")),
        "rule_relation": settings.get("traffic_rule_relation") if settings.get("traffic_rule_relation") in {"and", "or"} else "or",
        "match_rules": _normalize_rules(settings.get("traffic_match_rules")),
    }


def _target_allowed(target: dict[str, Any], config: dict[str, Any]) -> tuple[bool, str]:
    author = _lower_text(target.get("author_name"))
    title = _lower_text(_join_text(target.get("title"), target.get("description"), target.get("content_url")))
    keyword = _lower_text(_join_text(target.get("keyword"), target.get("source_keyword")))
    if _contains_any(author, config.get("author_block_keywords")):
        return False, "作者命中屏蔽词"
    if _contains_any(title, config.get("video_block_keywords")):
        return False, "视频命中屏蔽词"
    comment_count = target.get("comment_count")
    if config.get("only_active_video") and comment_count is not None:
        if _safe_int(comment_count, 0, 0, 100000000) < _safe_int(config.get("active_comment_min"), 5, 0, 100000):
            return False, "视频评论数不足"
    rules = _normalize_rules(config.get("match_rules"))
    if not rules:
        return True, ""
    matches = [_rule_matches(rule, author, title, keyword) for rule in rules]
    matched = all(matches) if config.get("rule_relation") == "and" else any(matches)
    return (True, "") if matched else (False, "未命中引流规则")


def _rule_matches(rule: dict[str, str], author: str, title: str, keyword: str) -> bool:
    field = rule.get("field")
    value = _lower_text(rule.get("keyword"))
    if not value:
        return False
    if field == "author":
        return value in author
    if field == "keyword":
        return value in keyword
    return value in title


def _contains_any(text: str, keywords: Any) -> bool:
    return any(_lower_text(keyword) in text for keyword in _text_list(keywords))


def _video_id_from_url(value: str) -> str:
    parsed = urlparse(value)
    match = re.search(r"/video/([^/?#]+)", parsed.path)
    return match.group(1) if match else ""


def _lower_text(value: Any) -> str:
    return str(value or "").strip().lower()


def _setting_or_payload(value: Any, default: Any) -> Any:
    return default if value is None else value


def _template_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _text_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _normalize_rules(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    rules: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        field = str(item.get("field") or "title")
        keyword = str(item.get("keyword") or "").strip()
        if field not in RULE_FIELDS or not keyword:
            continue
        rules.append({"field": field, "keyword": keyword})
    return rules


def _decode_data_url(data_url: str) -> tuple[str, bytes]:
    header, _, body = str(data_url or "").partition(",")
    if not header.startswith("data:image/") or ";base64" not in header or not body:
        raise ValueError("图片数据格式不正确")
    mime_type = header.removeprefix("data:").split(";", 1)[0]
    return mime_type, base64.b64decode(body, validate=True)


def _log_event(conn, run_id: str, target_id: int | None, action: str, status: str, detail: str, screenshot_path: str = "") -> None:
    conn.execute(
        "INSERT INTO traffic_action_events(run_id, target_id, action, status, detail, screenshot_path) VALUES(?, ?, ?, ?, ?, ?)",
        (run_id, target_id, action, status, detail, screenshot_path),
    )


def _today_done_count(campaign_id: int) -> int:
    with database.connect() as conn:
        return _count(conn, "SELECT COUNT(*) FROM traffic_targets WHERE campaign_id = ? AND status = 'succeeded' AND date(last_action_at) = date('now', 'localtime')", (campaign_id,))


def _pending_target_count(campaign_id: int) -> int:
    with database.connect() as conn:
        return _count(conn, "SELECT COUNT(*) FROM traffic_targets WHERE campaign_id = ? AND status = 'pending'", (campaign_id,))


def _ensure_no_running_run(campaign_id: int) -> None:
    with database.connect() as conn:
        running = _count(conn, "SELECT COUNT(*) FROM traffic_runs WHERE campaign_id = ? AND status IN ('pending', 'running')", (campaign_id,))
    if running:
        raise ValueError("该计划已有引流批次正在运行")


def _count(conn, sql: str, params: tuple[Any, ...] = ()) -> int:
    return int(conn.execute(sql, params).fetchone()[0])


def _safe_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(parsed, maximum))


def _safe_float(value: Any, default: float, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(parsed, maximum))


def _ordered_pair(left: float, right: float) -> tuple[float, float]:
    a = _safe_float(left, 0, 0, 300)
    b = _safe_float(right, a, 0, 300)
    return (a, b) if a <= b else (b, a)


def _json_list(value: Any, default: list[Any]) -> list[Any]:
    try:
        parsed = json.loads(value) if isinstance(value, str) else value
    except json.JSONDecodeError:
        return default
    return parsed if isinstance(parsed, list) else default


def _json_dict(value: Any) -> dict[str, Any]:
    try:
        parsed = json.loads(value) if isinstance(value, str) else value
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _join_text(*values: Any) -> str:
    return " ".join(str(value or "").strip() for value in values if str(value or "").strip())
