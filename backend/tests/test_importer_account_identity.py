from __future__ import annotations

import sqlite3

import pytest

from app import database, migrations
from app.services import importer


@pytest.mark.parametrize("match", ["sec_uid", "profile_url", "platform_user_id"])
def test_account_identity_priority_and_indexed_lookup_cost(match: str) -> None:
    step_counts: list[int] = []
    for size in (1000, 10000):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        migrations.initialize(conn, database.SCHEMA_SQL)
        # 更早的跨平台账号不能归并；同平台同时命中时优先 sec_uid，其次主页，最后原生 ID。
        sec_uid = "shared-sec" if match != "platform_user_id" else ""
        profile_url = "https://www.douyin.com/user/shared-sec"
        conn.executemany(
            "INSERT INTO user_accounts(platform, platform_user_id, sec_uid, profile_url) VALUES(?, ?, ?, ?)",
            [
                ("ks", "native", "shared-sec", profile_url),
                ("dy", "empty-identifiers", "", ""),
                ("dy", "native", "", ""),
                ("dy", "profile-first", "", profile_url),
                ("dy", "profile-second", "", profile_url),
                ("dy", "sec-first", "shared-sec" if match == "sec_uid" else "other-sec", ""),
                ("dy", "sec-second", "shared-sec" if match == "sec_uid" else "other-sec", ""),
            ],
        )
        conn.executemany(
            "INSERT INTO user_accounts(platform, platform_user_id, sec_uid, profile_url) VALUES('dy', ?, ?, ?)",
            [(f"unrelated-{index}", f"other-{index}", f"https://example.test/{index}") for index in range(size)],
        )
        steps = 0

        def count_step() -> int:
            nonlocal steps
            steps += 1
            return 0

        # 统计实际 SQLite VM 步数，读取和更新都计入；回归全平台扫描会明显超过上限。
        conn.set_progress_handler(count_step, 1)
        account_id = importer._upsert_account(conn, "dy", "native", sec_uid=sec_uid, nickname="更新昵称")
        conn.set_progress_handler(None, 0)
        expected_id = {"sec_uid": 6, "profile_url": 4, "platform_user_id": 3}[match]
        assert account_id == expected_id
        assert conn.execute("SELECT nickname FROM user_accounts WHERE id = ?", (expected_id,)).fetchone()[0] == "更新昵称"
        assert conn.execute("SELECT COUNT(*) FROM user_accounts").fetchone()[0] == size + 7
        step_counts.append(steps)
        conn.close()
    assert max(step_counts) < 500
    assert step_counts[1] <= step_counts[0] + 50


def test_current_schema_has_nonunique_identity_indexes() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    migrations.initialize(conn, database.SCHEMA_SQL)
    indexes = {row["name"]: row["unique"] for row in conn.execute("PRAGMA index_list(user_accounts)")}
    for column in ("sec_uid", "profile_url"):
        index = f"idx_user_accounts_platform_{column}"
        assert indexes[index] == 0
        assert [row["name"] for row in conn.execute(f"PRAGMA index_info({index})")] == ["platform", column]
    conn.close()
