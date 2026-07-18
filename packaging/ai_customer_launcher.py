from __future__ import annotations

import json
import logging
import os
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from logging.handlers import RotatingFileHandler
from pathlib import Path

import uvicorn


_INSTANCE_MUTEX: int | None = None


def app_dir() -> Path:
    """Return the folder that owns the packaged runtime files."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def configure_environment(base_dir: Path) -> None:
    """Point the portable app at its data and reusable environment folders."""
    root_dir = base_dir
    data_dir = Path(os.environ.get("AI_CUSTOMER_DATA_DIR", str(root_dir / "data")))
    packaged = bool(getattr(sys, "frozen", False))
    environment_dir = root_dir / "runtime" if packaged else root_dir
    runtime_dir = Path(os.environ.get("AI_CUSTOMER_RUNTIME_DIR", str(environment_dir / "components" if packaged else root_dir / "runtimes")))
    frontend_dist = environment_dir / "frontend_dist" if packaged else base_dir / "frontend_dist"
    media_crawler_dir = environment_dir / "MyCrawler" if packaged else root_dir / "MyCrawler"
    cloakbrowser_binary = environment_dir / "cloakbrowser_browser" / "chrome.exe" if packaged else base_dir / "r" / "cloakbrowser_browser" / "chrome.exe"
    crawler_python = environment_dir / "python" / "python.exe"
    voice_models = environment_dir / "models"

    data_dir.mkdir(parents=True, exist_ok=True)
    runtime_dir.mkdir(parents=True, exist_ok=True)

    os.environ.setdefault("AI_CUSTOMER_DATA_DIR", str(data_dir))
    os.environ.setdefault("AI_CUSTOMER_DB", str(data_dir / "ai_customer.sqlite3"))
    os.environ.setdefault("AI_CUSTOMER_RUNTIME_DIR", str(runtime_dir))
    if packaged:
        os.environ.setdefault("AI_CUSTOMER_VOICE_MODELS_DIR", str(voice_models))
    if frontend_dist.exists():
        os.environ.setdefault("AI_CUSTOMER_FRONTEND_DIST", str(frontend_dist))
    if media_crawler_dir.exists():
        os.environ.setdefault("AI_CUSTOMER_MEDIA_CRAWLER_PATH", str(media_crawler_dir))
        os.environ.setdefault("AI_CUSTOMER_MEDIA_CRAWLER_DB", str(media_crawler_dir / "database" / "sqlite_tables.db"))
    if crawler_python.is_file():
        os.environ.setdefault("AI_CUSTOMER_CRAWLER_PYTHON", str(crawler_python))
    if cloakbrowser_binary.is_file():
        os.environ.setdefault("CLOAKBROWSER_BINARY_PATH", str(cloakbrowser_binary))


def choose_port(default: int = 8000) -> int:
    """Use 8000 when free, otherwise find a nearby local port."""
    for port in [default, *range(8010, 8030)]:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.2)
            if sock.connect_ex(("127.0.0.1", port)) != 0:
                return port
    raise RuntimeError("No free local port found between 8000 and 8029")


def workbench_url_if_ready(port: int, timeout: float = 0.5) -> str:
    url = f"http://127.0.0.1:{port}"
    try:
        with urllib.request.urlopen(f"{url}/api/health", timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError, urllib.error.URLError):
        return ""
    is_packaged_workbench = (
        payload.get("status") == "ok"
        and payload.get("product") == "ai-customer"
        and payload.get("packaged") is True
    )
    return url if is_packaged_workbench else ""


def existing_workbench_url() -> str:
    for port in [8000, *range(8010, 8030)]:
        if url := workbench_url_if_ready(port, 0.2):
            return url
    return ""


def open_browser_when_ready(url: str, timeout_seconds: float = 60.0) -> bool:
    """Open the browser only after FastAPI finishes its startup lifecycle."""
    deadline = time.monotonic() + timeout_seconds
    port = int(url.rsplit(":", 1)[1])
    while time.monotonic() < deadline:
        if workbench_url_if_ready(port):
            webbrowser.open(url)
            return True
        time.sleep(0.25)
    _show_error("本地服务启动超时，请关闭软件后重试。详细原因已写入 data/logs/app.log。")
    return False


def configure_persistent_logging(data_dir: Path) -> Path:
    log_dir = data_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "app.log"
    handler = RotatingFileHandler(log_path, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    logging.getLogger().addHandler(handler)
    for name in ("uvicorn.error", "uvicorn.access"):
        logging.getLogger(name).addHandler(handler)
    return log_path


def _show_error(message: str) -> None:
    if os.name == "nt":
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, message, "AI拓客工具", 0x10)
    else:
        print(message)


def another_instance_running() -> bool:
    """Keep one packaged workbench process on Windows."""
    global _INSTANCE_MUTEX
    if os.name != "nt" or not getattr(sys, "frozen", False):
        return False
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = (ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p)
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
    ctypes.set_last_error(0)
    handle = kernel32.CreateMutexW(None, False, "Local\\AI_Customer_Workbench")
    if not handle:
        raise OSError(ctypes.get_last_error(), "无法创建应用单实例锁")
    if ctypes.get_last_error() == 183:
        kernel32.CloseHandle(handle)
        return True
    _INSTANCE_MUTEX = int(handle)
    return False


def main() -> None:
    base_dir = app_dir()
    if another_instance_running():
        if url := existing_workbench_url():
            webbrowser.open(url)
        else:
            _show_error("AI拓客工具已经运行，但暂时无法打开工作台，请稍后重试。")
        return
    configure_environment(base_dir)
    from app.main import app

    port = choose_port()
    url = f"http://127.0.0.1:{port}"
    threading.Thread(target=open_browser_when_ready, args=(url,), daemon=True).start()
    print(f"AI拓客工具已启动：{url}")
    print("关闭这个窗口即可停止本地服务。")
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="info")
    log_path = configure_persistent_logging(Path(os.environ["AI_CUSTOMER_DATA_DIR"]))
    server = uvicorn.Server(config)
    app.state.uvicorn_server = server
    try:
        server.run()
        if not server.started:
            raise RuntimeError("本地服务未能完成启动")
    except Exception as exc:
        logging.getLogger(__name__).exception("AI拓客工具本地服务异常退出")
        _show_error(f"AI拓客工具启动失败：{exc}\n\n详细日志：{log_path}")
        raise


if __name__ == "__main__":
    main()
