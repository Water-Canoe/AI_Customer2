from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from app import migrations


BACKEND_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = BACKEND_ROOT.parent
DEFAULT_DATA_ROOT = WORKSPACE_ROOT / "data"
DEFAULT_MEDIA_CRAWLER_PATH = Path(os.getenv("AI_CUSTOMER_MEDIA_CRAWLER_PATH", str(WORKSPACE_ROOT / "MyCrawler")))
DEFAULT_MEDIA_CRAWLER_DB = Path(os.getenv("AI_CUSTOMER_MEDIA_CRAWLER_DB", str(DEFAULT_MEDIA_CRAWLER_PATH / "database" / "sqlite_tables.db")))


def get_data_root() -> Path:
    """Return the persistent data folder that survives app updates."""
    return Path(os.getenv("AI_CUSTOMER_DATA_DIR", str(DEFAULT_DATA_ROOT)))


def get_db_path() -> Path:
    """Return the active project SQLite path."""
    return Path(os.getenv("AI_CUSTOMER_DB", str(get_data_root() / "ai_customer.sqlite3")))


def get_media_crawler_db_path(conn: sqlite3.Connection | None = None) -> Path:
    """Return the currently configured MyCrawler SQLite path."""
    if conn is None:
        with connect() as active_conn:
            return get_media_crawler_db_path(active_conn)
    value = get_setting(conn, "media_crawler_db_path", str(DEFAULT_MEDIA_CRAWLER_DB))
    return Path(str(value).strip().strip('"').strip("'")).expanduser()


def get_content_assets_root() -> Path:
    """Return the managed customer content asset folder."""
    return get_data_root() / "content_assets"


def get_video_generation_root() -> Path:
    """Return the persistent video generation workspace."""
    return get_data_root() / "video_generation"


def get_social_publish_root() -> Path:
    """Return the persistent social publishing workspace."""
    return get_data_root() / "social_publish"


def get_voice_models_root() -> Path:
    """Return the shared model folder used by video and future avatar features."""
    return Path(os.getenv("AI_CUSTOMER_VOICE_MODELS_DIR", str(get_data_root() / "voice_models")))


def get_runtime_root() -> Path:
    """Return optional runtimes outside both app versions and data backups."""
    default_root = get_data_root().parent / "runtimes"
    return Path(os.getenv("AI_CUSTOMER_RUNTIME_DIR", str(default_root)))


def get_backup_root(db_path: Path | None = None) -> Path:
    """Return the backup folder beside the persistent business database."""
    return (db_path or get_db_path()).parent / "backups"


def get_backup_data_roots() -> dict[str, Path]:
    """Return user-owned folders that must stay consistent with the business database."""
    root = get_data_root()
    return {
        "traffic_images": root / "traffic_images",
        "content_assets": get_content_assets_root(),
        "video_tasks": get_video_generation_root() / "tasks",
        "social_publish": get_social_publish_root(),
    }


@contextmanager
def connect(db_path: Path | None = None) -> Iterator[sqlite3.Connection]:
    """Open a SQLite connection with dict-like rows and FK checks."""
    path = db_path or get_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {key: row[key] for key in row.keys()}


def rows_to_dicts(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [row_to_dict(row) or {} for row in rows]


def quote_identifier(identifier: str) -> str:
    """Quote a SQLite table or column name for dynamic SQL."""
    return '"' + identifier.replace('"', '""') + '"'


SCHEMA_SQL = """
CREATE TABLE settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE crawl_jobs (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    mode TEXT NOT NULL,
    platform TEXT NOT NULL,
    login_type TEXT NOT NULL,
    crawler_type TEXT NOT NULL,
    keywords TEXT NOT NULL DEFAULT '',
    specified_id TEXT NOT NULL DEFAULT '',
    creator_id TEXT NOT NULL DEFAULT '',
    content_count INTEGER NOT NULL DEFAULT 20,
    comment_count INTEGER NOT NULL DEFAULT 20,
    collect_content INTEGER NOT NULL DEFAULT 1,
    collect_comments INTEGER NOT NULL DEFAULT 0,
    collect_authors INTEGER NOT NULL DEFAULT 1,
    collect_sub_comments INTEGER NOT NULL DEFAULT 0,
    max_concurrency INTEGER NOT NULL DEFAULT 1,
    tcp_mode INTEGER NOT NULL DEFAULT 1,
    headless INTEGER NOT NULL DEFAULT 0,
    execute_crawler INTEGER NOT NULL DEFAULT 1,
    skip_content_ids TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    archived INTEGER NOT NULL DEFAULT 0,
    error TEXT NOT NULL DEFAULT '',
    command TEXT NOT NULL DEFAULT '',
    process_id INTEGER,
    raw_started_ts_ms INTEGER NOT NULL DEFAULT 0,
    started_at TEXT,
    finished_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
, automation_managed INTEGER NOT NULL DEFAULT 0, account_id TEXT NOT NULL DEFAULT '');

CREATE TABLE task_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    level TEXT NOT NULL DEFAULT 'info',
    message TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    FOREIGN KEY(task_id) REFERENCES crawl_jobs(id) ON DELETE CASCADE
);

CREATE TABLE user_accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform TEXT NOT NULL,
    platform_user_id TEXT NOT NULL,
    sec_uid TEXT NOT NULL DEFAULT '',
    user_unique_id TEXT NOT NULL DEFAULT '',
    nickname TEXT NOT NULL DEFAULT '',
    avatar TEXT NOT NULL DEFAULT '',
    signature TEXT NOT NULL DEFAULT '',
    profile_url TEXT NOT NULL DEFAULT '',
    fans INTEGER,
    content_total_count INTEGER,
    account_role TEXT NOT NULL DEFAULT 'unknown',
    competitor_status TEXT NOT NULL DEFAULT '未分析',
    competitor_reason TEXT NOT NULL DEFAULT '',
    is_own_account INTEGER NOT NULL DEFAULT 0,
    raw_payload TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    UNIQUE(platform, platform_user_id)
);

CREATE TABLE contents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform TEXT NOT NULL,
    content_id TEXT NOT NULL,
    author_account_id INTEGER,
    title TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    content_url TEXT NOT NULL DEFAULT '',
    like_count INTEGER,
    comment_count INTEGER,
    last_comment_crawled_at TEXT NOT NULL DEFAULT '',
    source_keyword TEXT NOT NULL DEFAULT '',
    task_id TEXT,
    raw_payload TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    UNIQUE(platform, content_id),
    FOREIGN KEY(author_account_id) REFERENCES user_accounts(id) ON DELETE SET NULL,
    FOREIGN KEY(task_id) REFERENCES crawl_jobs(id) ON DELETE SET NULL
);

CREATE TABLE comments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform TEXT NOT NULL,
    comment_id TEXT NOT NULL,
    content_id INTEGER,
    author_account_id INTEGER,
    parent_comment_id TEXT NOT NULL DEFAULT '',
    body TEXT NOT NULL DEFAULT '',
    like_count INTEGER,
    task_id TEXT,
    raw_payload TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    UNIQUE(platform, comment_id),
    FOREIGN KEY(content_id) REFERENCES contents(id) ON DELETE CASCADE,
    FOREIGN KEY(author_account_id) REFERENCES user_accounts(id) ON DELETE SET NULL,
    FOREIGN KEY(task_id) REFERENCES crawl_jobs(id) ON DELETE SET NULL
);

CREATE TABLE account_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL,
    content_id INTEGER,
    keyword TEXT NOT NULL DEFAULT '',
    task_id TEXT,
    source_kind TEXT NOT NULL DEFAULT 'keyword_author',
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    UNIQUE(account_id, content_id, keyword, task_id, source_kind),
    FOREIGN KEY(account_id) REFERENCES user_accounts(id) ON DELETE CASCADE,
    FOREIGN KEY(content_id) REFERENCES contents(id) ON DELETE CASCADE,
    FOREIGN KEY(task_id) REFERENCES crawl_jobs(id) ON DELETE SET NULL
);

CREATE TABLE lead_user_accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL UNIQUE,
    screening_status TEXT NOT NULL DEFAULT '待筛选',
    follow_status TEXT NOT NULL DEFAULT '待筛选',
    manual_follow_status INTEGER NOT NULL DEFAULT 0,
    intention TEXT NOT NULL DEFAULT '',
    reason TEXT NOT NULL DEFAULT '',
    pain_points TEXT NOT NULL DEFAULT '[]',
    suggested_action TEXT NOT NULL DEFAULT '',
    script TEXT NOT NULL DEFAULT '',
    hidden INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    FOREIGN KEY(account_id) REFERENCES user_accounts(id) ON DELETE CASCADE
);

CREATE TABLE lead_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_account_id INTEGER NOT NULL,
    source_account_id INTEGER,
    content_id INTEGER,
    comment_id INTEGER,
    keyword TEXT NOT NULL DEFAULT '',
    source_type TEXT NOT NULL,
    task_id TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    UNIQUE(lead_account_id, source_account_id, content_id, comment_id, keyword, source_type),
    FOREIGN KEY(lead_account_id) REFERENCES lead_user_accounts(id) ON DELETE CASCADE,
    FOREIGN KEY(source_account_id) REFERENCES user_accounts(id) ON DELETE SET NULL,
    FOREIGN KEY(content_id) REFERENCES contents(id) ON DELETE CASCADE,
    FOREIGN KEY(comment_id) REFERENCES comments(id) ON DELETE CASCADE,
    FOREIGN KEY(task_id) REFERENCES crawl_jobs(id) ON DELETE SET NULL
);

CREATE TABLE lead_status_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_account_id INTEGER NOT NULL,
    from_status TEXT NOT NULL DEFAULT '',
    to_status TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    FOREIGN KEY(lead_account_id) REFERENCES lead_user_accounts(id) ON DELETE CASCADE
);

CREATE TABLE message_batches (
    id TEXT PRIMARY KEY,
    platform TEXT NOT NULL DEFAULT 'dy',
    keyword TEXT NOT NULL DEFAULT '',
    requested_count INTEGER NOT NULL DEFAULT 0,
    interval_min_seconds INTEGER NOT NULL DEFAULT 0,
    interval_max_seconds INTEGER NOT NULL DEFAULT 0,
    fill_only INTEGER NOT NULL DEFAULT 0,
    timeout_seconds INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending',
    total_count INTEGER NOT NULL DEFAULT 0,
    success_count INTEGER NOT NULL DEFAULT 0,
    failed_count INTEGER NOT NULL DEFAULT 0,
    skipped_count INTEGER NOT NULL DEFAULT 0,
    current_lead_id INTEGER,
    stop_requested INTEGER NOT NULL DEFAULT 0,
    error TEXT NOT NULL DEFAULT '',
    started_at TEXT,
    finished_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
, source TEXT NOT NULL DEFAULT 'manual', automation_run_id TEXT NOT NULL DEFAULT '', account_id TEXT NOT NULL DEFAULT '');

CREATE TABLE message_batch_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id TEXT NOT NULL,
    lead_account_id INTEGER NOT NULL,
    nickname TEXT NOT NULL DEFAULT '',
    profile_url TEXT NOT NULL DEFAULT '',
    script TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    error TEXT NOT NULL DEFAULT '',
    started_at TEXT,
    finished_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    FOREIGN KEY(batch_id) REFERENCES message_batches(id) ON DELETE CASCADE,
    FOREIGN KEY(lead_account_id) REFERENCES lead_user_accounts(id) ON DELETE CASCADE
);

CREATE INDEX idx_message_batches_status_created
ON message_batches(status, created_at);

CREATE INDEX idx_message_batch_items_batch_status
ON message_batch_items(batch_id, status, id);

CREATE TABLE raw_source_refs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type TEXT NOT NULL,
    entity_id INTEGER NOT NULL,
    platform TEXT NOT NULL,
    raw_table TEXT NOT NULL,
    raw_pk TEXT NOT NULL,
    task_id TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    UNIQUE(entity_type, entity_id, raw_table, raw_pk),
    FOREIGN KEY(task_id) REFERENCES crawl_jobs(id) ON DELETE SET NULL
);

CREATE TABLE deleted_identities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type TEXT NOT NULL,
    platform TEXT NOT NULL,
    identifier_type TEXT NOT NULL,
    identifier_value TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT '',
    snapshot TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    UNIQUE(entity_type, platform, identifier_type, identifier_value)
);

CREATE INDEX idx_deleted_identities_lookup
ON deleted_identities(entity_type, platform, identifier_type, identifier_value);

CREATE INDEX idx_crawl_jobs_status_archived_created
ON crawl_jobs(status, archived, created_at);

CREATE INDEX idx_task_logs_task_id_id
ON task_logs(task_id, id);

CREATE INDEX idx_contents_author_updated
ON contents(author_account_id, updated_at);

CREATE INDEX idx_comments_content_updated
ON comments(content_id, updated_at);

CREATE INDEX idx_account_sources_account_active
ON account_sources(account_id, active);

CREATE INDEX idx_account_sources_task_active
ON account_sources(task_id, active);

CREATE INDEX idx_lead_sources_lead_active
ON lead_sources(lead_account_id, active);

CREATE INDEX idx_lead_sources_task_active
ON lead_sources(task_id, active);

CREATE INDEX idx_lead_sources_content_active
ON lead_sources(content_id, active);

CREATE INDEX idx_lead_sources_comment_active
ON lead_sources(comment_id, active);

CREATE INDEX idx_lead_sources_source_active
ON lead_sources(source_account_id, active);

CREATE INDEX idx_lead_user_follow_hidden_updated
ON lead_user_accounts(follow_status, hidden, updated_at);

CREATE INDEX idx_lead_user_screening_hidden_updated
ON lead_user_accounts(screening_status, hidden, updated_at);

CREATE INDEX idx_lead_status_events_lead_status_created
ON lead_status_events(lead_account_id, to_status, created_at);

CREATE TABLE analysis_jobs (
    id TEXT PRIMARY KEY,
    target_type TEXT NOT NULL,
    target_id INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    error TEXT NOT NULL DEFAULT '',
    input_payload TEXT NOT NULL DEFAULT '{}',
    output_payload TEXT NOT NULL DEFAULT '{}',
    raw_output TEXT NOT NULL DEFAULT '',
    prompt_version TEXT NOT NULL DEFAULT '',
    system_prompt TEXT NOT NULL DEFAULT '',
    user_prompt TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    base_url TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
, auto_delete INTEGER NOT NULL DEFAULT -1);

CREATE INDEX idx_analysis_jobs_target_status
ON analysis_jobs(target_type, target_id, status);

CREATE INDEX idx_analysis_jobs_status_updated
ON analysis_jobs(status, updated_at);

CREATE TABLE deletion_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type TEXT NOT NULL,
    entity_id INTEGER NOT NULL,
    hard_delete INTEGER NOT NULL DEFAULT 0,
    detail TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE traffic_plans (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    platform TEXT NOT NULL DEFAULT 'dy',
    source_mode TEXT NOT NULL DEFAULT 'random_feed',
    source_value TEXT NOT NULL DEFAULT '',
    action_like INTEGER NOT NULL DEFAULT 0,
    action_collect INTEGER NOT NULL DEFAULT 0,
    action_follow INTEGER NOT NULL DEFAULT 0,
    action_comment_text INTEGER NOT NULL DEFAULT 0,
    action_comment_image INTEGER NOT NULL DEFAULT 0,
    round_video_limit INTEGER NOT NULL DEFAULT 5,
    enabled INTEGER NOT NULL DEFAULT 1,
    archived INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
, automation_managed INTEGER NOT NULL DEFAULT 0, account_id TEXT NOT NULL DEFAULT '');

CREATE TABLE traffic_runs (
    id TEXT PRIMARY KEY,
    plan_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    total_videos INTEGER NOT NULL DEFAULT 0,
    browsed_count INTEGER NOT NULL DEFAULT 0,
    action_success_count INTEGER NOT NULL DEFAULT 0,
    skipped_count INTEGER NOT NULL DEFAULT 0,
    failed_count INTEGER NOT NULL DEFAULT 0,
    stop_reason TEXT NOT NULL DEFAULT '',
    stop_suggestion TEXT NOT NULL DEFAULT '',
    stop_requested INTEGER NOT NULL DEFAULT 0,
    archived INTEGER NOT NULL DEFAULT 0,
    started_at TEXT,
    finished_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')), account_id TEXT NOT NULL DEFAULT '',
    FOREIGN KEY(plan_id) REFERENCES traffic_plans(id) ON DELETE CASCADE
);

CREATE TABLE traffic_run_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    video_id TEXT NOT NULL DEFAULT '',
    video_url TEXT NOT NULL DEFAULT '',
    author_id TEXT NOT NULL DEFAULT '',
    author_name TEXT NOT NULL DEFAULT '',
    video_desc TEXT NOT NULL DEFAULT '',
    like_count INTEGER,
    comment_count INTEGER,
    status TEXT NOT NULL DEFAULT 'pending',
    actions_done TEXT NOT NULL DEFAULT '[]',
    skip_reason TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    FOREIGN KEY(run_id) REFERENCES traffic_runs(id) ON DELETE CASCADE
);

CREATE TABLE traffic_action_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    level TEXT NOT NULL DEFAULT 'info',
    phase TEXT NOT NULL DEFAULT 'browse',
    message TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    suggestion TEXT NOT NULL DEFAULT '',
    details TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    FOREIGN KEY(run_id) REFERENCES traffic_runs(id) ON DELETE CASCADE
);

CREATE TABLE traffic_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    plan_id TEXT NOT NULL,
    platform TEXT NOT NULL DEFAULT 'dy',
    video_id TEXT NOT NULL DEFAULT '',
    video_url TEXT NOT NULL DEFAULT '',
    video_desc TEXT NOT NULL DEFAULT '',
    author_id TEXT NOT NULL DEFAULT '',
    author_name TEXT NOT NULL DEFAULT '',
    like_count INTEGER,
    comment_count INTEGER,
    actions TEXT NOT NULL DEFAULT '[]',
    comment_text TEXT NOT NULL DEFAULT '',
    comment_image_path TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'browsed',
    reason TEXT NOT NULL DEFAULT '',
    screenshot_path TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    FOREIGN KEY(run_id) REFERENCES traffic_runs(id) ON DELETE CASCADE,
    FOREIGN KEY(plan_id) REFERENCES traffic_plans(id) ON DELETE CASCADE
);

CREATE TABLE traffic_material_texts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    used_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE traffic_material_images (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    used_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE traffic_dedup_ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform TEXT NOT NULL,
    video_id TEXT NOT NULL DEFAULT '',
    author_id TEXT NOT NULL DEFAULT '',
    action_type TEXT NOT NULL,
    content_hash TEXT NOT NULL DEFAULT '',
    run_id TEXT,
    record_id INTEGER,
    status TEXT NOT NULL DEFAULT 'done',
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    UNIQUE(platform, video_id, author_id, action_type, content_hash),
    FOREIGN KEY(run_id) REFERENCES traffic_runs(id) ON DELETE SET NULL,
    FOREIGN KEY(record_id) REFERENCES traffic_records(id) ON DELETE SET NULL
);

CREATE TABLE runtime_jobs (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    entity_id TEXT NOT NULL DEFAULT '',
    resource TEXT NOT NULL DEFAULT 'default',
    payload TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'queued',
    priority INTEGER NOT NULL DEFAULT 0,
    attempt INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 1,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    error TEXT NOT NULL DEFAULT '',
    result TEXT NOT NULL DEFAULT '{}',
    lease_token TEXT NOT NULL DEFAULT '',
    heartbeat_at TEXT,
    started_at TEXT,
    finished_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE INDEX idx_runtime_jobs_status_priority
ON runtime_jobs(status, priority DESC, created_at, id);

CREATE INDEX idx_runtime_jobs_entity
ON runtime_jobs(kind, entity_id, created_at DESC);

CREATE UNIQUE INDEX idx_runtime_jobs_active_entity
ON runtime_jobs(kind, entity_id)
WHERE entity_id <> '' AND status IN ('queued', 'running');

CREATE TABLE content_assets (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    asset_type TEXT NOT NULL CHECK(asset_type IN ('video', 'image', 'audio')),
    relative_path TEXT NOT NULL UNIQUE,
    thumbnail_path TEXT NOT NULL DEFAULT '',
    mime_type TEXT NOT NULL DEFAULT '',
    file_size INTEGER NOT NULL,
    sha256 TEXT NOT NULL UNIQUE,
    width INTEGER,
    height INTEGER,
    duration REAL,
    deleted_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
, purpose TEXT NOT NULL DEFAULT ''
CHECK(purpose IN ('', 'background_music', 'voice_reference')));

CREATE TABLE video_jobs (
    id TEXT PRIMARY KEY,
    runtime_job_id TEXT NOT NULL DEFAULT '',
    subject TEXT NOT NULL,
    script TEXT NOT NULL DEFAULT '',
    params TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'queued',
    progress INTEGER NOT NULL DEFAULT 0,
    current_stage TEXT NOT NULL DEFAULT 'queued',
    attempt INTEGER NOT NULL DEFAULT 0,
    error TEXT NOT NULL DEFAULT '',
    outputs TEXT NOT NULL DEFAULT '[]',
    archived INTEGER NOT NULL DEFAULT 0,
    started_at TEXT,
    finished_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE video_job_assets (
    video_job_id TEXT NOT NULL,
    asset_id TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'material' CHECK(role IN ('material', 'audio', 'bgm')),
    sort_order INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(video_job_id, asset_id, role),
    FOREIGN KEY(video_job_id) REFERENCES video_jobs(id) ON DELETE CASCADE,
    FOREIGN KEY(asset_id) REFERENCES content_assets(id) ON DELETE RESTRICT
);

CREATE INDEX idx_content_assets_type_created
ON content_assets(asset_type, deleted_at, created_at DESC);

CREATE INDEX idx_video_jobs_status_created
ON video_jobs(status, archived, created_at DESC);

CREATE INDEX idx_video_job_assets_order
ON video_job_assets(video_job_id, role, sort_order);

CREATE TABLE voice_profiles (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    provider TEXT NOT NULL,
    reference_asset_id TEXT NOT NULL,
    prompt_text TEXT NOT NULL DEFAULT '',
    style_prompt TEXT NOT NULL DEFAULT '',
    consent_confirmed INTEGER NOT NULL DEFAULT 0,
    consent_confirmed_at TEXT,
    deleted_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    FOREIGN KEY(reference_asset_id) REFERENCES content_assets(id) ON DELETE RESTRICT
);

CREATE INDEX idx_voice_profiles_provider_created
ON voice_profiles(provider, deleted_at, created_at DESC);

CREATE INDEX idx_content_assets_purpose_created
ON content_assets(purpose, deleted_at, created_at DESC);

CREATE TABLE publish_accounts (
id TEXT PRIMARY KEY,
platform TEXT NOT NULL CHECK(platform IN ('dy', 'ks', 'xhs')),
name TEXT NOT NULL,
auth_relative_path TEXT NOT NULL UNIQUE,
status TEXT NOT NULL DEFAULT 'login_required'
    CHECK(status IN ('login_required', 'checking', 'ready', 'expired', 'error')),
is_default INTEGER NOT NULL DEFAULT 0,
enabled INTEGER NOT NULL DEFAULT 1,
last_checked_at TEXT,
last_error TEXT NOT NULL DEFAULT '',
qrcode_relative_path TEXT NOT NULL DEFAULT '',
deleted_at TEXT,
created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
        , role TEXT NOT NULL DEFAULT 'brand'
CHECK(role IN ('brand', 'service', 'operations', 'traffic', 'test')), platform_user_id TEXT NOT NULL DEFAULT '');

CREATE UNIQUE INDEX idx_publish_accounts_active_name
ON publish_accounts(platform, name) WHERE deleted_at IS NULL;

CREATE INDEX idx_publish_accounts_status
ON publish_accounts(enabled, status, platform, created_at DESC);

CREATE TABLE publish_tasks (
    id TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL,
    runtime_job_id TEXT NOT NULL DEFAULT '',
    account_id TEXT NOT NULL,
    source_type TEXT NOT NULL CHECK(source_type IN ('video_output', 'asset_video', 'asset_images')),
    content_type TEXT NOT NULL CHECK(content_type IN ('video', 'note')),
    video_job_id TEXT,
    output_index INTEGER NOT NULL DEFAULT 0,
    output_name TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    tags TEXT NOT NULL DEFAULT '[]',
    publish_strategy TEXT NOT NULL DEFAULT 'immediate'
        CHECK(publish_strategy IN ('immediate', 'scheduled')),
    scheduled_at TEXT,
    platform_options TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'queued'
        CHECK(status IN ('waiting_media', 'queued', 'running', 'succeeded', 'failed', 'review_required', 'cancelled')),
    current_stage TEXT NOT NULL DEFAULT 'queued',
    progress INTEGER NOT NULL DEFAULT 0,
    attempt INTEGER NOT NULL DEFAULT 0,
    result TEXT NOT NULL DEFAULT '{}',
    error TEXT NOT NULL DEFAULT '',
    started_at TEXT,
    finished_at TEXT,
    published_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    FOREIGN KEY(account_id) REFERENCES publish_accounts(id) ON DELETE RESTRICT,
    FOREIGN KEY(video_job_id) REFERENCES video_jobs(id) ON DELETE RESTRICT
);

CREATE INDEX idx_publish_tasks_status_created
ON publish_tasks(status, created_at DESC);

CREATE INDEX idx_publish_tasks_video_output
ON publish_tasks(video_job_id, output_name, created_at DESC);

CREATE TABLE publish_task_assets (
    task_id TEXT NOT NULL,
    asset_id TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'media'
        CHECK(role IN ('media', 'cover', 'portrait_cover', 'landscape_cover')),
    sort_order INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(task_id, asset_id, role),
    FOREIGN KEY(task_id) REFERENCES publish_tasks(id) ON DELETE CASCADE,
    FOREIGN KEY(asset_id) REFERENCES content_assets(id) ON DELETE RESTRICT
);

CREATE INDEX idx_publish_task_assets_order
ON publish_task_assets(task_id, role, sort_order);

CREATE TABLE automation_run_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    keyword TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    current_stage TEXT NOT NULL DEFAULT 'pending',
    context TEXT NOT NULL DEFAULT '{}',
    error TEXT NOT NULL DEFAULT '',
    started_at TEXT,
    finished_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    FOREIGN KEY(run_id) REFERENCES automation_runs(id) ON DELETE CASCADE
);

CREATE TABLE message_send_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_account_id INTEGER NOT NULL,
    source TEXT NOT NULL,
    source_id TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'reserved',
    error TEXT NOT NULL DEFAULT '',
    attempted_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    finished_at TEXT,
    FOREIGN KEY(lead_account_id) REFERENCES lead_user_accounts(id) ON DELETE CASCADE
);

CREATE INDEX idx_automation_run_items_run_status
ON automation_run_items(run_id, status, id);

CREATE INDEX idx_message_send_attempts_time
ON message_send_attempts(attempted_at, lead_account_id);

CREATE TABLE "automation_plans" (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    plan_type TEXT NOT NULL CHECK(plan_type IN ('keyword_lead', 'message', 'traffic')),
    weekdays TEXT NOT NULL DEFAULT '[]',
    run_time TEXT NOT NULL,
    config TEXT NOT NULL DEFAULT '{}',
    enabled INTEGER NOT NULL DEFAULT 0,
    archived INTEGER NOT NULL DEFAULT 0,
    last_triggered_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    sort_order INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE "automation_runs" (
    id TEXT PRIMARY KEY,
    plan_id TEXT,
    plan_name TEXT NOT NULL,
    plan_type TEXT NOT NULL CHECK(plan_type IN ('keyword_lead', 'message', 'traffic')),
    trigger_type TEXT NOT NULL CHECK(trigger_type IN ('scheduled', 'manual')),
    scheduled_at TEXT,
    config_snapshot TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'queued',
    current_stage TEXT NOT NULL DEFAULT 'queued',
    runtime_job_id TEXT NOT NULL DEFAULT '',
    total_count INTEGER NOT NULL DEFAULT 0,
    success_count INTEGER NOT NULL DEFAULT 0,
    failed_count INTEGER NOT NULL DEFAULT 0,
    skipped_count INTEGER NOT NULL DEFAULT 0,
    message_batch_id TEXT NOT NULL DEFAULT '',
    message_attempted_count INTEGER NOT NULL DEFAULT 0,
    message_success_count INTEGER NOT NULL DEFAULT 0,
    traffic_run_id TEXT NOT NULL DEFAULT '',
    traffic_runtime_job_id TEXT NOT NULL DEFAULT '',
    stop_requested INTEGER NOT NULL DEFAULT 0,
    error TEXT NOT NULL DEFAULT '',
    started_at TEXT,
    finished_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    FOREIGN KEY(plan_id) REFERENCES automation_plans(id) ON DELETE SET NULL
);

CREATE UNIQUE INDEX idx_automation_scheduled_once ON automation_runs(plan_id, scheduled_at) WHERE trigger_type = 'scheduled' AND scheduled_at IS NOT NULL;

CREATE UNIQUE INDEX idx_automation_active_plan ON automation_runs(plan_id) WHERE plan_id IS NOT NULL AND status IN ('queued', 'running');

CREATE INDEX idx_automation_plans_enabled_time ON automation_plans(enabled, archived, run_time);

CREATE INDEX idx_automation_plans_sort_order ON automation_plans(archived, sort_order, id);

CREATE INDEX idx_automation_runs_plan_status ON automation_runs(plan_id, status, created_at DESC);

CREATE TABLE account_feature_bindings (
    account_id TEXT NOT NULL,
    feature TEXT NOT NULL CHECK(feature IN ('acquisition', 'message', 'traffic', 'publish')),
    is_default INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'unknown'
        CHECK(status IN ('unknown', 'checking', 'ready', 'expired', 'error')),
    last_checked_at TEXT,
    last_error TEXT NOT NULL DEFAULT '',
    PRIMARY KEY(account_id, feature),
    FOREIGN KEY(account_id) REFERENCES publish_accounts(id) ON DELETE CASCADE
);

CREATE INDEX idx_account_feature_lookup
ON account_feature_bindings(feature, is_default, status, account_id);

CREATE UNIQUE INDEX idx_platform_account_user
ON publish_accounts(platform, platform_user_id)
WHERE deleted_at IS NULL AND platform_user_id <> '';

CREATE INDEX idx_user_accounts_platform_sec_uid ON user_accounts(platform, sec_uid);

CREATE INDEX idx_user_accounts_platform_profile_url ON user_accounts(platform, profile_url);
"""


DEFAULT_SETTINGS = {
    "media_crawler_path": str(DEFAULT_MEDIA_CRAWLER_PATH),
    "media_crawler_db_path": str(DEFAULT_MEDIA_CRAWLER_DB),
    "ai_base_url": "",
    "ai_api_key": "",
    "ai_model": "deepseek-chat",
    "default_content_count": "20",
    "default_comment_count": "20",
    "content_cutoff_days": "0",
    "comment_cutoff_days": "0",
    "comment_recrawl_cooldown_hours": "24",
    "account_analysis_content_count": "5",
    "ai_analysis_concurrency": "3",
    "unreplied_reminder_days": "3",
    "dm_script_mode": "ai",
    "fixed_dm_script": "",
    "auto_dm_fill_only": "false",
    "auto_dm_timeout_seconds": "300",
    "message_daily_limit": "",
    "message_hourly_limit": "",
    "douyin_detail_sleep_seconds": "2",
    "max_concurrency": "1",
    "headless": "false",
    "auto_analyze_competitors": "false",
    "auto_delete_non_competitors": "false",
    "auto_analyze_leads": "false",
    "auto_delete_non_customers": "false",
    "own_accounts": json.dumps({"dy": [], "xhs": [], "ks": []}, ensure_ascii=False),
    "license_code": "",
    "device_code": "",
    "license_last_status": "unconfigured",
    "license_last_reason": "",
    "license_last_message": "未填写授权码",
    "license_last_checked_at": "",
    "license_lease_text": "",
    "license_lease_signature": "",
    "license_max_devices": "",
    "license_active_device_count": "",
    "traffic_daily_action_limit": "50",
    "traffic_min_watch_seconds": "3",
    "traffic_max_watch_seconds": "8",
    "traffic_author_cooldown_hours": "24",
    "traffic_stop_after_failures": "3",
    "traffic_close_browser_on_failure": "true",
    "traffic_headless": "false",
    "traffic_action_probability": "60",
    "icp_profile": json.dumps(
        {
            "product": "",
            "company_name": "",
            "industry": "",
            "roles": "",
            "pain_points": "",
            "high_intent_words": "",
            "value_proposition": "",
            "excluded_audience": "",
        },
        ensure_ascii=False,
    ),
    "next_task_number": "1",
    "content_video_config": "{}",
}


def init_db() -> None:
    """创建当前空库结构并填入默认设置；已有当前版本数据库只校验版本。"""
    with connect() as conn:
        migrations.initialize(conn, SCHEMA_SQL)
        for key, value in DEFAULT_SETTINGS.items():
            conn.execute(
                "INSERT OR IGNORE INTO settings(key, value) VALUES(?, ?)",
                (key, value),
            )
        # Packaged updates always rebind internal component paths to the stable install root.
        if os.getenv("AI_CUSTOMER_MEDIA_CRAWLER_PATH", "").strip():
            set_setting(conn, "media_crawler_path", DEFAULT_MEDIA_CRAWLER_PATH)
            set_setting(conn, "media_crawler_db_path", DEFAULT_MEDIA_CRAWLER_DB)


def schema_version() -> dict[str, int]:
    with connect() as conn:
        return {
            "current": migrations.current_version(conn),
            "latest": migrations.latest_version(),
        }


def get_setting(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return str(row["value"]) if row else default


def set_setting(conn: sqlite3.Connection, key: str, value: Any) -> None:
    conn.execute(
        """
        INSERT INTO settings(key, value, updated_at)
        VALUES(?, ?, datetime('now', 'localtime'))
        ON CONFLICT(key) DO UPDATE SET
            value = excluded.value,
            updated_at = excluded.updated_at
        """,
        (key, str(value)),
    )
