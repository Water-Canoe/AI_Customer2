from __future__ import annotations

import os
import socket
import sys
import threading
import time
import webbrowser
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


def open_browser_later(url: str) -> None:
    """Open the browser after uvicorn has a short moment to start."""
    time.sleep(1.5)
    webbrowser.open(url)


def run_internal_mode(base_dir: Path) -> bool:
    if len(sys.argv) != 3 or sys.argv[1] != "--internal-platform-login":
        return False
    configure_environment(base_dir)
    from app.services.traffic_workbench import _hold_platform_login_window

    _hold_platform_login_window(sys.argv[2])
    return True


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
    if run_internal_mode(base_dir):
        return
    if another_instance_running():
        return
    configure_environment(base_dir)
    from app.main import app

    port = choose_port()
    url = f"http://127.0.0.1:{port}"
    threading.Thread(target=open_browser_later, args=(url,), daemon=True).start()
    print(f"AI拓客工具已启动：{url}")
    print("关闭这个窗口即可停止本地服务。")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")


if __name__ == "__main__":
    main()
