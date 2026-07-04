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


class LicenseUpdate(BaseModel):
    license_code: str = ""


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


class TrafficSettingsUpdate(BaseModel):
    values: dict[str, Any]


class TrafficCampaignCreate(BaseModel):
    name: str = ""
    mode: Literal["targeted", "random"] = "targeted"
    source_type: Literal["competitor", "keyword", "random_feed"] = "competitor"
    keyword: str = ""
    action_like: bool = True
    action_follow: bool = False
    action_comment: bool = True
    comment_templates: list[str] = Field(default_factory=list)
    image_asset_ids: list[int] = Field(default_factory=list)
    per_run_limit: int = Field(default=20, ge=1, le=100)
    daily_limit: int = Field(default=100, ge=1, le=500)
    stay_seconds_min: float = Field(default=6, ge=0, le=120)
    stay_seconds_max: float = Field(default=15, ge=0, le=300)
    action_interval_seconds_min: float = Field(default=1, ge=0, le=60)
    action_interval_seconds_max: float = Field(default=3, ge=0, le=120)


class TrafficTargetBuild(BaseModel):
    limit: int = Field(default=50, ge=1, le=500)


class TrafficRunCreate(BaseModel):
    campaign_id: int
    limit: int | None = Field(default=None, ge=1, le=100)


class TrafficAssetCreate(BaseModel):
    name: str = ""
    data_url: str
