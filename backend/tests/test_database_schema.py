from __future__ import annotations

import sqlite3

import pytest

from app import database, migrations


def test_current_schema_initializes_directly_and_keeps_existing_data() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    statements: list[str] = []
    conn.set_trace_callback(statements.append)
    migrations.initialize(conn, database.SCHEMA_SQL)

    assert migrations.current_version(conn) == migrations.latest_version() == 14
    tables = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert not tables.intersection({"schema_migrations", "traffic_campaigns", "agent_runs", "agent_run_events"})
    assert {"automation_plans", "content_assets", "runtime_jobs", "account_feature_bindings"} <= tables
    assert "publish_results" not in {row["name"] for row in conn.execute("PRAGMA table_info(video_jobs)")}
    assert not any(statement.lstrip().startswith(("ALTER ", "DROP ", "UPDATE ")) for statement in statements)
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    conn.execute("INSERT INTO user_accounts(platform, platform_user_id, nickname) VALUES('dy', 'current', '保留账号')")
    statements.clear()

    migrations.initialize(conn, database.SCHEMA_SQL)

    assert statements == ["PRAGMA user_version"]
    assert conn.execute("SELECT nickname FROM user_accounts").fetchone()[0] == "保留账号"
    conn.close()


@pytest.mark.parametrize("version", [0, 13, 15])
def test_initialization_rejects_incompatible_databases_without_modifying_them(version: int) -> None:
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE existing_data(value TEXT)")
    conn.execute("INSERT INTO existing_data(value) VALUES('不能覆盖')")
    conn.execute(f"PRAGMA user_version = {version}")

    with pytest.raises(RuntimeError, match="结构版本"):
        migrations.initialize(conn, database.SCHEMA_SQL)

    assert conn.execute("SELECT value FROM existing_data").fetchone()[0] == "不能覆盖"
    assert migrations.current_version(conn) == version
    assert conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type = 'table'").fetchone()[0] == 1
    conn.close()


def test_initialization_rolls_back_incomplete_schema() -> None:
    conn = sqlite3.connect(":memory:")

    with pytest.raises(sqlite3.OperationalError):
        migrations.initialize(conn, "CREATE TABLE partial(id INTEGER); INVALID SQL;")

    assert conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall() == []
    assert migrations.current_version(conn) == 0
    conn.close()
