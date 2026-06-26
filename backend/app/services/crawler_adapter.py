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
}

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


def get_task(task_id: str) -> dict[str, object] | None:
    with database.connect() as conn:
        row = conn.execute("SELECT * FROM crawl_jobs WHERE id = ?", (task_id,)).fetchone()
        return database.row_to_dict(row)


def list_tasks(include_archived: bool = False) -> list[dict[str, object]]:
    with database.connect() as conn:
        where = "" if include_archived else "WHERE archived = 0"
        rows = conn.execute(f"SELECT * FROM crawl_jobs {where} ORDER BY created_at DESC").fetchall()
        return database.rows_to_dicts(rows)


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
