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


class AiBulkDelete(BaseModel):
    target_ids: list[int]


class BulkActionPreview(BaseModel):
    action: Literal[
        "ai_analyze",
        "delete_non_competitors",
        "delete_non_customers",
        "retry_failed_ai",
        "keyword_analyze",
        "keyword_find_customers",
    ]
    target_type: Literal["competitor", "lead", "ai_job", "keyword"]
    target_ids: list[int | str] = Field(default_factory=list)
    filters: dict[str, Any] = Field(default_factory=dict)


class CustomerFollowStatusUpdate(BaseModel):
    follow_status: Literal["待筛选", "未私信", "已私信", "未回复", "已回复", "未成交", "已成交", "非客户", "无需跟进"]
    note: str = ""
