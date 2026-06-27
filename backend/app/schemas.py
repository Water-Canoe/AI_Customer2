from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


Platform = Literal["dy", "xhs", "ks"]
TaskMode = Literal[
    "competitor_discovery",
    "competitor_crawl",
    "demand_content",
    "own_account",
    "profile_enrichment",
    "account_analysis",
]
LoginType = Literal["qrcode", "phone", "cookie"]


class TaskCreate(BaseModel):
    name: str = ""
    mode: TaskMode
    platform: Platform
    login_type: LoginType = "qrcode"
    keywords: str = ""
    specified_id: str = ""
    creator_id: str = ""
    content_count: int = Field(default=20, ge=1, le=500)
    comment_count: int = Field(default=20, ge=0, le=1000)
    collect_content: bool = True
    collect_comments: bool = False
    collect_authors: bool = True
    collect_sub_comments: bool = False
    max_concurrency: int = Field(default=1, ge=1, le=10)
    tcp_mode: bool = True
    headless: bool = False
    execute_crawler: bool = True


class TableUpdate(BaseModel):
    values: dict[str, Any]


class SettingsUpdate(BaseModel):
    values: dict[str, Any]


class ClearDataRequest(BaseModel):
    confirm: str


class AiJobCreate(BaseModel):
    target_type: Literal["competitor", "lead", "content"]
    target_id: int
    run_now: bool = True


class AiBatchCreate(BaseModel):
    target_type: Literal["competitor", "lead", "content"]
    target_ids: list[int]
    run_now: bool = True
