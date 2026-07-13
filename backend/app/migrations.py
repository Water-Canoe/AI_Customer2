from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from typing import Callable


MigrationAction = Callable[[sqlite3.Connection, str], None]


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    action: MigrationAction

    @property
    def checksum(self) -> str:
        value = f"{self.version}:{self.name}".encode("utf-8")
        return hashlib.sha256(value).hexdigest()


def _create_initial_schema(conn: sqlite3.Connection, schema_sql: str) -> None:
    # 初始结构固定为项目进入正式交付改造时的完整业务结构。
    conn.executescript(schema_sql)


def _drop_removed_agent_tables(conn: sqlite3.Connection, _: str) -> None:
    # 已移除的 AI 编排功能不再保留运行表，避免旧结构继续误导维护者。
    conn.execute("DROP TABLE IF EXISTS agent_run_events")
    conn.execute("DROP TABLE IF EXISTS agent_runs")


def _create_runtime_job_queue(conn: sqlite3.Connection, _: str) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS runtime_jobs (
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

        CREATE INDEX IF NOT EXISTS idx_runtime_jobs_status_priority
        ON runtime_jobs(status, priority DESC, created_at, id);

        CREATE INDEX IF NOT EXISTS idx_runtime_jobs_entity
        ON runtime_jobs(kind, entity_id, created_at DESC);

        CREATE UNIQUE INDEX IF NOT EXISTS idx_runtime_jobs_active_entity
        ON runtime_jobs(kind, entity_id)
        WHERE entity_id <> '' AND status IN ('queued', 'running');
        """
    )


def _create_content_workbench(conn: sqlite3.Connection, _: str) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS content_assets (
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
        );

        CREATE TABLE IF NOT EXISTS video_jobs (
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
            publish_results TEXT NOT NULL DEFAULT '[]',
            archived INTEGER NOT NULL DEFAULT 0,
            started_at TEXT,
            finished_at TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
        );

        CREATE TABLE IF NOT EXISTS video_job_assets (
            video_job_id TEXT NOT NULL,
            asset_id TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'material' CHECK(role IN ('material', 'audio', 'bgm')),
            sort_order INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(video_job_id, asset_id, role),
            FOREIGN KEY(video_job_id) REFERENCES video_jobs(id) ON DELETE CASCADE,
            FOREIGN KEY(asset_id) REFERENCES content_assets(id) ON DELETE RESTRICT
        );

        CREATE INDEX IF NOT EXISTS idx_content_assets_type_created
        ON content_assets(asset_type, deleted_at, created_at DESC);

        CREATE INDEX IF NOT EXISTS idx_video_jobs_status_created
        ON video_jobs(status, archived, created_at DESC);

        CREATE INDEX IF NOT EXISTS idx_video_job_assets_order
        ON video_job_assets(video_job_id, role, sort_order);
        """
    )


def _create_voice_profiles(conn: sqlite3.Connection, _: str) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS voice_profiles (
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

        CREATE INDEX IF NOT EXISTS idx_voice_profiles_provider_created
        ON voice_profiles(provider, deleted_at, created_at DESC);
        """
    )


def _classify_audio_assets(conn: sqlite3.Connection, _: str) -> None:
    conn.executescript(
        """
        ALTER TABLE content_assets ADD COLUMN purpose TEXT NOT NULL DEFAULT ''
        CHECK(purpose IN ('', 'background_music', 'voice_reference'));

        UPDATE content_assets
        SET purpose = 'voice_reference'
        WHERE asset_type = 'audio'
          AND id IN (SELECT reference_asset_id FROM voice_profiles);

        UPDATE content_assets
        SET purpose = 'background_music'
        WHERE asset_type = 'audio' AND purpose = '';

        CREATE INDEX IF NOT EXISTS idx_content_assets_purpose_created
        ON content_assets(purpose, deleted_at, created_at DESC);
        """
    )


def _create_content_publish(conn: sqlite3.Connection, _: str) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS publish_accounts (
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
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_publish_accounts_active_name
        ON publish_accounts(platform, name) WHERE deleted_at IS NULL;

        CREATE INDEX IF NOT EXISTS idx_publish_accounts_status
        ON publish_accounts(enabled, status, platform, created_at DESC);

        CREATE TABLE IF NOT EXISTS publish_tasks (
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

        CREATE INDEX IF NOT EXISTS idx_publish_tasks_status_created
        ON publish_tasks(status, created_at DESC);

        CREATE INDEX IF NOT EXISTS idx_publish_tasks_video_output
        ON publish_tasks(video_job_id, output_name, created_at DESC);

        CREATE TABLE IF NOT EXISTS publish_task_assets (
            task_id TEXT NOT NULL,
            asset_id TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'media'
                CHECK(role IN ('media', 'cover', 'portrait_cover', 'landscape_cover')),
            sort_order INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(task_id, asset_id, role),
            FOREIGN KEY(task_id) REFERENCES publish_tasks(id) ON DELETE CASCADE,
            FOREIGN KEY(asset_id) REFERENCES content_assets(id) ON DELETE RESTRICT
        );

        CREATE INDEX IF NOT EXISTS idx_publish_task_assets_order
        ON publish_task_assets(task_id, role, sort_order);
        """
    )


def _create_automation_plans(conn: sqlite3.Connection, _: str) -> None:
    # 自动化只保存声明式配置和运行快照，实际工作仍由 runtime_jobs 执行。
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS automation_plans (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            plan_type TEXT NOT NULL CHECK(plan_type IN ('keyword_lead', 'message')),
            weekdays TEXT NOT NULL DEFAULT '[]',
            run_time TEXT NOT NULL,
            config TEXT NOT NULL DEFAULT '{}',
            enabled INTEGER NOT NULL DEFAULT 0,
            archived INTEGER NOT NULL DEFAULT 0,
            last_triggered_at TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
        );

        CREATE TABLE IF NOT EXISTS automation_runs (
            id TEXT PRIMARY KEY,
            plan_id TEXT,
            plan_name TEXT NOT NULL,
            plan_type TEXT NOT NULL CHECK(plan_type IN ('keyword_lead', 'message')),
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
            stop_requested INTEGER NOT NULL DEFAULT 0,
            error TEXT NOT NULL DEFAULT '',
            started_at TEXT,
            finished_at TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
            FOREIGN KEY(plan_id) REFERENCES automation_plans(id) ON DELETE SET NULL
        );

        CREATE TABLE IF NOT EXISTS automation_run_items (
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

        CREATE TABLE IF NOT EXISTS message_send_attempts (
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

        CREATE UNIQUE INDEX IF NOT EXISTS idx_automation_scheduled_once
        ON automation_runs(plan_id, scheduled_at)
        WHERE trigger_type = 'scheduled' AND scheduled_at IS NOT NULL;

        CREATE UNIQUE INDEX IF NOT EXISTS idx_automation_active_plan
        ON automation_runs(plan_id)
        WHERE plan_id IS NOT NULL AND status IN ('queued', 'running');

        CREATE INDEX IF NOT EXISTS idx_automation_plans_enabled_time
        ON automation_plans(enabled, archived, run_time);

        CREATE INDEX IF NOT EXISTS idx_automation_runs_plan_status
        ON automation_runs(plan_id, status, created_at DESC);

        CREATE INDEX IF NOT EXISTS idx_automation_run_items_run_status
        ON automation_run_items(run_id, status, id);

        CREATE INDEX IF NOT EXISTS idx_message_send_attempts_time
        ON message_send_attempts(attempted_at, lead_account_id);

        ALTER TABLE crawl_jobs ADD COLUMN automation_managed INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE analysis_jobs ADD COLUMN auto_delete INTEGER NOT NULL DEFAULT -1;
        ALTER TABLE message_batches ADD COLUMN source TEXT NOT NULL DEFAULT 'manual';
        ALTER TABLE message_batches ADD COLUMN automation_run_id TEXT NOT NULL DEFAULT '';
        """
    )


def _add_automation_plan_order(conn: sqlite3.Connection, _: str) -> None:
    # 计划顺序同时用于页面展示和运行队列优先级。
    conn.execute("ALTER TABLE automation_plans ADD COLUMN sort_order INTEGER NOT NULL DEFAULT 0")
    rows = conn.execute("SELECT id FROM automation_plans WHERE archived = 0 ORDER BY created_at DESC, id DESC").fetchall()
    for index, row in enumerate(rows):
        conn.execute("UPDATE automation_plans SET sort_order = ? WHERE id = ?", (index, row["id"]))
        conn.execute(
            "UPDATE runtime_jobs SET resource = 'automation', priority = ? WHERE kind = 'automation_run' AND entity_id IN (SELECT id FROM automation_runs WHERE plan_id = ?)",
            (-index, row["id"]),
        )
    conn.execute("CREATE INDEX idx_automation_plans_sort_order ON automation_plans(archived, sort_order, id)")


MIGRATIONS = (
    Migration(1, "initial_business_schema", _create_initial_schema),
    Migration(2, "drop_removed_agent_tables", _drop_removed_agent_tables),
    Migration(3, "create_runtime_job_queue", _create_runtime_job_queue),
    Migration(4, "create_content_workbench", _create_content_workbench),
    Migration(5, "create_voice_profiles", _create_voice_profiles),
    Migration(6, "classify_audio_assets", _classify_audio_assets),
    Migration(7, "create_content_publish", _create_content_publish),
    Migration(8, "create_automation_plans", _create_automation_plans),
    Migration(9, "add_automation_plan_order", _add_automation_plan_order),
)


def ensure_migration_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            checksum TEXT NOT NULL,
            applied_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
        )
        """
    )


def applied_migrations(conn: sqlite3.Connection) -> dict[int, sqlite3.Row]:
    ensure_migration_table(conn)
    rows = conn.execute(
        "SELECT version, name, checksum, applied_at FROM schema_migrations ORDER BY version"
    ).fetchall()
    return {int(row["version"]): row for row in rows}


def pending_migrations(conn: sqlite3.Connection) -> list[Migration]:
    applied = applied_migrations(conn)
    _validate_applied_migrations(applied)
    return [migration for migration in MIGRATIONS if migration.version not in applied]


def apply_migrations(conn: sqlite3.Connection, schema_sql: str) -> list[int]:
    pending = pending_migrations(conn)
    applied_versions: list[int] = []
    for migration in pending:
        migration.action(conn, schema_sql)
        conn.execute(
            "INSERT INTO schema_migrations(version, name, checksum) VALUES(?, ?, ?)",
            (migration.version, migration.name, migration.checksum),
        )
        applied_versions.append(migration.version)
    conn.execute(f"PRAGMA user_version = {latest_version()}")
    return applied_versions


def current_version(conn: sqlite3.Connection) -> int:
    applied = applied_migrations(conn)
    return max(applied, default=0)


def latest_version() -> int:
    return max((migration.version for migration in MIGRATIONS), default=0)


def has_business_data(conn: sqlite3.Connection) -> bool:
    rows = conn.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type = 'table'
          AND name NOT LIKE 'sqlite_%'
          AND name NOT IN ('schema_migrations', 'settings')
        """
    ).fetchall()
    for row in rows:
        table = str(row["name"]).replace('"', '""')
        count = conn.execute(f'SELECT 1 FROM "{table}" LIMIT 1').fetchone()
        if count:
            return True
    return False


def _validate_applied_migrations(applied: dict[int, sqlite3.Row]) -> None:
    known = {migration.version: migration for migration in MIGRATIONS}
    for version, row in applied.items():
        migration = known.get(version)
        if migration is None:
            raise RuntimeError(f"数据库版本 {version} 高于当前程序支持范围")
        if str(row["name"]) != migration.name or str(row["checksum"]) != migration.checksum:
            raise RuntimeError(f"数据库迁移 {version} 的名称或校验值不一致")
