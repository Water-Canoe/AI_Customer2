from __future__ import annotations

import os
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence


@dataclass
class InteractiveSession:
    platform: str
    process: subprocess.Popen[Any]
    started_at: float


_LOCK = threading.RLock()
_INTERACTIVE: InteractiveSession | None = None
_RUNTIME_OWNER = ""


def acquire_runtime(job_id: str) -> bool:
    """Reserve the shared browser profile for one queued runtime job."""
    global _RUNTIME_OWNER
    with _LOCK:
        _refresh_interactive_locked()
        if _INTERACTIVE or (_RUNTIME_OWNER and _RUNTIME_OWNER != job_id):
            return False
        _RUNTIME_OWNER = job_id
        return True


def release_runtime(job_id: str) -> None:
    global _RUNTIME_OWNER
    with _LOCK:
        if _RUNTIME_OWNER == job_id:
            _RUNTIME_OWNER = ""


def launch_interactive(
    platform: str,
    command: Sequence[str],
    *,
    cwd: Path,
    log_path: Path,
) -> dict[str, Any]:
    """Start one visible login process without exposing its local profile path."""
    global _INTERACTIVE
    clean_platform = str(platform or "").strip()
    if not clean_platform:
        raise ValueError("平台不能为空")

    with _LOCK:
        _refresh_interactive_locked()
        if _INTERACTIVE:
            return {"started": False, "active": True, "platform": _INTERACTIVE.platform}
        if _RUNTIME_OWNER:
            raise ValueError("浏览器自动化任务正在执行，请等待任务结束后再打开登录窗口")

        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as log_file:
            process = subprocess.Popen(
                list(command),
                cwd=str(cwd),
                stdout=log_file,
                stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        _INTERACTIVE = InteractiveSession(clean_platform, process, time.time())

    time.sleep(1)
    with _LOCK:
        _refresh_interactive_locked()
        if not _INTERACTIVE:
            raise ValueError("登录窗口启动失败，请检查运行环境和本地日志")
        return {"started": True, "active": True, "platform": clean_platform}


def status() -> dict[str, Any]:
    with _LOCK:
        _refresh_interactive_locked()
        session = _INTERACTIVE
        return {
            "interactive_active": bool(session),
            "interactive_platform": session.platform if session else "",
            "runtime_active": bool(_RUNTIME_OWNER),
        }


def close_interactive() -> dict[str, Any]:
    global _INTERACTIVE
    with _LOCK:
        _refresh_interactive_locked()
        session = _INTERACTIVE
        _INTERACTIVE = None
    if not session:
        return {"ok": True, "closed": False}
    _terminate_process_tree(session.process)
    return {"ok": True, "closed": True, "platform": session.platform}


def shutdown() -> dict[str, Any]:
    result = close_interactive()
    with _LOCK:
        runtime_active = bool(_RUNTIME_OWNER)
    return {**result, "runtime_active": runtime_active}


def _refresh_interactive_locked() -> None:
    global _INTERACTIVE
    if _INTERACTIVE and _INTERACTIVE.process.poll() is not None:
        _INTERACTIVE = None


def _terminate_process_tree(process: subprocess.Popen[Any], timeout_seconds: float = 5.0) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T"],
            check=False,
            capture_output=True,
            timeout=max(1.0, timeout_seconds),
        )
    else:
        process.terminate()
    try:
        process.wait(timeout=max(0.5, timeout_seconds))
        return
    except subprocess.TimeoutExpired:
        pass
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            check=False,
            capture_output=True,
            timeout=max(1.0, timeout_seconds),
        )
    else:
        process.kill()
    try:
        process.wait(timeout=max(0.5, timeout_seconds))
    except subprocess.TimeoutExpired:
        return
