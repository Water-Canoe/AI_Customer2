from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


BACKEND_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = BACKEND_ROOT.parent
DEFAULT_DB_PATH = BACKEND_ROOT / "runtime" / "ai_customer.sqlite3"
DEFAULT_MEDIA_CRAWLER_PATH = Path(r"D:\Dev\Projects\MediaCrawler")
DEFAULT_MEDIA_CRAWLER_DB = DEFAULT_MEDIA_CRAWLER_PATH / "database" / "sqlite_tables.db"


def get_db_path() -> Path:
    """Return the active project SQLite path."""
    return Path(os.getenv("AI_CUSTOMER_DB", str(DEFAULT_DB_PATH)))


@contextmanager
def connect(db_path: Path | None = None) -> Iterator[sqlite3.Connection]:
    """Open a SQLite connection with dict-like rows and FK checks."""
    path = db_path or get_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
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


def now_sql() -> str:
    return "datetime('now', 'localtime')"


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS crawl_jobs (
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
);

CREATE TABLE IF NOT EXISTS task_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    level TEXT NOT NULL DEFAULT 'info',
    message TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    FOREIGN KEY(task_id) REFERENCES crawl_jobs(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS user_accounts (
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

CREATE TABLE IF NOT EXISTS contents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform TEXT NOT NULL,
    content_id TEXT NOT NULL,
    author_account_id INTEGER,
    title TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    content_url TEXT NOT NULL DEFAULT '',
    like_count INTEGER,
    comment_count INTEGER,
    source_keyword TEXT NOT NULL DEFAULT '',
    task_id TEXT,
    raw_payload TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    UNIQUE(platform, content_id),
    FOREIGN KEY(author_account_id) REFERENCES user_accounts(id) ON DELETE SET NULL,
    FOREIGN KEY(task_id) REFERENCES crawl_jobs(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS comments (
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

CREATE TABLE IF NOT EXISTS account_sources (
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

CREATE TABLE IF NOT EXISTS lead_user_accounts (
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

CREATE TABLE IF NOT EXISTS lead_sources (
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

CREATE TABLE IF NOT EXISTS lead_status_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_account_id INTEGER NOT NULL,
    from_status TEXT NOT NULL DEFAULT '',
    to_status TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    FOREIGN KEY(lead_account_id) REFERENCES lead_user_accounts(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS raw_source_refs (
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

CREATE TABLE IF NOT EXISTS deleted_identities (
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

CREATE INDEX IF NOT EXISTS idx_deleted_identities_lookup
ON deleted_identities(entity_type, platform, identifier_type, identifier_value);

CREATE TABLE IF NOT EXISTS analysis_jobs (
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
);

CREATE TABLE IF NOT EXISTS deletion_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type TEXT NOT NULL,
    entity_id INTEGER NOT NULL,
    hard_delete INTEGER NOT NULL DEFAULT 0,
    detail TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
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
    "account_analysis_content_count": "5",
    "ai_analysis_concurrency": "3",
    "unreplied_reminder_days": "3",
    "douyin_detail_sleep_seconds": "2",
    "max_concurrency": "1",
    "headless": "false",
    "auto_analyze_competitors": "false",
    "auto_delete_non_competitors": "false",
    "auto_analyze_leads": "false",
    "auto_delete_non_customers": "false",
    "icp_profile": json.dumps(
        {
            "product": "",
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
}


def init_db() -> None:
    """Create all tables and seed default settings."""
    with connect() as conn:
        conn.executescript(SCHEMA_SQL)
        _ensure_column(conn, "user_accounts", "competitor_reason", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "user_accounts", "content_total_count", "INTEGER")
        _ensure_column(conn, "lead_user_accounts", "manual_follow_status", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "analysis_jobs", "raw_output", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "analysis_jobs", "prompt_version", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "analysis_jobs", "system_prompt", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "analysis_jobs", "user_prompt", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "analysis_jobs", "model", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "analysis_jobs", "base_url", "TEXT NOT NULL DEFAULT ''")
        for key, value in DEFAULT_SETTINGS.items():
            conn.execute(
                "INSERT OR IGNORE INTO settings(key, value) VALUES(?, ?)",
                (key, value),
            )


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    columns = {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


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
