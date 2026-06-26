from __future__ import annotations

import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable

from app import database
from app.schemas import TaskCreate
from app.services.importer import import_for_task


PLATFORM_LABELS = {"dy": "抖音", "xhs": "小红书", "ks": "快手"}
MODE_LABELS = {
    "competitor_discovery": "竞品账号采集",
    "competitor_crawl": "竞品账号爬取",
    "demand_content": "找需求内容",
    "own_account": "自家账号互动",
    "profile_enrichment": "账号资料补全",
}
PROFILE_ENRICHMENT_PLATFORMS = {"dy", "xhs"}

RUNNING_PROCESSES: dict[str, subprocess.Popen[str]] = {}


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


def create_profile_enrichment_task(account_id: int) -> dict[str, object]:
    with database.connect() as conn:
        row = conn.execute("SELECT * FROM user_accounts WHERE id = ?", (account_id,)).fetchone()
        if not row:
            raise ValueError("账号不存在，无法补资料")
        account = database.row_to_dict(row)
        platform = str(account.get("platform") or "")
        if platform not in PROFILE_ENRICHMENT_PLATFORMS:
            raise ValueError("MediaCrawler SQLite 目前仅支持抖音/小红书账号资料补全，快手主页资料不会写入 SQLite")
        creator_id = _account_profile_identifier(account)
        if not creator_id:
            raise ValueError("账号缺少主页链接或平台ID，无法补资料")
        login_type = database.get_setting(conn, "login_type", "qrcode")
        headless = database.get_setting(conn, "headless", "false") == "true"

    nickname = str(account.get("nickname") or account_id)
    task = create_task(
        TaskCreate(
            name=f"账号资料补全-{nickname}",
            mode="profile_enrichment",
            platform=platform,
            login_type=login_type if login_type in ("qrcode", "phone", "cookie") else "qrcode",
            creator_id=creator_id,
            content_count=1,
            comment_count=0,
            collect_comments=False,
            collect_sub_comments=False,
            max_concurrency=1,
            headless=headless,
            execute_crawler=True,
        )
    )
    with database.connect() as conn:
        log_task(conn, str(task["id"]), "info", f"补资料来源账号：{nickname}（ID={account_id}）")
    return task


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
        process = subprocess.Popen(
            command,
            cwd=media_path,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
        )
    except Exception as exc:
        _fail_task(task_id, f"启动 MediaCrawler 失败：{exc}")
        return

    RUNNING_PROCESSES[task_id] = process
    with database.connect() as conn:
        conn.execute("UPDATE crawl_jobs SET process_id = ? WHERE id = ?", (process.pid, task_id))
        log_task(conn, task_id, "info", f"MediaCrawler 进程已启动，PID={process.pid}")

    assert process.stdout is not None
    for line in process.stdout:
        message = line.rstrip()
        if not message:
            continue
        with database.connect() as conn:
            log_task(conn, task_id, "info", message)
        if after_log:
            after_log(message)

    return_code = process.wait()
    RUNNING_PROCESSES.pop(task_id, None)
    if return_code != 0:
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


def cancel_task(task_id: str) -> dict[str, object]:
    process = RUNNING_PROCESSES.get(task_id)
    if process and process.poll() is None:
        process.terminate()
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
