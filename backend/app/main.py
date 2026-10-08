from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
import uvicorn

from app import database
from app.routers import (
    accounts,
    ai,
    automation,
    content,
    data_governance,
    drafts,
    lead_accounts,
    libraries,
    message,
    message_inbox,
    overview,
    runtime,
    system,
    tasks,
    traffic,
)
from app.services import automation_workbench, data_management, job_handlers, job_queue, license_service, profile_manager, message_inbox as inbox_service
from app.version import APP_VERSION


@asynccontextmanager
async def lifespan(_: FastAPI):
    database.init_db()
    inbox_service.recover_pending_sends()
    license_service.check_license()
    job_queue.configure_handlers(job_handlers)
    job_queue.start()
    automation_workbench.start_scheduler()
    # 浏览器同步可能排队，后台执行以免延迟健康检查和工作台打开。
    inbox_startup = asyncio.create_task(inbox_service.auto_connect_accounts())
    try:
        yield
    finally:
        try:
            inbox_startup.cancel()
            with suppress(asyncio.CancelledError):
                await inbox_startup
        finally:
            automation_workbench.stop_scheduler()
            job_queue.shutdown()
            inbox_service.shutdown()
            profile_manager.shutdown()


app = FastAPI(title="AI拓客工具", version=APP_VERSION, lifespan=lifespan)


@app.middleware("http")
async def reject_requests_during_maintenance(request: Request, call_next):
    if data_management.maintenance_active() and request.url.path != "/api/health":
        return JSONResponse(status_code=503, content={"detail": "系统正在备份或维护数据，请稍后重试"})
    return await call_next(request)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

for api_router in (
    system.router,
    accounts.router,
    lead_accounts.router,
    traffic.router,
    automation.router,
    tasks.router,
    libraries.router,
    overview.router,
    data_governance.router,
    drafts.router,
    message.router,
    message_inbox.router,
    ai.router,
    content.router,
    runtime.router,
):
    app.include_router(api_router)


def _frontend_dist() -> Path | None:
    candidates: list[Path] = []
    env_path = os.getenv("AI_CUSTOMER_FRONTEND_DIST", "").strip()
    if env_path:
        candidates.append(Path(env_path))
    if os.getenv("AI_CUSTOMER_PACKAGED", "").strip() == "1":
        install_root = Path(os.environ["AI_CUSTOMER_INSTALL_ROOT"])
        candidates.append(install_root / "runtime" / "frontend_dist")
    candidates.append(database.WORKSPACE_ROOT / "frontend" / "dist")
    for path in candidates:
        if path and (path / "index.html").exists():
            return path
    return None


def run_development_server() -> None:
    # 开发启动也保存 Server 实例，让页面可以请求 Uvicorn 正常退出。
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=8000, log_level="info"))
    app.state.uvicorn_server = server
    server.run()


@app.get("/{full_path:path}", include_in_schema=False)
def serve_frontend(full_path: str) -> FileResponse:
    if full_path.startswith("api/"):
        raise HTTPException(status_code=404, detail="接口不存在")
    dist = _frontend_dist()
    if not dist:
        raise HTTPException(status_code=404, detail="前端静态文件不存在，请先运行 npm run build")
    safe_dist = dist.resolve()
    target = (safe_dist / full_path).resolve()
    if target.is_file() and safe_dist in target.parents:
        return FileResponse(target)
    return FileResponse(safe_dist / "index.html")


if __name__ == "__main__":
    run_development_server()
