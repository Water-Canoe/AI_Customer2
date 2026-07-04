from __future__ import annotations

import hashlib
import importlib.util
import json
import random
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

from app import database
from app.schemas import TrafficPlanCreate, TrafficSettingsUpdate


# 引流设置默认值只覆盖缺失项，避免覆盖用户在设置页保存的配置。
TRAFFIC_SETTING_KEYS = {
    "traffic_round_video_limit": "5",
    "traffic_daily_action_limit": "50",
    "traffic_min_watch_seconds": "3",
    "traffic_max_watch_seconds": "8",
    "traffic_author_cooldown_hours": "24",
    "traffic_stop_after_failures": "3",
}

TRAFFIC_IMAGE_DIR = database.BACKEND_ROOT / "runtime" / "traffic_images"
TRAFFIC_DOUYIN_PROFILE_DIR = database.WORKSPACE_ROOT / "runtime" / "traffic_douyin_profile"
TRAFFIC_LAST_VIDEO_URL_KEY = "traffic_last_douyin_video_url"
TRAFFIC_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
TRAFFIC_IMAGE_MAX_BYTES = 8 * 1024 * 1024
INSTALL_TIMEOUT_SECONDS = 300
DOUYIN_LOGIN_PROCESS: subprocess.Popen[Any] | None = None


# 停机异常同时携带用户提示和技术详情，日志页面按这两个层级展示。
@dataclass
class TrafficStop(Exception):
    message: str
    reason: str
    suggestion: str
    phase: str = "stop"
    details: dict[str, Any] | None = None


def list_plans() -> list[dict[str, Any]]:
    with database.connect() as conn:
        rows = conn.execute("SELECT * FROM traffic_plans ORDER BY created_at DESC").fetchall()
    return [_format_plan(row) for row in rows]


def get_plan(plan_id: str) -> dict[str, Any] | None:
    with database.connect() as conn:
        row = conn.execute("SELECT * FROM traffic_plans WHERE id = ?", (plan_id,)).fetchone()
    return _format_plan(row) if row else None


def create_plan(payload: TrafficPlanCreate) -> dict[str, Any]:
    plan = _normalize_plan(payload)
    plan_id = uuid.uuid4().hex
    with database.connect() as conn:
        conn.execute(
            """
            INSERT INTO traffic_plans(
                id, name, platform, source_mode, source_value,
                action_like, action_collect, action_follow,
                action_comment_text, action_comment_image, enabled
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                int(plan["enabled"]),
            ),
        )
    created = get_plan(plan_id)
    assert created is not None
    return created


def update_plan(plan_id: str, payload: TrafficPlanCreate) -> dict[str, Any]:
    plan = _normalize_plan(payload)
    with database.connect() as conn:
        row = conn.execute("SELECT id FROM traffic_plans WHERE id = ?", (plan_id,)).fetchone()
        if not row:
            raise ValueError("引流计划不存在")
        conn.execute(
            """
            UPDATE traffic_plans
            SET name = ?, platform = ?, source_mode = ?, source_value = ?,
                action_like = ?, action_collect = ?, action_follow = ?,
                action_comment_text = ?, action_comment_image = ?, enabled = ?,
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
                int(plan["enabled"]),
                plan_id,
            ),
        )
    updated = get_plan(plan_id)
    assert updated is not None
    return updated


def delete_plan(plan_id: str) -> dict[str, Any]:
    with database.connect() as conn:
        conn.execute("DELETE FROM traffic_plans WHERE id = ?", (plan_id,))
    return {"deleted": True}


def create_run(plan_id: str) -> dict[str, Any]:
    plan = get_plan(plan_id)
    if not plan:
        raise ValueError("引流计划不存在")
    _validate_run_plan(plan)
    run_id = uuid.uuid4().hex
    with database.connect() as conn:
        _insert_run(conn, run_id, plan_id)
        _insert_log(
            conn,
            run_id,
            "info",
            "probe",
            "批次已创建，等待打开抖音。",
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


def list_runs() -> list[dict[str, Any]]:
    with database.connect() as conn:
        rows = conn.execute(
            """
            SELECT r.*, p.name AS plan_name, p.platform, p.source_mode
            FROM traffic_runs r
            JOIN traffic_plans p ON p.id = r.plan_id
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
    return run


def stop_run(run_id: str) -> dict[str, Any]:
    with database.connect() as conn:
        row = conn.execute("SELECT status FROM traffic_runs WHERE id = ?", (run_id,)).fetchone()
        if not row:
            raise ValueError("引流批次不存在")
        conn.execute(
            """
            UPDATE traffic_runs
            SET stop_requested = 1, updated_at = datetime('now', 'localtime')
            WHERE id = ?
            """,
            (run_id,),
        )
        if row["status"] not in {"running", "queued"}:
            return get_run(run_id) or {}
        _insert_log(
            conn,
            run_id,
            "warning",
            "stop",
            "已收到停止请求，当前视频处理完会停止。",
            "用户手动停止任务",
            "无需重复点击，稍等几秒后查看最终状态。",
            {},
        )
    return get_run(run_id) or {}


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
        clauses.append("(video_desc LIKE ? OR author_name LIKE ? OR comment_text LIKE ?)")
        like = f"%{query}%"
        params.extend([like, like, like])
    if status:
        clauses.append("status = ?")
        params.append(status)
    if action:
        clauses.append("actions LIKE ?")
        params.append(f"%{action}%")
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with database.connect() as conn:
        total = conn.execute(f"SELECT COUNT(*) AS c FROM traffic_records {where}", params).fetchone()["c"]
        rows = conn.execute(
            f"""
            SELECT * FROM traffic_records
            {where}
            ORDER BY created_at DESC, id DESC
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
        counts = {
            "records": conn.execute("SELECT COUNT(*) AS c FROM traffic_records").fetchone()["c"],
            "runs": conn.execute("SELECT COUNT(*) AS c FROM traffic_runs").fetchone()["c"],
        }
        conn.execute("DELETE FROM traffic_dedup_ledger")
        conn.execute("DELETE FROM traffic_records")
        conn.execute("DELETE FROM traffic_action_logs")
        conn.execute("DELETE FROM traffic_run_items")
        conn.execute("DELETE FROM traffic_runs")
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
        for text in _clean_lines(payload.texts):
            conn.execute("INSERT INTO traffic_material_texts(text, enabled) VALUES(?, 1)", (text,))
        for image in _clean_lines(payload.images):
            conn.execute("INSERT INTO traffic_material_images(path, enabled) VALUES(?, 1)", (image,))
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
    chromium = _check_chromium() if playwright_ok else _env_item(False, "需要先安装 Playwright Python 包")
    TRAFFIC_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    image_dir_ok = TRAFFIC_IMAGE_DIR.exists() and TRAFFIC_IMAGE_DIR.is_dir()
    items = {
        "python": _env_item(True, str(sys.executable)),
        "playwright": _env_item(playwright_ok, "已安装" if playwright_ok else "缺少 Python Playwright 依赖"),
        "chromium": chromium,
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
    steps = [
        _run_install_step([sys.executable, "-m", "pip", "install", "-r", str(database.BACKEND_ROOT / "requirements.txt")]),
        _run_install_step([sys.executable, "-m", "playwright", "install", "chromium"]),
    ]
    return {"ok": all(step["ok"] for step in steps), "steps": steps, "check": environment_check()}


def open_douyin_login_window() -> dict[str, Any]:
    global DOUYIN_LOGIN_PROCESS
    if importlib.util.find_spec("playwright") is None:
        raise ValueError("缺少 Python Playwright 依赖，请先在引流设置执行环境检查并自动安装")
    if DOUYIN_LOGIN_PROCESS and DOUYIN_LOGIN_PROCESS.poll() is None:
        return {"ok": True, "message": "抖音登录窗口已经打开。扫码后请点开任意视频，关闭窗口，再启动批次。", "profile_dir": str(TRAFFIC_DOUYIN_PROFILE_DIR)}

    runtime_dir = database.BACKEND_ROOT / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    log_path = runtime_dir / "traffic_douyin_login.log"
    command = [sys.executable, "-c", "from app.services.traffic_workbench import _hold_douyin_login_window; _hold_douyin_login_window()"]
    with log_path.open("a", encoding="utf-8") as log_file:
        # ponytail: 独立登录进程即可，后续需要托盘控制时再做进程管理页。
        DOUYIN_LOGIN_PROCESS = subprocess.Popen(
            command,
            cwd=str(database.BACKEND_ROOT),
            stdout=log_file,
            stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    time.sleep(1)
    if DOUYIN_LOGIN_PROCESS.poll() is not None:
        raise ValueError(f"抖音登录窗口启动失败，请先检查环境。日志：{log_path}")
    return {"ok": True, "message": "抖音登录窗口已打开。扫码后请点开任意视频，关闭窗口，再启动批次。", "profile_dir": str(TRAFFIC_DOUYIN_PROFILE_DIR)}


def source_keywords() -> list[dict[str, Any]]:
    # 关键词直接来自拓客工作台已入库内容，计划页只负责点击填入。
    with database.connect() as conn:
        rows = conn.execute(
            """
            SELECT source_keyword AS keyword, COUNT(*) AS content_count
            FROM contents
            WHERE source_keyword <> ''
            GROUP BY source_keyword
            ORDER BY content_count DESC, keyword
            """
        ).fetchall()
    return database.rows_to_dicts(rows)


def source_competitor_videos(limit: int = 100) -> list[dict[str, Any]]:
    with database.connect() as conn:
        rows = conn.execute(
            """
            SELECT
                c.id, c.content_id, c.title, c.description, c.content_url,
                c.like_count, c.comment_count, u.nickname AS author_name
            FROM contents c
            LEFT JOIN user_accounts u ON u.id = c.author_account_id
            WHERE u.competitor_status = '竞品'
            ORDER BY c.updated_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return database.rows_to_dicts(rows)


def run_traffic_run(run_id: str) -> None:
    # 后台任务只写批次状态和日志，前端通过执行监控轮询读取。
    run = get_run(run_id)
    if not run:
        return
    plan = get_plan(str(run["plan_id"]))
    if not plan:
        return
    _mark_run_running(run_id)
    try:
        _run_with_playwright(run_id, plan)
        _finish_run(
            run_id,
            "completed",
            "已达到本轮上限，任务已自动完成。",
            "本轮执行已完成",
            "可以在操作记录查看每个视频的处理结果。",
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


def _run_with_playwright(run_id: str, plan: dict[str, Any]) -> None:
    # 真实浏览器自动化集中在这里，所有异常都转为用户可读停机原因。
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise TrafficStop(
            "缺少 Playwright 依赖，任务已停止。",
            "本地后端没有安装浏览器自动化依赖。",
            "请先在后端环境安装 requirements.txt，并执行 Playwright 浏览器安装。",
            "probe",
            {"error": str(exc)},
        ) from exc

    settings = get_settings()["values"]
    limit = _int_setting(settings, "traffic_round_video_limit", 5, 1, 200)
    min_watch = _int_setting(settings, "traffic_min_watch_seconds", 3, 0, 120)
    max_watch = _int_setting(settings, "traffic_max_watch_seconds", 8, min_watch, 300)
    stop_after_failures = _int_setting(settings, "traffic_stop_after_failures", 3, 1, 10)
    author_cooldown_hours = _int_setting(settings, "traffic_author_cooldown_hours", 24, 0, 720)
    failure_count = 0
    project_author_keys: set[str] = set()

    with sync_playwright() as playwright:
        context = _launch_context(playwright, TRAFFIC_DOUYIN_PROFILE_DIR)
        page = context.pages[0] if context.pages else context.new_page()
        video_cache = _setup_video_data_cache(page)
        try:
            target_url = _target_url(plan)
            _append_log(run_id, "info", "login", "正在打开抖音页面。", "准备执行引流批次", "如果弹出登录，请先到引流设置完成扫码登录。", {"url": target_url})
            page.goto(target_url, wait_until="domcontentloaded", timeout=60_000)
            page.wait_for_timeout(4000)
            _ensure_page_ready(page)
            navigation_source = _navigate_to_executable_video(page, run_id, plan["source_mode"], video_cache, author_cooldown_hours, project_author_keys)
            use_project_queue = navigation_source == "project"
            for index in range(limit):
                _raise_if_stop_requested(run_id)
                video = _read_active_video(page, video_cache)
                if not video["video_id"]:
                    is_jingxuan = "douyin.com/jingxuan" in page.url
                    raise TrafficStop(
                        "连续没有找到可执行的视频，任务已停止。",
                        "当前停留在抖音精选页，未进入具体视频。" if is_jingxuan else "当前页面没有活跃视频节点。",
                        "请到引流设置打开抖音登录窗口，扫码后点开任意视频，关闭窗口，再重新启动批次。" if is_jingxuan else "请确认抖音已经进入推荐流或视频详情页，再重新启动任务。",
                        "probe",
                        {"url": page.url},
                    )
                if not _is_regular_video(video):
                    _append_log(run_id, "warning", "browse", "当前不是常规视频，已跳过。", "可能是直播、图文或广告内容", "系统会尝试切换到下一个视频。", {"video_id": video["video_id"], "aweme_type": video.get("aweme_type")})
                    _record_video(run_id, plan, video, ["跳过"], "", "", "skipped")
                    if index < limit - 1 and not _next_video(page, run_id, video["video_id"], video_cache, use_project_queue, author_cooldown_hours, project_author_keys):
                        raise TrafficStop(
                            "连续 3 次没有切换到新视频，任务已停止。可能是页面没有进入推荐流。",
                            "翻页后视频 ID 没有变化。",
                            "请到引流设置重新打开抖音登录窗口，进入任意视频后再启动。",
                            "advance",
                            {"video_id": video["video_id"], "url": page.url},
                        )
                    continue
                watch_seconds = random.randint(min_watch, max_watch)
                _append_log(run_id, "info", "browse", f"正在浏览视频：{video['video_desc'][:36] or video['video_id']}。", "按设置随机停留", f"本次停留约 {watch_seconds} 秒。", {"video_id": video["video_id"]})
                page.wait_for_timeout(watch_seconds * 1000)
                done_actions, comment_text, image_path = _execute_actions(run_id, plan, page, video)
                if plan["actions"] and not done_actions:
                    failure_count += 1
                    _append_log(run_id, "warning", "stop", f"当前视频没有完成任何动作，连续失败 {failure_count} 次。", "动作没有确认成功", "系统会继续尝试下一个视频，连续失败过多会自动停止。", {"video_id": video["video_id"]})
                else:
                    failure_count = 0
                _record_video(run_id, plan, video, done_actions or ["仅浏览"], comment_text, image_path, "browsed" if not plan["actions"] else "done")
                if failure_count >= stop_after_failures:
                    raise TrafficStop(
                        f"连续 {failure_count} 次动作没有确认成功，任务已停止。",
                        "连续动作失败次数达到停机阈值。",
                        "请检查抖音页面是否改版、账号是否受限，或先降低动作频率。",
                        "stop",
                        {"failure_count": failure_count},
                    )
                if index < limit - 1 and not _next_video(page, run_id, video["video_id"], video_cache, use_project_queue, author_cooldown_hours, project_author_keys):
                    raise TrafficStop(
                        "连续 3 次没有切换到新视频，任务已停止。可能是页面没有进入推荐流。",
                        "翻页后视频 ID 没有变化。",
                        "请到引流设置重新打开抖音并确认推荐流可以正常切换。",
                        "advance",
                        {"video_id": video["video_id"], "url": page.url},
                    )
        except PlaywrightTimeoutError as exc:
            raise TrafficStop(
                "抖音页面加载超时，任务已停止。",
                "浏览器等待页面响应超时。",
                "请检查网络和抖音页面是否能手动打开。",
                "probe",
                {"error": str(exc)},
            ) from exc
        finally:
            context.close()


def _hold_douyin_login_window() -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        context = _launch_context(playwright, TRAFFIC_DOUYIN_PROFILE_DIR)
        page = context.pages[0] if context.pages else context.new_page()
        video_cache = _setup_video_data_cache(page)
        page.goto("https://www.douyin.com/?recommend=1", wait_until="domcontentloaded", timeout=60_000)
        while True:
            video = _read_active_video(page, video_cache)
            if video["video_id"]:
                _save_last_douyin_video_url(video["video_url"] if _is_douyin_video_url(video["video_url"]) else f"https://www.douyin.com/video/{video['video_id']}")
            else:
                _save_last_douyin_video_url(page.url)
            page.wait_for_timeout(1000)


def _launch_context(playwright: Any, profile_dir: Path) -> Any:
    profile_dir.mkdir(parents=True, exist_ok=True)
    failures = []
    for channel in ("chrome", "msedge", None):
        try:
            return playwright.chromium.launch_persistent_context(
                str(profile_dir),
                channel=channel,
                headless=False,
                viewport={"width": 1440, "height": 900},
                locale="zh-CN",
            )
        except Exception as exc:
            failures.append(f"{channel or 'bundled'}: {exc}")
    raise TrafficStop(
        "没有找到可用的 Chrome 或 Edge，任务已停止。",
        "Playwright 无法启动浏览器。",
        "请安装 Chrome/Edge，或执行 Playwright 浏览器安装后重试。",
        "probe",
        {"failures": failures},
    )


def _target_url(plan: dict[str, Any]) -> str:
    if plan["source_mode"] == "search_keyword" and plan["source_value"]:
        return f"https://www.douyin.com/search/{quote(plan['source_value'])}"
    if plan["source_mode"] == "competitor_videos" and plan["source_value"].startswith("http"):
        return plan["source_value"]
    return "https://www.douyin.com/?recommend=1"


def _ensure_page_ready(page: Any) -> None:
    state = page.evaluate(
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
    )
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


def _navigate_to_executable_video(page: Any, run_id: str, source_mode: str, video_cache: dict[str, Any] | None = None, cooldown_hours: int = 24, project_author_keys: set[str] | None = None) -> str:
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
    if "douyin.com/jingxuan" in page.url:
        project = _random_project_video_candidate(cooldown_hours, project_author_keys)
        if project:
            _remember_project_author(project_author_keys, project)
            _goto_video_candidate(page, run_id, project["url"], "已从项目库随机跳转一个视频。")
            return "project"
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
    for _ in range(4):
        candidates = _visible_video_candidates(page)
        random.shuffle(candidates)
        for candidate in candidates[:6]:
            if _click_video_candidate(page, run_id, candidate, "已从抖音首页随机点击一个视频。") and (
                _read_active_video(page, video_cache)["video_id"] or _is_douyin_video_url(page.url)
            ):
                return True
        page.mouse.wheel(0, random.randint(500, 1100))
        page.wait_for_timeout(1200)
    return False


def _click_video_candidate(page: Any, run_id: str, candidate: dict[str, Any], message: str) -> bool:
    # ponytail: 随机引流必须来自当前抖音页面，不能退回项目库视频。
    url = str(candidate.get("href") or page.url)
    _append_log(run_id, "info", "probe", message, "当前页面不是可执行视频流", "系统会进入具体视频后继续执行。", {"from_url": page.url, "target_url": url})
    clicked = False
    if isinstance(candidate.get("x"), (int, float)) and isinstance(candidate.get("y"), (int, float)):
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
    if not clicked:
        page.goto(url, wait_until="domcontentloaded", timeout=60_000)
    page.wait_for_timeout(3000)
    _ensure_page_ready(page)
    return True


def _next_video(page: Any, run_id: str, previous_video_id: str, video_cache: dict[str, Any] | None, use_project_queue: bool, cooldown_hours: int, project_author_keys: set[str] | None = None) -> bool:
    if use_project_queue:
        project = _random_project_video_candidate(cooldown_hours, project_author_keys)
        if not project:
            return False
        _remember_project_author(project_author_keys, project)
        _goto_video_candidate(page, run_id, project["url"], "已按作者冷却随机切换到项目库另一个视频。")
        next_id = _read_active_video(page, video_cache)["video_id"]
        return bool(next_id and next_id != previous_video_id)
    return _advance_video(page, previous_video_id, video_cache)


def _goto_video_candidate(page: Any, run_id: str, url: str, message: str) -> None:
    # ponytail: 先复用已有视频入口；后续需要纯随机推荐再接入接口队列。
    _append_log(run_id, "info", "probe", message, "当前页面不是可执行视频流", "系统会进入具体视频后继续执行。", {"from_url": page.url, "target_url": url})
    page.goto(url, wait_until="domcontentloaded", timeout=60_000)
    page.wait_for_timeout(3000)
    _ensure_page_ready(page)


def _random_project_video_url() -> str:
    candidate = _random_project_video_candidate(0)
    return candidate["url"] if candidate else ""


def _random_project_video_candidate(cooldown_hours: int, exclude_author_keys: set[str] | None = None) -> dict[str, str] | None:
    modifier = f"-{max(cooldown_hours, 0)} hours"
    with database.connect() as conn:
        rows = conn.execute(
            """
            SELECT c.content_url, c.content_id,
                   COALESCE(u.platform_user_id, '') AS author_id,
                   COALESCE(u.nickname, '') AS author_name
            FROM contents c
            LEFT JOIN user_accounts u ON u.id = c.author_account_id
            WHERE c.platform = 'dy'
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
                    OR (u.platform_user_id <> '' AND tr.author_id = u.platform_user_id)
                    OR (u.nickname <> '' AND tr.author_name = u.nickname)
                  )
              )
            ORDER BY RANDOM()
            LIMIT 20
            """,
            (modifier,),
        ).fetchall()
    for row in rows:
        candidate = _project_video_candidate_from_row(row)
        if candidate and _project_author_key(candidate) not in (exclude_author_keys or set()):
            return candidate
    return None


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
    }


def _is_regular_video(video: dict[str, Any]) -> bool:
    return video.get("aweme_type") in (None, "", 0)


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _execute_actions(run_id: str, plan: dict[str, Any], page: Any, video: dict[str, Any]) -> tuple[list[str], str, str]:
    if not plan["actions"]:
        return [], "", ""
    done: list[str] = []
    comment_text = ""
    image_path = ""
    if "like" in plan["actions"] and _execute_click_action(run_id, page, video, "like", '[data-e2e="video-player-digg"]', "点赞视频"):
        done.append("点赞视频")
    if "collect" in plan["actions"] and _execute_click_action(run_id, page, video, "collect", '[data-e2e="video-player-collect"]', "收藏视频"):
        done.append("收藏视频")
    if "follow" in plan["actions"] and _execute_follow(run_id, page, video):
        done.append("关注作者")
    if "comment_text" in plan["actions"] or "comment_image" in plan["actions"]:
        comment_text = _pick_text() if "comment_text" in plan["actions"] else ""
        image_path = _pick_image() if "comment_image" in plan["actions"] else ""
        if _execute_comment(run_id, page, video, comment_text, image_path):
            done.append("评论")
    return done, comment_text, image_path


def _execute_click_action(run_id: str, page: Any, video: dict[str, Any], action: str, selector: str, label: str) -> bool:
    if _dedup_exists(video, action, ""):
        _append_log(run_id, "warning", action, f"这个视频已经执行过{label}，本次跳过。", "防重复命中", "系统已自动跳过，不需要处理。", {"video_id": video["video_id"]})
        return False
    if action == "like":
        page.keyboard.press("z")
        page.wait_for_timeout(700)
        _append_log(run_id, "success", action, f"{label}已执行。", "已使用抖音快捷键触发", "可以在操作记录中查看本视频结果。", {"video_id": video["video_id"]})
        _insert_dedup(video, action, "", "done")
        return True
    locator = page.locator(selector).first
    if not locator.is_visible(timeout=1500):
        _append_log(run_id, "warning", action, f"没有找到{label}按钮，已跳过当前动作。", "页面没有对应按钮", "如果频繁出现，可能是抖音页面改版或当前视频不支持该动作。", {"selector": selector})
        return False
    locator.click(timeout=5000)
    page.wait_for_timeout(1200)
    _append_log(run_id, "success", action, f"{label}已执行。", "动作已点击", "可以在操作记录中查看本视频结果。", {"video_id": video["video_id"]})
    _insert_dedup(video, action, "", "done")
    return True


def _execute_follow(run_id: str, page: Any, video: dict[str, Any]) -> bool:
    if _dedup_exists(video, "follow", ""):
        _append_log(run_id, "warning", "follow", "这个作者已经关注过或处理过，本次跳过。", "防重复命中", "系统已自动跳过，不需要处理。", {"video_id": video["video_id"]})
        return False
    button = page.get_by_text("关注", exact=False).first
    if not button.is_visible(timeout=1500):
        _append_log(run_id, "warning", "follow", "没有找到关注按钮，已跳过关注。", "当前视频可能是广告、直播或已关注作者", "如果计划包含关注，系统会继续处理其它动作。", {"video_id": video["video_id"]})
        return False
    button.click(timeout=5000)
    page.wait_for_timeout(1200)
    _append_log(run_id, "success", "follow", "已尝试关注作者。", "关注动作已点击", "如果账号已关注或平台限制，记录里会保留本次尝试。", {"video_id": video["video_id"]})
    _insert_dedup(video, "follow", "", "done")
    return True


def _execute_comment(run_id: str, page: Any, video: dict[str, Any], text: str, image_path: str) -> bool:
    content_hash = _hash_text(f"{text}|{image_path}")
    if _dedup_exists(video, "comment", content_hash):
        _append_log(run_id, "warning", "comment", "这个视频已经发送过相同评论，已跳过。", "防重复命中", "系统已自动跳过，不需要处理。", {"video_id": video["video_id"]})
        return False
    page.keyboard.press("x")
    page.wait_for_timeout(1000)
    composer = page.locator(".comment-input-inner-container, textarea, [contenteditable='true']").first
    if not composer.is_visible(timeout=3000):
        _append_log(run_id, "warning", "comment", "没有找到评论输入框，已跳过评论。", "评论区没有打开或当前视频不支持评论", "系统会继续浏览后续视频。", {"video_id": video["video_id"]})
        return False
    composer.click(timeout=5000)
    if text:
        composer.press_sequentially(text, delay=80)
    if image_path:
        chooser_selector = ".commentInput-right-ct > div > span:nth-child(2)"
        if Path(image_path).exists():
            with page.expect_file_chooser(timeout=5000) as chooser:
                page.locator(chooser_selector).first.click(timeout=5000)
            chooser.value.set_files(image_path)
            page.wait_for_timeout(1500)
        else:
            _append_log(run_id, "warning", "comment", "评论图片文件不存在，已取消本次评论。", "图片路径无效", "请到引流设置检查图片路径。", {"image_path": image_path})
            return False
    page.keyboard.press("Enter")
    page.wait_for_timeout(1500)
    _append_log(run_id, "success", "comment", "评论已发送。", "评论动作已提交", "可以在操作记录中查看实际文案和图片。", {"video_id": video["video_id"], "text": text, "image_path": image_path})
    _insert_dedup(video, "comment", content_hash, "done")
    return True


def _advance_video(page: Any, previous_video_id: str, video_cache: dict[str, Any] | None = None) -> bool:
    page.keyboard.press("x")
    page.wait_for_timeout(500)
    for action in ("arrow_down", "wheel", "page_down"):
        if action == "arrow_down":
            page.keyboard.press("ArrowDown")
        elif action == "wheel":
            page.mouse.wheel(0, 1600)
        else:
            page.keyboard.press("PageDown")
        page.wait_for_timeout(1400)
        next_id = _read_active_video(page, video_cache)["video_id"]
        if next_id and next_id != previous_video_id:
            return True
    return False


def _record_video(
    run_id: str,
    plan: dict[str, Any],
    video: dict[str, Any],
    actions: list[str],
    comment_text: str,
    image_path: str,
    status: str,
) -> None:
    # 每个视频都落操作记录，纯刷视频也会以“仅浏览”进入表格。
    with database.connect() as conn:
        conn.execute(
            """
            INSERT INTO traffic_run_items(
                run_id, video_id, video_url, author_id, author_name,
                video_desc, like_count, comment_count, status, actions_done
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            ),
        )
        conn.execute(
            """
            INSERT INTO traffic_records(
                run_id, plan_id, platform, video_id, video_url, video_desc,
                author_id, author_name, like_count, comment_count, actions,
                comment_text, comment_image_path, status
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
    if plan["platform"] != "dy":
        raise ValueError("快手/小红书引流正在开发，当前只能启动抖音计划")
    settings = get_settings()
    if "comment_text" in plan["actions"] and not [item for item in settings["texts"] if item.get("enabled")]:
        raise ValueError("已选择评论文案，但引流设置里还没有可用文案")
    if "comment_image" in plan["actions"] and not [item for item in settings["images"] if item.get("enabled")]:
        raise ValueError("已选择评论图片，但引流设置里还没有可用图片")


def _normalize_plan(payload: TrafficPlanCreate) -> dict[str, Any]:
    name = payload.name.strip() or "引流计划"
    return {
        "name": name,
        "platform": payload.platform,
        "source_mode": payload.source_mode,
        "source_value": payload.source_value.strip(),
        "action_like": payload.action_like,
        "action_collect": payload.action_collect,
        "action_follow": payload.action_follow,
        "action_comment_text": payload.action_comment_text,
        "action_comment_image": payload.action_comment_image,
        "enabled": payload.enabled,
    }


def _format_plan(row: Any) -> dict[str, Any]:
    data = database.row_to_dict(row) or {}
    data["action_like"] = bool(data.get("action_like"))
    data["action_collect"] = bool(data.get("action_collect"))
    data["action_follow"] = bool(data.get("action_follow"))
    data["action_comment_text"] = bool(data.get("action_comment_text"))
    data["action_comment_image"] = bool(data.get("action_comment_image"))
    data["enabled"] = bool(data.get("enabled"))
    data["actions"] = _plan_actions(data)
    data["action_label"] = "、".join(_action_label(action) for action in data["actions"]) or "仅浏览"
    return data


def _format_run(row: Any) -> dict[str, Any]:
    return database.row_to_dict(row) or {}


def _format_record(row: Any) -> dict[str, Any]:
    data = database.row_to_dict(row) or {}
    try:
        data["actions"] = json.loads(data.get("actions") or "[]")
    except json.JSONDecodeError:
        data["actions"] = []
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


def _check_chromium() -> dict[str, Any]:
    try:
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "from playwright.sync_api import sync_playwright\n"
                "with sync_playwright() as p:\n"
                "    browser = p.chromium.launch(headless=True)\n"
                "    browser.close()\n"
                "print('Chromium 可启动')",
            ],
            cwd=str(database.BACKEND_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        return _env_item(False, "Chromium 启动检查超时")
    text = _tail(result.stdout)
    if result.returncode != 0:
        return _env_item(False, text or "Chromium 浏览器内核未安装")
    return _env_item(True, text or "Chromium 浏览器内核可用")


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
        _insert_log(conn, run_id, "info", "probe", "任务已启动，正在检查抖音页面。", "开始执行批次", "请不要关闭浏览器窗口或本地服务。", {})


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
        conn.execute(
            """
            UPDATE traffic_runs
            SET status = ?, stop_reason = ?, stop_suggestion = ?,
                finished_at = datetime('now', 'localtime'),
                updated_at = datetime('now', 'localtime')
            WHERE id = ?
            """,
            (status, reason, suggestion, run_id),
        )
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
    with database.connect() as conn:
        row = conn.execute(
            """
            SELECT id FROM traffic_dedup_ledger
            WHERE platform = 'dy' AND video_id = ? AND author_id = ?
              AND action_type = ? AND content_hash = ?
            """,
            (video.get("video_id", ""), video.get("author_id", ""), action, content_hash),
        ).fetchone()
    return row is not None


def _insert_dedup(video: dict[str, Any], action: str, content_hash: str, status: str) -> None:
    with database.connect() as conn:
        _insert_dedup_with_conn(conn, video, action, content_hash, status, None, None)


def _insert_dedup_with_conn(conn: Any, video: dict[str, Any], action: str, content_hash: str, status: str, run_id: str | None, record_id: int | None) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO traffic_dedup_ledger(platform, video_id, author_id, action_type, content_hash, run_id, record_id, status)
        VALUES('dy', ?, ?, ?, ?, ?, ?, ?)
        """,
        (video.get("video_id", ""), video.get("author_id", ""), action, content_hash, run_id, record_id, status),
    )


def _hash_text(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()


def _clean_lines(values: list[str]) -> list[str]:
    return [str(item).strip() for item in values if str(item).strip()]


def _int_setting(settings: dict[str, Any], key: str, default: int, minimum: int, maximum: int) -> int:
    value = int(str(settings.get(key, default) or default))
    return max(minimum, min(maximum, value))
