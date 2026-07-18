"""引流设置、素材文件、依赖检查与拓客来源数据。"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

from app import database
from app.schemas import TrafficSettingsUpdate


# 设置默认值只补齐缺失项，不覆盖用户保存的数据。
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
TRAFFIC_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
TRAFFIC_IMAGE_MAX_BYTES = 8 * 1024 * 1024
INSTALL_TIMEOUT_SECONDS = 300


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
    # 素材属于本机运行数据，由备份模块负责保护。
    path.write_bytes(content)
    return {"path": str(path), "preview_url": image_preview_url(path)}


def material_image_path(name: str) -> Path:
    path = TRAFFIC_IMAGE_DIR / Path(name).name
    if path.suffix.lower() not in TRAFFIC_IMAGE_EXTENSIONS or not path.exists():
        raise ValueError("图片不存在")
    return path


def image_preview_url(path: Path) -> str:
    try:
        if path.resolve().parent != TRAFFIC_IMAGE_DIR.resolve():
            return ""
    except OSError:
        return ""
    return f"/api/traffic/material-images/{path.name}"


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


def source_keywords() -> list[dict[str, Any]]:
    # 关键词直接来自拓客工作台已入库内容，计划页只负责选择。
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


def _format_material_image(row: Any) -> dict[str, Any]:
    data = database.row_to_dict(row) or {}
    preview_url = image_preview_url(Path(str(data.get("path") or "")))
    if preview_url:
        data["preview_url"] = preview_url
    return data


def _env_item(ok: bool, message: str) -> dict[str, Any]:
    return {"ok": ok, "message": message}


def _check_cloakbrowser_binary() -> dict[str, Any]:
    if getattr(sys, "frozen", False):
        bundled_binary = Path(os.environ.get("CLOAKBROWSER_BINARY_PATH", ""))
        if bundled_binary.is_file():
            return _env_item(True, str(bundled_binary))
        return _env_item(False, "打包内置的 CloakBrowser 浏览器不存在")
    try:
        # 只执行 CloakBrowser 自检，避免直接启动浏览器进程。
        result = subprocess.run(
            [sys.executable, "-m", "cloakbrowser", "doctor", "--quick", "--json"],
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
