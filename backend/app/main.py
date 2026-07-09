from __future__ import annotations

import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from app import database
from app.routers import ai, message, overview, runtime, system, tasks, traffic
from app.services import job_queue, profile_manager


@asynccontextmanager
async def lifespan(_: FastAPI):
    database.init_db()
    job_queue.start()
    try:
        yield
    finally:
        job_queue.shutdown()
        profile_manager.shutdown()


app = FastAPI(title="AI拓客工具", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

for api_router in (
    system.router,
    traffic.router,
    tasks.router,
    overview.router,
    message.router,
    ai.router,
    runtime.router,
):
    app.include_router(api_router)


def _frontend_dist() -> Path | None:
    candidates: list[Path] = []
    env_path = os.getenv("AI_CUSTOMER_FRONTEND_DIST", "").strip()
    if env_path:
        candidates.append(Path(env_path))
    if getattr(sys, "frozen", False):
        candidates.append(Path(getattr(sys, "_MEIPASS", "")) / "frontend_dist")
        candidates.append(Path(sys.executable).resolve().parent / "frontend_dist")
    candidates.append(database.WORKSPACE_ROOT / "frontend" / "dist")
    for path in candidates:
        if path and (path / "index.html").exists():
            return path
    return None


@app.get("/{full_path:path}", include_in_schema=False)
def serve_frontend(full_path: str) -> FileResponse:
    if full_path.startswith("api/"):
        raise HTTPException(status_code=404, detail="接口不存在")
    dist = _frontend_dist()
    if not dist:
        raise HTTPException(status_code=404, detail="前端静态文件不存在，请先运行 npm run build")
    safe_dist = dist.resolve()
    target = (safe_dist / full_path).resolve()
    if target.is_file() and str(target).startswith(str(safe_dist)):
        return FileResponse(target)
    return FileResponse(safe_dist / "index.html")
