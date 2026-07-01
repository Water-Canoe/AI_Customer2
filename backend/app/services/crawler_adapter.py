from __future__ import annotations

import os
import platform
import re
import socket
import sqlite3
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable, Literal

from app import database
from app.schemas import TaskCreate
from app.services.importer import CONTENT_TABLES, import_for_task


PLATFORM_LABELS = {"dy": "抖音", "xhs": "小红书", "ks": "快手"}
MODE_LABELS = {
    "competitor_discovery": "竞品账号采集",
    "competitor_crawl": "竞品账号爬取",
    "demand_content": "找需求内容",
    "own_account": "自家账号互动",
    "profile_enrichment": "账号资料补全",
    "account_analysis": "账号分析",
}
PROFILE_ENRICHMENT_PLATFORMS = {"dy", "xhs"}

RUNNING_PROCESSES: dict[str, subprocess.Popen[str]] = {}
CDP_BROWSER_PROCESS: subprocess.Popen[str] | None = None

ACCOUNT_ANALYSIS_AWEME_LOG_MARKERS = (
    "store.douyin.update_douyin_aweme",
    "store.xhs.update_xhs_note",
)
ACCOUNT_ANALYSIS_CREATOR_LOG_MARKERS = (
    "store.douyin.save_creator",
    "store.xhs.save_creator",
)


def normalize_path(value: str) -> str:
    """Remove quotes users often paste around Windows paths."""
    return value.strip().strip('"').strip("'")


def next_task_id() -> str:
    with database.connect() as conn:
        current = int(database.get_setting(conn, "next_task_number", "1"))
        if current > 0xFFFF:
            raise ValueError("任务ID已超过 4 位十六进制上限 FFFF")
        database.set_setting(conn, "next_task_number", str(current + 1))
        return f"{current:04X}"


def infer_crawler_type(payload: TaskCreate) -> str:
    if payload.mode in ("competitor_discovery", "demand_content"):
        return "search"
    if payload.specified_id.strip():
        return "detail"
    return "creator"


def normalize_task_defaults(payload: TaskCreate) -> TaskCreate:
    crawler_type = infer_crawler_type(payload)
    keywords = payload.keywords.strip()
    specified_id = payload.specified_id.strip()
    creator_id = payload.creator_id.strip()
    if crawler_type == "search":
        if not keywords:
            raise ValueError("搜索型任务必须填写关键词，避免使用 MediaCrawler 默认关键词")
        specified_id = ""
        creator_id = ""
    elif crawler_type == "detail":
        if not specified_id:
            raise ValueError("详情采集任务必须填写指定内容ID/链接")
        keywords = ""
        creator_id = ""
    else:
        if not creator_id:
            raise ValueError("账号采集任务必须填写创作者主页/ID")
        keywords = ""
        specified_id = ""
    collect_comments = payload.collect_comments or payload.mode in ("competitor_crawl", "own_account")
    collect_sub_comments = payload.collect_sub_comments or payload.mode in ("competitor_crawl", "own_account")
    name = payload.name.strip()
    if not name:
        creator_items = [item.strip() for item in creator_id.split(",") if item.strip()]
        if payload.mode == "account_analysis" and len(creator_items) > 1:
            seed = f"{len(creator_items)}个竞品账号"
        else:
            seed = keywords or creator_id or specified_id or PLATFORM_LABELS[payload.platform]
        name = f"{MODE_LABELS[payload.mode]}-{seed}"
    return payload.model_copy(
        update={
            "name": name,
            "keywords": keywords,
            "specified_id": specified_id,
            "creator_id": creator_id,
            "collect_comments": collect_comments,
            "collect_sub_comments": collect_sub_comments,
            "collect_authors": True,
        }
    )


def build_command(task: dict[str, object], media_crawler_path: str) -> list[str]:
    """Translate project task fields to MediaCrawler CLI flags."""
    command = [
        "uv",
        "run",
        "main.py",
        "--platform",
        str(task["platform"]),
        "--lt",
        str(task["login_type"]),
        "--type",
        str(task["crawler_type"]),
        "--save_data_option",
        "sqlite",
        "--crawler_max_notes_count",
        str(task["content_count"]),
        "--max_comments_count_singlenotes",
        str(task["comment_count"]),
        "--get_comment",
        "true" if task["collect_comments"] else "false",
        "--get_sub_comment",
        "true" if task["collect_sub_comments"] else "false",
        "--max_concurrency_num",
        str(task["max_concurrency"]),
        "--headless",
        "true" if task["headless"] else "false",
    ]
    crawler_type = str(task["crawler_type"])
    if crawler_type == "search" and task["keywords"]:
        command.extend(["--keywords", str(task["keywords"])])
    if crawler_type == "detail" and task["specified_id"]:
        command.extend(["--specified_id", str(task["specified_id"])])
    if crawler_type == "creator" and task["creator_id"]:
        command.extend(["--creator_id", str(task["creator_id"])])
    return command


def preview_task(payload: TaskCreate) -> dict[str, object]:
    """Return the exact normalized command without creating a task."""
    task = normalize_task_defaults(payload)
    crawler_type = infer_crawler_type(task)
    with database.connect() as conn:
        media_path = normalize_path(database.get_setting(conn, "media_crawler_path"))
    normalized = task.model_dump()
    command = build_command({**normalized, "crawler_type": crawler_type}, media_path)
    sanitized = _sanitized_fields(payload, task)
    return {
        "name": task.name,
        "mode": task.mode,
        "platform": task.platform,
        "crawler_type": crawler_type,
        "normalized": normalized,
        "sanitized": sanitized,
        "warnings": _preview_warnings(task, crawler_type, sanitized),
        "media_crawler_path": media_path,
        "command": command,
        "command_text": " ".join(command),
    }


def _sanitized_fields(original: TaskCreate, normalized: TaskCreate) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for field in ("keywords", "creator_id", "specified_id"):
        before = str(getattr(original, field) or "")
        after = str(getattr(normalized, field) or "")
        if before != after:
            result[field] = {"before": before, "after": after}
    return result


def _preview_warnings(task: TaskCreate, crawler_type: str, sanitized: dict[str, dict[str, str]]) -> list[str]:
    warnings: list[str] = []
    if sanitized:
        cleaned = "、".join(_task_field_label(field) for field in sanitized)
        warnings.append(f"已按采集类型净化参数：{cleaned}")
    if crawler_type == "search":
        warnings.append("搜索型任务只会向 MediaCrawler 传递关键词，不会传递创作者主页或内容ID。")
    elif crawler_type == "detail":
        warnings.append("详情任务只会向 MediaCrawler 传递指定内容ID/链接，不会传递关键词或创作者主页。")
    else:
        warnings.append("账号任务只会向 MediaCrawler 传递创作者主页/ID，不会传递关键词或内容ID。")
    if task.mode in ("competitor_crawl", "own_account") and not task.collect_comments:
        warnings.append("该模式会自动打开评论采集，因为线索客户来自评论区。")
    if task.platform == "ks" and task.mode == "competitor_discovery":
        warnings.append("快手不能补齐主页简介，竞品候选更依赖昵称和内容证据。")
    return warnings


def _task_field_label(field: str) -> str:
    return {
        "keywords": "关键词",
        "creator_id": "创作者主页/ID",
        "specified_id": "指定内容ID/链接",
    }.get(field, field)


def create_task(payload: TaskCreate) -> dict[str, object]:
    task = normalize_task_defaults(payload)
    task_id = next_task_id()
    crawler_type = infer_crawler_type(task)
    raw_started_ts_ms = int(time.time() * 1000)
    with database.connect() as conn:
        media_path = normalize_path(database.get_setting(conn, "media_crawler_path"))
        command = build_command(
            {
                **task.model_dump(),
                "crawler_type": crawler_type,
            },
            media_path,
        )
        conn.execute(
            """
            INSERT INTO crawl_jobs (
                id, name, mode, platform, login_type, crawler_type, keywords,
                specified_id, creator_id, content_count, comment_count,
                collect_content, collect_comments, collect_authors, collect_sub_comments,
                max_concurrency, tcp_mode, headless, execute_crawler,
                status, command, raw_started_ts_ms
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task_id,
                task.name,
                task.mode,
                task.platform,
                task.login_type,
                crawler_type,
                task.keywords,
                task.specified_id,
                task.creator_id,
                task.content_count,
                task.comment_count,
                int(task.collect_content),
                int(task.collect_comments),
                int(task.collect_authors),
                int(task.collect_sub_comments),
                task.max_concurrency,
                int(task.tcp_mode),
                int(task.headless),
                int(task.execute_crawler),
                "pending",
                " ".join(command),
                raw_started_ts_ms,
            ),
        )
        log_task(conn, task_id, "info", "任务已创建，等待执行")
    return get_task(task_id) or {}


def _account_profile_identifier(account: dict[str, object]) -> str:
    # creator 模式优先使用主页链接；抖音主页链接内含 sec_uid，跨平台也更贴近 MediaCrawler 的解析入口。
    platform = str(account.get("platform") or "")
    profile_url = str(account.get("profile_url") or "").strip()
    sec_uid = str(account.get("sec_uid") or "").strip()
    platform_user_id = str(account.get("platform_user_id") or "").strip()
    if profile_url:
        return profile_url
    if platform == "dy" and sec_uid:
        return sec_uid
    return platform_user_id


def create_profile_enrichment_task(
    account_id: int,
    *,
    mode: Literal["profile_enrichment", "account_analysis"] = "profile_enrichment",
    name_prefix: str = "账号资料补全",
    content_count: int = 1,
) -> dict[str, object]:
    with database.connect() as conn:
        row = conn.execute("SELECT * FROM user_accounts WHERE id = ?", (account_id,)).fetchone()
        if not row:
            raise ValueError("账号不存在，无法采集主页资料")
        account = database.row_to_dict(row)
        platform = str(account.get("platform") or "")
        if platform not in PROFILE_ENRICHMENT_PLATFORMS:
            raise ValueError("MediaCrawler SQLite 目前仅支持抖音/小红书账号主页资料采集，快手主页资料不会写入 SQLite")
        creator_id = _account_profile_identifier(account)
        if not creator_id:
            raise ValueError("账号缺少主页链接或平台ID，无法采集主页资料")
        login_type = database.get_setting(conn, "login_type", "qrcode")
        headless = database.get_setting(conn, "headless", "false") == "true"

    nickname = str(account.get("nickname") or account_id)
    task = create_task(
        TaskCreate(
            name=f"{name_prefix}-{nickname}",
            mode=mode,
            platform=platform,
            login_type=login_type if login_type in ("qrcode", "phone", "cookie") else "qrcode",
            creator_id=creator_id,
            content_count=content_count,
            comment_count=0,
            collect_comments=False,
            collect_sub_comments=False,
            max_concurrency=1,
            headless=headless,
            execute_crawler=True,
        )
    )
    with database.connect() as conn:
        log_task(conn, str(task["id"]), "info", f"{name_prefix}来源账号：{nickname}（ID={account_id}）")
    return task


def create_profile_enrichment_batch(limit: int = 10) -> dict[str, object]:
    """Create a small serial queue for accounts that can actually be enriched."""
    safe_limit = max(1, min(int(limit), 50))
    with database.connect() as conn:
        rows = conn.execute(
            """
            SELECT ua.id
            FROM user_accounts ua
            WHERE ua.platform IN ('dy', 'xhs')
              AND COALESCE(ua.signature, '') = ''
              AND (
                COALESCE(ua.profile_url, '') <> ''
                OR COALESCE(ua.platform_user_id, '') <> ''
                OR (ua.platform = 'dy' AND COALESCE(ua.sec_uid, '') <> '')
              )
              AND (
                EXISTS (SELECT 1 FROM contents c WHERE c.author_account_id = ua.id)
                OR EXISTS (SELECT 1 FROM comments cm WHERE cm.author_account_id = ua.id)
                OR EXISTS (SELECT 1 FROM account_sources ac WHERE ac.account_id = ua.id AND ac.active = 1)
                OR EXISTS (SELECT 1 FROM lead_user_accounts lua WHERE lua.account_id = ua.id)
              )
              AND NOT EXISTS (
                SELECT 1
                FROM crawl_jobs j
                WHERE j.mode = 'profile_enrichment'
                  AND j.platform = ua.platform
                  AND j.status IN ('pending', 'running')
                  AND (
                    j.creator_id = ua.profile_url
                    OR j.creator_id = ua.platform_user_id
                    OR j.creator_id = ua.sec_uid
                  )
              )
            ORDER BY ua.updated_at DESC, ua.id DESC
            LIMIT ?
            """,
            (safe_limit,),
        ).fetchall()
    tasks = [create_profile_enrichment_task(int(row["id"])) for row in rows]
    return {
        "created": len(tasks),
        "limit": safe_limit,
        "task_ids": [str(task["id"]) for task in tasks],
        "tasks": tasks,
    }


def run_tasks_serially(task_ids: list[str]) -> None:
    # 批量补资料串行执行，避免同时启动多个 MediaCrawler 子进程。
    for task_id in task_ids:
        run_task(str(task_id))


def get_task(task_id: str) -> dict[str, object] | None:
    with database.connect() as conn:
        row = conn.execute("SELECT * FROM crawl_jobs WHERE id = ?", (task_id,)).fetchone()
        if not row:
            return None
        task = database.row_to_dict(row)
        task["outcome"] = _task_outcome(conn, task)
        return task


def list_tasks(include_archived: bool = False) -> list[dict[str, object]]:
    with database.connect() as conn:
        where = "" if include_archived else "WHERE archived = 0"
        rows = conn.execute(f"SELECT * FROM crawl_jobs {where} ORDER BY created_at DESC").fetchall()
        tasks = database.rows_to_dicts(rows)
        for task in tasks:
            task["outcome"] = _task_outcome(conn, task)
        return tasks


def _count(conn, sql: str, params: tuple[object, ...]) -> int:
    row = conn.execute(sql, params).fetchone()
    return int(row["count"] or 0) if row else 0


def _task_outcome(conn, task: dict[str, object]) -> dict[str, object]:
    task_id = str(task["id"])
    counts = {
        "raw_refs": _count(conn, "SELECT COUNT(*) AS count FROM raw_source_refs WHERE task_id = ?", (task_id,)),
        "contents": _count(conn, "SELECT COUNT(*) AS count FROM contents WHERE task_id = ?", (task_id,)),
        "comments": _count(conn, "SELECT COUNT(*) AS count FROM comments WHERE task_id = ?", (task_id,)),
        "competitor_candidates": _count(
            conn,
            "SELECT COUNT(DISTINCT account_id) AS count FROM account_sources WHERE task_id = ? AND active = 1",
            (task_id,),
        ),
        "leads": _count(
            conn,
            "SELECT COUNT(DISTINCT lead_account_id) AS count FROM lead_sources WHERE task_id = ? AND active = 1",
            (task_id,),
        ),
        "target_customers": _count(
            conn,
            """
            SELECT COUNT(DISTINCT lua.id) AS count
            FROM lead_sources ls
            JOIN lead_user_accounts lua ON lua.id = ls.lead_account_id
            WHERE ls.task_id = ? AND ls.active = 1 AND lua.hidden = 0
              AND lua.follow_status NOT IN ('待筛选', '无需跟进')
            """,
            (task_id,),
        ),
        "profile_enrichment_needed": _count(
            conn,
            """
            SELECT COUNT(*) AS count
            FROM (
                SELECT author_account_id AS account_id FROM contents WHERE task_id = ? AND author_account_id IS NOT NULL
                UNION
                SELECT author_account_id AS account_id FROM comments WHERE task_id = ? AND author_account_id IS NOT NULL
                UNION
                SELECT account_id FROM account_sources WHERE task_id = ? AND active = 1
                UNION
                SELECT lua.account_id
                FROM lead_sources ls
                JOIN lead_user_accounts lua ON lua.id = ls.lead_account_id
                WHERE ls.task_id = ? AND ls.active = 1
            ) related
            JOIN user_accounts ua ON ua.id = related.account_id
            WHERE ua.platform IN ('dy', 'xhs') AND COALESCE(ua.signature, '') = ''
            """,
            (task_id, task_id, task_id, task_id),
        ),
    }
    return {
        "counts": counts,
        "health": _task_outcome_health(str(task.get("status") or ""), counts),
        "next_actions": _task_next_actions(str(task.get("status") or ""), counts),
    }


def _task_outcome_health(status: str, counts: dict[str, int]) -> str:
    business_total = counts["contents"] + counts["comments"] + counts["competitor_candidates"] + counts["leads"]
    if status == "failed":
        return "failed"
    if status == "succeeded" and business_total == 0:
        return "empty"
    if counts["target_customers"] or counts["leads"] or counts["competitor_candidates"]:
        return "actionable"
    if counts["contents"] or counts["comments"]:
        return "collected"
    return "pending"


def _task_next_actions(status: str, counts: dict[str, int]) -> list[str]:
    if status == "failed":
        return ["查看错误日志，修正登录、路径、关键词或平台限制后重新创建任务"]
    if status == "succeeded" and counts["contents"] + counts["comments"] + counts["raw_refs"] == 0:
        return ["没有导入有效数据，先检查设置页的平台诊断、任务关键词/ID、登录状态和采集时间窗口"]
    actions: list[str] = []
    if counts["competitor_candidates"]:
        actions.append("进入 AI分析，批量筛选竞品候选")
    if counts["leads"]:
        actions.append("进入 AI分析，筛选线索客户并生成私信话术")
    if counts["target_customers"]:
        actions.append("进入数据表的目标客户库，继续跟进状态流转")
    if counts["profile_enrichment_needed"]:
        actions.append("账号主页简介为空，可在数据表点击补资料")
    if not actions and (counts["contents"] or counts["comments"]):
        actions.append("查看数据表或总览树，确认内容和评论来源")
    return actions


def list_task_logs(task_id: str) -> list[dict[str, object]]:
    with database.connect() as conn:
        rows = conn.execute(
            "SELECT * FROM task_logs WHERE task_id = ? ORDER BY id ASC",
            (task_id,),
        ).fetchall()
        return database.rows_to_dicts(rows)


def log_task(conn, task_id: str, level: str, message: str) -> None:
    conn.execute(
        "INSERT INTO task_logs(task_id, level, message) VALUES(?, ?, ?)",
        (task_id, level, message),
    )


def run_task_background(task_id: str, after_log: Callable[[str], None] | None = None) -> None:
    thread = threading.Thread(target=run_task, args=(task_id, after_log), daemon=True)
    thread.start()


def run_task(task_id: str, after_log: Callable[[str], None] | None = None) -> None:
    task = get_task(task_id)
    if not task:
        return
    with database.connect() as conn:
        conn.execute(
            """
            UPDATE crawl_jobs
            SET status = 'running', started_at = datetime('now', 'localtime'), updated_at = datetime('now', 'localtime')
            WHERE id = ?
            """,
            (task_id,),
        )
        log_task(conn, task_id, "info", "任务开始执行")

    if not task["execute_crawler"]:
        _finish_without_crawler(task_id)
        return

    with database.connect() as conn:
        media_path = normalize_path(database.get_setting(conn, "media_crawler_path"))
        command = build_command(task, media_path)

    media_dir = Path(media_path)
    if not media_dir.exists():
        _fail_task(task_id, f"MediaCrawler 路径不存在：{media_path}")
        return

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        schema_message = _ensure_media_crawler_sqlite_schema(media_dir, str(task["platform"]), env)
    except Exception as exc:
        _fail_task(task_id, f"初始化 MediaCrawler SQLite 表结构失败：{exc}")
        return
    if schema_message:
        with database.connect() as conn:
            log_task(conn, task_id, "info", schema_message)

    try:
        cdp_message = _ensure_cdp_browser_for_existing_mode(media_dir, bool(task.get("headless")))
    except Exception as exc:
        _fail_task(task_id, f"启动 MediaCrawler 前置 CDP 浏览器失败：{exc}")
        return
    if cdp_message:
        with database.connect() as conn:
            log_task(conn, task_id, "info", cdp_message)

    run_env = _media_crawler_subprocess_env(env, task)
    try:
        process = subprocess.Popen(
            command,
            cwd=media_path,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=run_env,
        )
    except Exception as exc:
        _fail_task(task_id, f"启动 MediaCrawler 失败：{exc}")
        return

    RUNNING_PROCESSES[task_id] = process
    with database.connect() as conn:
        conn.execute("UPDATE crawl_jobs SET process_id = ? WHERE id = ?", (process.pid, task_id))
        log_task(conn, task_id, "info", f"MediaCrawler 进程已启动，PID={process.pid}")

    controlled_stop = False
    analysis_progress: dict[str, Any] = {"creator": False, "contents": 0}
    assert process.stdout is not None
    for line in process.stdout:
        message = line.rstrip()
        if not message:
            continue
        with database.connect() as conn:
            log_task(conn, task_id, "info", message)
        if after_log:
            after_log(message)
        if _should_stop_account_analysis(task, message, analysis_progress):
            controlled_stop = True
            with database.connect() as conn:
                log_task(conn, task_id, "info", f"账号分析已采集到 {analysis_progress['contents']} 条视频，提前结束 MediaCrawler 子进程")
            _terminate_process_tree(process)
            break

    return_code = process.wait()
    RUNNING_PROCESSES.pop(task_id, None)
    if return_code != 0 and not controlled_stop:
        _fail_task(task_id, f"MediaCrawler 退出码异常：{return_code}")
        return

    try:
        import_result = import_for_task(task_id)
    except Exception as exc:
        _fail_task(task_id, f"采集完成，但归一化导入失败：{exc}")
        return

    with database.connect() as conn:
        log_task(conn, task_id, "info", f"归一化导入完成：{import_result}")
        conn.execute(
            """
            UPDATE crawl_jobs
            SET status = 'succeeded', finished_at = datetime('now', 'localtime'), updated_at = datetime('now', 'localtime')
            WHERE id = ?
            """,
            (task_id,),
        )
    _run_post_success_automation(task_id)


def _ensure_media_crawler_sqlite_schema(media_dir: Path, platform_value: str, env: dict[str, str]) -> str | None:
    mapping = CONTENT_TABLES.get(platform_value)
    required_table = mapping["table"] if mapping else ""
    if not required_table:
        return None

    db_path = media_dir / "database" / "sqlite_tables.db"
    if _sqlite_table_exists(db_path, required_table):
        return None

    db_path.parent.mkdir(parents=True, exist_ok=True)
    command = ["uv", "run", "main.py", "--init_db", "sqlite"]
    result = subprocess.run(
        command,
        cwd=media_dir,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=120,
    )
    if result.returncode != 0:
        output = (result.stdout or "").strip()
        raise RuntimeError(f"uv run main.py --init_db sqlite 退出码 {result.returncode}：{output}")
    if not _sqlite_table_exists(db_path, required_table):
        output = (result.stdout or "").strip()
        raise RuntimeError(f"初始化完成后仍缺少表 {required_table}：{output}")
    return f"检测到 MediaCrawler SQLite 缺少 {required_table} 表，已自动执行 uv run main.py --init_db sqlite 初始化表结构"


def _sqlite_table_exists(db_path: Path, table_name: str) -> bool:
    if not db_path.exists():
        return False
    try:
        conn = sqlite3.connect(db_path)
        try:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
                (table_name,),
            ).fetchone()
            return row is not None
        finally:
            conn.close()
    except sqlite3.Error:
        return False


def _ensure_cdp_browser_for_existing_mode(media_dir: Path, headless: bool) -> str | None:
    cdp_config = _read_media_crawler_cdp_config(media_dir)
    if not cdp_config["enabled"] or not cdp_config["connect_existing"]:
        return None

    debug_port = int(cdp_config["debug_port"])
    if _is_tcp_port_open("127.0.0.1", debug_port):
        return None

    browser_path = _detect_browser_path(str(cdp_config["custom_browser_path"]))
    user_data_dir = media_dir / "browser_data" / "ai_customer_cdp"
    user_data_dir.mkdir(parents=True, exist_ok=True)

    global CDP_BROWSER_PROCESS
    args = [
        browser_path,
        f"--remote-debugging-port={debug_port}",
        "--remote-debugging-address=127.0.0.1",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-background-timer-throttling",
        "--disable-backgrounding-occluded-windows",
        "--disable-renderer-backgrounding",
        "--disable-features=TranslateUI",
        "--disable-blink-features=AutomationControlled",
        "--exclude-switches=enable-automation",
        "--disable-infobars",
        f"--user-data-dir={user_data_dir}",
    ]
    if headless:
        args.extend(["--headless=new", "--disable-gpu"])
    else:
        args.append("--start-maximized")

    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if platform.system() == "Windows" else 0
    CDP_BROWSER_PROCESS = subprocess.Popen(
        args,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
        creationflags=creationflags,
    )
    if not _wait_for_tcp_port("127.0.0.1", debug_port, timeout_seconds=20):
        raise RuntimeError(f"Chrome 已启动但 CDP 端口 {debug_port} 未就绪")
    return f"检测到 MediaCrawler 需要连接已有 CDP 浏览器，但端口 {debug_port} 未开启；已自动启动 Chrome 调试实例"


def _read_media_crawler_cdp_config(media_dir: Path) -> dict[str, object]:
    config_path = media_dir / "config" / "base_config.py"
    if not config_path.exists():
        return {"enabled": False, "connect_existing": False, "debug_port": 9222, "custom_browser_path": ""}
    text = config_path.read_text(encoding="utf-8", errors="ignore")
    return {
        "enabled": _read_bool_assignment(text, "ENABLE_CDP_MODE", False),
        "connect_existing": _read_bool_assignment(text, "CDP_CONNECT_EXISTING", False),
        "debug_port": _read_int_assignment(text, "CDP_DEBUG_PORT", 9222),
        "custom_browser_path": _read_str_assignment(text, "CUSTOM_BROWSER_PATH", ""),
    }


def _read_bool_assignment(text: str, name: str, default: bool) -> bool:
    match = re.search(rf"^\s*{re.escape(name)}\s*=\s*(True|False)\b", text, re.MULTILINE)
    return (match.group(1) == "True") if match else default


def _read_int_assignment(text: str, name: str, default: int) -> int:
    match = re.search(rf"^\s*{re.escape(name)}\s*=\s*(\d+)\b", text, re.MULTILINE)
    return int(match.group(1)) if match else default


def _read_str_assignment(text: str, name: str, default: str) -> str:
    match = re.search(rf"^\s*{re.escape(name)}\s*=\s*['\"]([^'\"]*)['\"]", text, re.MULTILINE)
    return match.group(1) if match else default


def _detect_browser_path(custom_browser_path: str = "") -> str:
    if custom_browser_path and Path(custom_browser_path).is_file():
        return custom_browser_path
    candidates: list[str]
    if platform.system() == "Windows":
        candidates = [
            os.path.expandvars(r"%PROGRAMFILES%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%PROGRAMFILES(X86)%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%PROGRAMFILES%\Microsoft\Edge\Application\msedge.exe"),
            os.path.expandvars(r"%PROGRAMFILES(X86)%\Microsoft\Edge\Application\msedge.exe"),
        ]
    elif platform.system() == "Darwin":
        candidates = [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        ]
    else:
        candidates = [
            "/usr/bin/google-chrome",
            "/usr/bin/google-chrome-stable",
            "/usr/bin/chromium",
            "/usr/bin/chromium-browser",
            "/usr/bin/microsoft-edge",
        ]
    for candidate in candidates:
        if Path(candidate).is_file():
            return candidate
    raise RuntimeError("找不到 Chrome 或 Edge，请安装浏览器或在 MediaCrawler config/base_config.py 设置 CUSTOM_BROWSER_PATH")


def _is_tcp_port_open(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(1)
        return sock.connect_ex((host, port)) == 0


def _wait_for_tcp_port(host: str, port: int, timeout_seconds: int) -> bool:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if _is_tcp_port_open(host, port):
            return True
        time.sleep(0.5)
    return False


def _finish_without_crawler(task_id: str) -> None:
    try:
        result = import_for_task(task_id)
        status = "succeeded"
        message = f"已跳过真实采集并执行导入：{result}"
    except Exception as exc:
        status = "failed"
        message = f"跳过采集后的导入失败：{exc}"
    with database.connect() as conn:
        log_task(conn, task_id, "info" if status == "succeeded" else "error", message)
        conn.execute(
            """
            UPDATE crawl_jobs
            SET status = ?, error = ?, finished_at = datetime('now', 'localtime'), updated_at = datetime('now', 'localtime')
            WHERE id = ?
            """,
            (status, "" if status == "succeeded" else message, task_id),
        )
    if status == "succeeded":
        _run_post_success_automation(task_id)


def _run_post_success_automation(task_id: str) -> None:
    with database.connect() as conn:
        task = conn.execute("SELECT * FROM crawl_jobs WHERE id = ?", (task_id,)).fetchone()
        if not task:
            return
        auto_competitor = database.get_setting(conn, "auto_analyze_competitors", "false") == "true"
        auto_lead = database.get_setting(conn, "auto_analyze_leads", "false") == "true"
        task_mode = str(task["mode"])
        if auto_competitor and task_mode == "competitor_discovery":
            log_task(conn, task_id, "info", "已开启自动分析竞品账号，开始创建账号分析任务")

    if auto_competitor and task_mode == "competitor_discovery":
        try:
            from app.services import account_actions

            result = account_actions.create_task_account_analysis_task(task_id)
            if not result.get("task_ids"):
                with database.connect() as conn:
                    log_task(conn, task_id, "info", f"自动分析竞品账号未创建任务：待分析账号 {result.get('account_count', 0)} 个，跳过 {len(result.get('skipped', []))} 个")
                return
            analysis_task_id = str(result["task_ids"][0])
            account_ids = [int(item["account_id"]) for item in result.get("accounts", [])]
            with database.connect() as conn:
                log_task(conn, task_id, "info", f"已创建自动账号分析任务 {analysis_task_id}，账号 {len(account_ids)} 个")
            account_actions.run_account_analysis_batch(account_ids, analysis_task_id)
        except Exception as exc:
            with database.connect() as conn:
                log_task(conn, task_id, "error", f"自动分析竞品账号失败：{exc}")

    if auto_lead and task_mode in ("competitor_crawl", "own_account", "demand_content"):
        with database.connect() as conn:
            log_task(conn, task_id, "info", "已开启自动分析线索用户，开始执行 AI 筛选")
        try:
            from app.services import ai_service

            result = ai_service.run_auto_lead_analysis_for_task(task_id)
            with database.connect() as conn:
                log_task(
                    conn,
                    task_id,
                    "info",
                    f"自动线索 AI 分析完成：成功 {result['succeeded']} 个，失败 {result['failed']} 个，自动删除非客户 {result['auto_deleted']} 个",
                )
                if result["errors"]:
                    log_task(conn, task_id, "error", f"自动线索 AI 分析失败明细：{result['errors']}")
        except Exception as exc:
            with database.connect() as conn:
                log_task(conn, task_id, "error", f"自动分析线索用户失败：{exc}")


def _media_crawler_subprocess_env(base_env: dict[str, str], task: dict[str, object]) -> dict[str, str]:
    env = base_env.copy()
    shim_required = False
    if task.get("platform") == "dy":
        env["AI_CUSTOMER_DY_RESILIENT_HTTP"] = "1"
        env["AI_CUSTOMER_DY_DETAIL_SLEEP_SEC"] = _douyin_detail_sleep_seconds()
        shim_required = True
    if (
        task.get("mode") in ("account_analysis", "competitor_crawl", "own_account")
        and task.get("crawler_type") == "creator"
        and task.get("platform") == "dy"
    ):
        limit = max(1, int(task.get("content_count") or 5))
        env["AI_CUSTOMER_DY_CREATOR_VIDEO_LIMIT"] = str(limit)
        shim_required = True
    content_cutoff_ts = _setting_cutoff_ts_seconds("content_cutoff_days")
    if content_cutoff_ts:
        env["AI_CUSTOMER_CONTENT_CUTOFF_TS"] = str(content_cutoff_ts)
        shim_required = True
    comment_cutoff_ts = _setting_cutoff_ts_seconds("comment_cutoff_days")
    if comment_cutoff_ts:
        env["AI_CUSTOMER_COMMENT_CUTOFF_TS"] = str(comment_cutoff_ts)
        shim_required = True
    if shim_required:
        shim_dir = Path(__file__).resolve().parents[1] / "mediacrawler_shims"
        existing_pythonpath = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = str(shim_dir) if not existing_pythonpath else f"{shim_dir}{os.pathsep}{existing_pythonpath}"
    return env


def _setting_cutoff_ts_seconds(setting_key: str) -> int | None:
    with database.connect() as conn:
        try:
            days = int(database.get_setting(conn, setting_key, "0") or "0")
        except (TypeError, ValueError):
            days = 0
    if days <= 0:
        return None
    return int(time.time()) - days * 86400


def _douyin_detail_sleep_seconds() -> str:
    with database.connect() as conn:
        raw_value = database.get_setting(conn, "douyin_detail_sleep_seconds", "2")
    try:
        value = float(raw_value)
    except (TypeError, ValueError):
        value = 2.0
    value = min(max(value, 0.0), 10.0)
    return f"{value:g}"


def _fail_task(task_id: str, error: str) -> None:
    with database.connect() as conn:
        log_task(conn, task_id, "error", error)
        conn.execute(
            """
            UPDATE crawl_jobs
            SET status = 'failed', error = ?, finished_at = datetime('now', 'localtime'), updated_at = datetime('now', 'localtime')
            WHERE id = ?
            """,
            (error, task_id),
        )


def _terminate_process_tree(process: subprocess.Popen[str]) -> None:
    # Kill the explicit subprocess tree so uv/python/browser children do not keep running.
    if process.poll() is not None:
        return
    if platform.system().lower() == "windows":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return
    process.terminate()


def _should_stop_account_analysis(task: dict[str, object], message: str, progress: dict[str, Any]) -> bool:
    if task.get("mode") != "account_analysis":
        return False
    if "," in str(task.get("creator_id") or ""):
        return False
    if any(marker in message for marker in ACCOUNT_ANALYSIS_CREATOR_LOG_MARKERS):
        progress["creator"] = True
    if any(marker in message for marker in ACCOUNT_ANALYSIS_AWEME_LOG_MARKERS):
        progress["contents"] = int(progress.get("contents") or 0) + 1
    target_count = max(1, int(task.get("content_count") or 5))
    return bool(progress.get("creator")) and int(progress.get("contents") or 0) >= target_count


def recover_interrupted_running_tasks() -> int:
    """Mark tasks left running by a backend restart as failed."""
    with database.connect() as conn:
        rows = conn.execute("SELECT id FROM crawl_jobs WHERE status = 'running'").fetchall()
        for row in rows:
            task_id = str(row["id"])
            message = "服务启动时发现任务仍处于 running；上一次后端可能重启或中断，任务已标记失败，请重新执行"
            log_task(conn, task_id, "error", message)
            conn.execute(
                """
                UPDATE crawl_jobs
                SET status = 'failed', error = ?, finished_at = datetime('now', 'localtime'), updated_at = datetime('now', 'localtime')
                WHERE id = ?
                """,
                (message, task_id),
            )
    return len(rows)


def cancel_task(task_id: str) -> dict[str, object]:
    process = RUNNING_PROCESSES.get(task_id)
    if process and process.poll() is None:
        _terminate_process_tree(process)
    with database.connect() as conn:
        log_task(conn, task_id, "warning", "用户请求取消任务")
        conn.execute(
            """
            UPDATE crawl_jobs
            SET status = 'cancelled', finished_at = datetime('now', 'localtime'), updated_at = datetime('now', 'localtime')
            WHERE id = ?
            """,
            (task_id,),
        )
    return get_task(task_id) or {}


def archive_task(task_id: str) -> dict[str, object]:
    with database.connect() as conn:
        conn.execute("UPDATE crawl_jobs SET archived = 1, updated_at = datetime('now', 'localtime') WHERE id = ?", (task_id,))
        log_task(conn, task_id, "info", "任务已归档")
    return get_task(task_id) or {}
