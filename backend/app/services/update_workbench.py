from __future__ import annotations

import asyncio
import os
import threading
from pathlib import Path
from typing import Any

from app.services import automation_workbench, data_management, desktop_update


_lock = threading.RLock()
_stop = threading.Event()
_task: asyncio.Task | None = None
_checked: dict[str, Any] = {}
_requests = 0
_state: dict[str, Any] = {"phase": "idle", "percent": 0, "message": ""}
_PUBLIC_FIELDS = ("available", "can_prepare", "current_version", "version", "notes", "size", "mandatory", "reason")


def install_root() -> Path:
    """使用启动器给出的安装根目录，不能把业务 EXE 目录当作根目录。"""
    root = os.getenv("AI_CUSTOMER_INSTALL_ROOT", "").strip()
    if os.getenv("AI_CUSTOMER_PACKAGED") != "1" or not root:
        raise ValueError("软件更新仅支持打包版，请通过 AI_Customer.exe 启动软件")
    path = Path(root).resolve()
    if not (path / "AI_Customer.exe").is_file():
        raise ValueError("安装目录缺少 AI_Customer.exe，请重新解压核心包")
    return path


def installing() -> bool:
    with _lock:
        return _state["phase"] == "installing"


def enter_request(path: str) -> bool:
    """请求计数与安装门禁共用锁，避免已进入的请求在空闲检查后才发送或入队。"""
    global _requests
    with _lock:
        if installing() and path != "/api/health":
            raise ValueError("应用正在安全重启并安装更新，请稍候")
        tracked = path.startswith("/api/") and path not in {
            "/api/health", "/api/system/check-update", "/api/system/prepare-update",
            "/api/system/update-status", "/api/system/install-update",
        }
        if tracked:
            _requests += 1
        return tracked


def leave_request() -> None:
    global _requests
    with _lock:
        _requests -= 1


def status() -> dict[str, Any]:
    root = install_root()
    with _lock:
        result = {key: _checked[key] for key in _PUBLIC_FIELDS if key in _checked}
        result.update(_state)
        if result["phase"] in {"idle", "ready", "failed"}:
            prepared = desktop_update.prepared_update(root)
            if prepared["ready"] and _checked.get("can_prepare") and prepared.get("version") != _checked.get("version"):
                result.update(ready=False, phase="idle" if result["phase"] == "ready" else result["phase"], message="请下载本次检查到的新版本")
            elif prepared["ready"]:
                result.update(prepared, phase="ready", percent=100, message="下载及校验完成，可以安装")
            elif result["phase"] == "ready":
                result.update(prepared, phase="failed", message=prepared["reason"])
        return result


async def check() -> dict[str, Any]:
    global _checked
    root = install_root()
    with _lock:
        if _state["phase"] in {"checking", "downloading", "installing"}:
            return status()
        _state.update(phase="checking", message="正在检查更新", percent=0)
    try:
        checked = await asyncio.to_thread(desktop_update.check_remote_update, root)
        with _lock:
            _checked = checked
            _state.update(phase="idle", message="", percent=0)
        return status()
    except Exception:
        with _lock:
            _state.update(phase="failed", message="检查更新失败，请重试")
        raise


async def prepare() -> dict[str, Any]:
    global _task
    root = install_root()
    with _lock:
        if _state["phase"] in {"checking", "downloading", "installing"}:
            raise ValueError("更新操作正在执行，请等待完成")
        if not _checked.get("can_prepare"):
            raise ValueError("请先检查可用更新，再下载")
        checked = dict(_checked)
        _stop.clear()
        _state.update(phase="downloading", message="正在准备下载", percent=0)

    def progress(message: str, percent: int) -> None:
        if _stop.is_set():
            raise RuntimeError("更新下载已取消，当前版本未改变")
        with _lock:
            _state.update(message=message, percent=percent)

    def download() -> None:
        try:
            desktop_update.prepare_remote_update(root, checked, progress)
            with _lock:
                _state.update(phase="ready", percent=100, message="下载及校验完成，可以安装")
        except Exception as exc:
            # 未知网络错误不原样回显，避免临时下载地址或凭据出现在页面。
            message = str(exc) if isinstance(exc, (RuntimeError, ValueError)) else f"下载或校验失败（{type(exc).__name__}），请重新检查更新"
            with _lock:
                _state.update(phase="failed", message=message)

    # 生命周期持有该任务；HTTP 请求返回后下载继续，退出时通知并等待线程收尾。
    _task = asyncio.create_task(asyncio.to_thread(download))
    return status()


def install(backend_pid: int) -> dict[str, Any]:
    root = install_root()
    with _lock:
        if _state["phase"] in {"checking", "downloading", "installing"}:
            raise ValueError("更新操作正在执行，请等待完成")
        if _requests:
            raise ValueError("其它业务请求正在完成，请稍后再安装更新")
        prepared = desktop_update.prepared_update(root)
        if not prepared["ready"]:
            raise ValueError("更新尚未下载或校验失败，请重新检查更新")
        if _checked.get("can_prepare") and prepared.get("version") != _checked.get("version"):
            raise ValueError("请先下载本次检查到的新版本，再安装更新")
        _state.update(phase="installing", message="正在安全退出并安装更新")
    scheduler_running = automation_workbench.scheduler_running()
    try:
        automation_workbench.stop_scheduler()
        if automation_workbench.scheduler_running():
            raise ValueError("自动化调度尚未停止，请等待当前检查结束后再安装更新")
        # 接收连接会由 lifespan 正常关闭；发送、登录及业务任务必须先完成。
        data_management.ensure_idle("安装更新", allow_message_receivers=True)
        desktop_update.start_prepared_update(root, backend_pid)
    except Exception:
        with _lock:
            _state.update(phase="ready", message="安装未开始，当前应用继续运行")
        if scheduler_running:
            automation_workbench.start_scheduler()
        raise
    return {"ok": True, "message": "更新已准备，应用正在安全重启"}


async def shutdown() -> None:
    _stop.set()
    if _task is not None and not _task.done():
        await _task
