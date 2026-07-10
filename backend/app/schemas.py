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


class ContentAssetUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class ContentVideoJobCreate(BaseModel):
    params: dict[str, Any] = Field(default_factory=dict)
    asset_ids: list[str] = Field(default_factory=list)
    audio_asset_id: str = ""
    bgm_asset_id: str = ""


class ContentSettingsUpdate(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)


class ContentScriptRequest(BaseModel):
    video_subject: str = Field(min_length=1, max_length=500)
    video_language: str = ""
    paragraph_number: int = Field(default=1, ge=1, le=10)
    video_script_prompt: str = Field(default="", max_length=2000)
    custom_system_prompt: str = Field(default="", max_length=8000)


class ContentTermsRequest(BaseModel):
    video_subject: str = Field(min_length=1, max_length=500)
    video_script: str = Field(min_length=1, max_length=20000)
    amount: int = Field(default=5, ge=1, le=20)
    match_materials_to_script: bool = False


class ContentSocialMetadataRequest(BaseModel):
    video_subject: str = Field(min_length=1, max_length=500)
    video_script: str = Field(default="", max_length=20000)
    language: str = Field(default="auto", max_length=64)
    platform: str = Field(default="tiktok", max_length=64)


class LicenseUpdate(BaseModel):
    license_code: str = ""


class TrafficPlanCreate(BaseModel):
    name: str = ""
    platform: Platform = "dy"
    source_mode: Literal["random_feed", "competitor_videos", "collected_keyword", "search_keyword"] = "random_feed"
    source_value: str = ""
    action_like: bool = False
    action_collect: bool = False
    action_follow: bool = False
    action_comment_text: bool = False
    action_comment_image: bool = False
    round_video_limit: int = Field(default=5, ge=1, le=200)
    enabled: bool = True


class TrafficSettingsUpdate(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)
    texts: list[Any] = Field(default_factory=list)
    images: list[Any] = Field(default_factory=list)


class ClearDataRequest(BaseModel):
    confirm: str
    create_backup: bool = True
    include_crawler: bool = True


class BackupCreateRequest(BaseModel):
    reason: str = "manual"


class BackupRestoreRequest(BaseModel):
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


class CustomerAutoMessageRequest(BaseModel):
    dry_run: bool = False
    timeout_seconds: int = Field(default=0, ge=0, le=3600)
    message_script: str = ""
    script_label: str = ""


class MessageAutoBatchCreate(BaseModel):
    platform: Platform = "dy"
    keyword: str
    count: int = Field(default=10, ge=1, le=200)
    interval_min_seconds: int = Field(default=30, ge=0, le=3600)
    interval_max_seconds: int = Field(default=60, ge=0, le=3600)
