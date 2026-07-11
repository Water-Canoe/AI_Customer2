from __future__ import annotations

import os
from pathlib import Path

from app import database


BASE_DIR = Path(__file__).resolve().parent
RUNTIME_DIR = database.get_data_root() / "social_publish"
LOCAL_CHROME_PATH = os.getenv("AI_CUSTOMER_PUBLISH_CHROME_PATH", "").strip()
LOCAL_CHROME_HEADLESS = True
DEBUG_MODE = False


def ensure_runtime_dirs() -> None:
    """创建发布器需要的稳定运行目录。"""
    for name in ("accounts", "qrcode", "tasks", "logs"):
        (RUNTIME_DIR / name).mkdir(parents=True, exist_ok=True)


ensure_runtime_dirs()

