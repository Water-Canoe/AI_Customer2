from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


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
    account_id: str = ""


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
    publish: dict[str, Any] = Field(default_factory=dict)


class ContentVideoJobUpdate(BaseModel):
    subject: str = Field(min_length=1, max_length=500)


class ContentVideoOutputUpdate(BaseModel):
    output_name: str = Field(min_length=1, max_length=255)
    upload_status: Literal["not_uploaded", "uploaded"]


AccountRole = Literal["brand", "service", "operations", "traffic", "test"]
AccountFeature = Literal["acquisition", "message", "traffic", "publish"]


class PlatformAccountCreate(BaseModel):
    platform: Platform
    name: str = Field(min_length=1, max_length=100)
    role: AccountRole
    features: list[AccountFeature] = Field(default_factory=list, max_length=4)
    default_features: list[AccountFeature] = Field(default_factory=list, max_length=4)


class PlatformAccountUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    role: AccountRole | None = None
    platform_user_id: str | None = Field(default=None, max_length=200)
    enabled: bool | None = None
    features: list[AccountFeature] | None = Field(default=None, max_length=4)
    default_features: list[AccountFeature] | None = Field(default=None, max_length=4)


class ContentPublishSource(BaseModel):
    type: Literal["video_output", "assets"]
    video_job_id: str = ""
    output_name: str = ""
    asset_ids: list[str] = Field(default_factory=list)


class ContentPublishTaskCreate(BaseModel):
    source: ContentPublishSource
    account_ids: list[str] = Field(default_factory=list)
    title: str = Field(min_length=1, max_length=500)
    description: str = Field(default="", max_length=20000)
    tags: list[str] = Field(default_factory=list, max_length=20)
    publish_strategy: Literal["immediate", "scheduled"] = "immediate"
    scheduled_at: str = ""
    platform_overrides: dict[str, Any] = Field(default_factory=dict)


class ContentPublishOneClick(BaseModel):
    source: ContentPublishSource


class ContentPublishResultUpdate(BaseModel):
    status: Literal["succeeded", "failed"]
    note: str = Field(default="", max_length=1000)


class ContentVoiceProfileCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    provider: str = Field(default="voxcpm2", min_length=1, max_length=50)
    reference_asset_id: str = Field(min_length=1, max_length=100)
    prompt_text: str = Field(default="", max_length=2000)
    style_prompt: str = Field(default="", max_length=500)
    consent_confirmed: bool


class ContentVoiceProfileUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    prompt_text: str | None = Field(default=None, max_length=2000)
    style_prompt: str | None = Field(default=None, max_length=500)


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
    account_id: str = ""


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
    record_message_attempt: bool = False


class CustomerAutoMessageRequest(BaseModel):
    dry_run: bool = False
    timeout_seconds: int = Field(default=0, ge=0, le=3600)
    message_script: str = ""
    script_label: str = ""
    account_id: str = ""


class MessageAutoBatchCreate(BaseModel):
    platform: Platform = "dy"
    keyword: str
    count: int = Field(default=10, ge=1, le=200)
    interval_min_seconds: int = Field(default=30, ge=0, le=3600)
    interval_max_seconds: int = Field(default=60, ge=0, le=3600)
    account_id: str = ""


class KeywordLeadPlanConfig(BaseModel):
    platform: Literal["dy"] = "dy"
    keywords: list[str] = Field(min_length=1, max_length=100)
    keyword_count: int = Field(default=1, ge=1, le=10)
    discovery_content_count: int = Field(default=20, ge=1, le=100)
    competitor_limit: int = Field(default=30, ge=1, le=100)
    competitor_content_count: int = Field(default=10, ge=1, le=100)
    comment_count: int = Field(default=50, ge=1, le=1000)
    collect_sub_comments: bool = False
    # 清理开关只在对应 AI 分析完成且明确判负后生效。
    auto_delete_non_competitors: bool = False
    auto_analyze_leads: bool = True
    auto_delete_non_customers: bool = False
    acquisition_account_id: str = ""

    @model_validator(mode="after")
    def normalize_keywords(self) -> "KeywordLeadPlanConfig":
        self.keywords = list(dict.fromkeys(value.strip() for value in self.keywords if value.strip()))
        if not self.keywords:
            raise ValueError("请至少填写一个关键词")
        self.keyword_count = min(self.keyword_count, len(self.keywords))
        if self.auto_delete_non_customers and not self.auto_analyze_leads:
            raise ValueError("自动删除非客户必须先开启客户意向分析")
        return self


class MessagePlanConfig(BaseModel):
    platform: Literal["dy"] = "dy"
    keyword_scope: Literal["all", "selected"] = "all"
    keywords: list[str] = Field(default_factory=list, max_length=100)
    count: int = Field(default=10, ge=1, le=100)
    script_mode: Literal["ai", "fixed"] = "ai"
    fixed_script: str = Field(default="", max_length=2000)
    interval_min_seconds: int = Field(default=30, ge=0, le=3600)
    interval_max_seconds: int = Field(default=60, ge=0, le=3600)
    account_id: str = ""

    @model_validator(mode="after")
    def validate_dependencies(self) -> "MessagePlanConfig":
        self.keywords = list(dict.fromkeys(value.strip() for value in self.keywords if value.strip()))
        if self.keyword_scope == "selected" and not self.keywords:
            raise ValueError("指定关键词模式下请至少选择一个关键词")
        if self.script_mode == "fixed" and not self.fixed_script.strip():
            raise ValueError("固定话术模式下必须填写话术")
        if self.interval_max_seconds < self.interval_min_seconds:
            raise ValueError("最大间隔不能小于最小间隔")
        return self


class TrafficAutomationPlanConfig(BaseModel):
    platform: Literal["dy", "ks"] = "dy"
    source_mode: Literal["random_feed", "competitor_videos", "collected_keyword", "search_keyword"] = "random_feed"
    source_value: str = ""
    action_like: bool = False
    action_collect: bool = False
    action_follow: bool = False
    action_comment_text: bool = False
    action_comment_image: bool = False
    round_video_limit: int = Field(default=5, ge=1, le=200)
    account_id: str = ""

    @model_validator(mode="after")
    def validate_dependencies(self) -> "TrafficAutomationPlanConfig":
        self.source_value = self.source_value.strip()
        if self.source_mode in {"collected_keyword", "search_keyword"} and not self.source_value:
            raise ValueError("关键词来源必须填写关键词")
        if self.platform == "ks" and self.source_mode != "random_feed":
            raise ValueError("快手引流当前只支持随机推荐流")
        if self.platform == "ks" and self.action_comment_image:
            raise ValueError("快手 Web 端暂不支持评论图片")
        return self


AutomationPlanConfig = KeywordLeadPlanConfig | MessagePlanConfig | TrafficAutomationPlanConfig


def _automation_config_type(plan_type: str) -> type[KeywordLeadPlanConfig] | type[MessagePlanConfig] | type[TrafficAutomationPlanConfig]:
    return {
        "keyword_lead": KeywordLeadPlanConfig,
        "message": MessagePlanConfig,
        "traffic": TrafficAutomationPlanConfig,
    }.get(plan_type, MessagePlanConfig)


class AutomationPlanCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    plan_type: Literal["keyword_lead", "message", "traffic"]
    weekdays: list[int] = Field(min_length=1, max_length=7)
    run_time: str = Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    config: AutomationPlanConfig
    enabled: bool = False

    @model_validator(mode="before")
    @classmethod
    def parse_typed_config(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        values = dict(data)
        expected = _automation_config_type(str(values.get("plan_type") or ""))
        values["config"] = expected.model_validate(values.get("config") or {})
        return values

    @model_validator(mode="after")
    def validate_plan_config(self) -> "AutomationPlanCreate":
        self.weekdays = sorted(set(self.weekdays))
        if any(value < 1 or value > 7 for value in self.weekdays):
            raise ValueError("星期只能是1至7")
        expected = _automation_config_type(self.plan_type)
        if not isinstance(self.config, expected):
            raise ValueError("计划类型与配置不匹配")
        return self


class AutomationPlanPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    weekdays: list[int] | None = Field(default=None, min_length=1, max_length=7)
    run_time: str | None = Field(default=None, pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    config: dict[str, Any] | None = None
    enabled: bool | None = None


class AutomationPlanOrderUpdate(BaseModel):
    plan_ids: list[str] = Field(min_length=1, max_length=500)


class AutomationMessageLimitsUpdate(BaseModel):
    daily_limit: int = Field(ge=1, le=100)
    hourly_limit: int = Field(ge=1, le=40)
