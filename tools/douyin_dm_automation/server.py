from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

try:
    from .automation import DEFAULT_WAIT_SECONDS, send_douyin_dm
except ImportError:
    from automation import DEFAULT_WAIT_SECONDS, send_douyin_dm

APP_DIR = Path(__file__).resolve().parent
INDEX_HTML = APP_DIR / "index.html"

app = FastAPI(title="Douyin DM Automation Demo")
_send_lock = asyncio.Lock()


class SendRequest(BaseModel):
    user_url: str = Field(..., description="抖音用户主页 URL")
    message: str = Field(..., description="私信话术")
    dry_run: bool = Field(False, description="只输入不发送")
    login_wait_seconds: int = Field(DEFAULT_WAIT_SECONDS, ge=30, le=900)


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(INDEX_HTML)


@app.post("/api/send")
async def send_message(payload: SendRequest) -> dict:
    # 同一个持久化浏览器目录不能并发打开。
    async with _send_lock:
        try:
            return await send_douyin_dm(
                payload.user_url,
                payload.message,
                login_wait_seconds=payload.login_wait_seconds,
                dry_run=payload.dry_run,
            )
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
