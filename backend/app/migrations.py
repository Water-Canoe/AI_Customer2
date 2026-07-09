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


MIGRATIONS = (
    Migration(1, "initial_business_schema", _create_initial_schema),
    Migration(2, "drop_removed_agent_tables", _drop_removed_agent_tables),
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
