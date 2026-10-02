from __future__ import annotations

import sqlite3


SCHEMA_VERSION = 14


def current_version(conn: sqlite3.Connection) -> int:
    """读取 SQLite 自带的结构版本，不维护历史迁移账本。"""
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def latest_version() -> int:
    """返回程序和打包清单支持的当前结构版本。"""
    return SCHEMA_VERSION


def initialize(conn: sqlite3.Connection, schema_sql: str) -> None:
    """仅为新空库创建结构；已有当前库保持原样，不转换旧版本数据。"""
    version = current_version(conn)
    if version == SCHEMA_VERSION:
        return
    has_schema = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE name NOT GLOB 'sqlite_*' LIMIT 1"
    ).fetchone()
    if version or has_schema:
        raise RuntimeError(f"数据库结构版本 {version} 与当前版本 {SCHEMA_VERSION} 不一致，请重新创建测试数据库")
    # 所有表和版本号在同一事务中建立，失败时不会留下半成品结构。
    try:
        conn.executescript(f"BEGIN IMMEDIATE;\n{schema_sql}\nPRAGMA user_version = {SCHEMA_VERSION};\nCOMMIT;")
    except Exception:
        conn.rollback()
        raise
