from __future__ import annotations

import os
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

import uvicorn


def app_dir() -> Path:
    """Return the folder that owns the packaged runtime files."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def choose_port(default: int = 8000) -> int:
    """Use 8000 when free, otherwise find a nearby local port."""
    for port in [default, *range(8010, 8030)]:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.2)
            if sock.connect_ex(("127.0.0.1", port)) != 0:
                return port
    raise RuntimeError("No free local port found between 8000 and 8029")


def open_browser_later(url: str) -> None:
    """Open the browser after uvicorn has a short moment to start."""
    time.sleep(1.5)
    webbrowser.open(url)


base_dir = app_dir()
runtime_dir = base_dir / "runtime"
frontend_dist = base_dir / "frontend_dist"
media_crawler_dir = base_dir / "MediaCrawler"

runtime_dir.mkdir(parents=True, exist_ok=True)

os.environ.setdefault("AI_CUSTOMER_DB", str(runtime_dir / "ai_customer.sqlite3"))
if frontend_dist.exists():
    os.environ.setdefault("AI_CUSTOMER_FRONTEND_DIST", str(frontend_dist))
if media_crawler_dir.exists():
    os.environ.setdefault("AI_CUSTOMER_MEDIA_CRAWLER_PATH", str(media_crawler_dir))

from app.main import app  # noqa: E402


if __name__ == "__main__":
    port = choose_port()
    url = f"http://127.0.0.1:{port}"
    threading.Thread(target=open_browser_later, args=(url,), daemon=True).start()
    print(f"AI拓客工具已启动：{url}")
    print("关闭这个窗口即可停止本地服务。")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")
