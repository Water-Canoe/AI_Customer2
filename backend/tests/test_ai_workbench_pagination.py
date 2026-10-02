from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager

import pytest

from app import database
from app.services import ai_service


@pytest.fixture
def workbench_db(monkeypatch: pytest.MonkeyPatch):
    # 内存数据库隔离真实客户数据，同时记录实际 SQL 查询数。
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(database.SCHEMA_SQL)

    @contextmanager
    def connect(*_args, **_kwargs):
        yield conn

    monkeypatch.setattr(database, "connect", connect)
    yield conn
    conn.close()


@pytest.mark.parametrize("tab", ["competitors", "leads"])
def test_filtered_targets_include_records_beyond_old_limit(workbench_db: sqlite3.Connection, tab: str) -> None:
    workbench_db.executemany(
        """
        INSERT INTO user_accounts(platform, platform_user_id, nickname, account_role, competitor_status)
        VALUES('dy', ?, ?, 'competitor_candidate', '竞品')
        """,
        [(f"account-{index}", "ÄLTESTER" if index == 0 else f"账号 {index}") for index in range(1001)],
    )
    if tab == "leads":
        workbench_db.execute("INSERT INTO lead_user_accounts(account_id, screening_status) SELECT id, '目标客户' FROM user_accounts")
    target_type = "competitor" if tab == "competitors" else "lead"
    output_key = "is_competitor" if tab == "competitors" else "is_customer"
    workbench_db.execute(
        """
        INSERT INTO analysis_jobs(id, target_type, target_id, status, output_payload)
        VALUES('oldest-target-job', ?, 1, 'succeeded', ?)
        """,
        (target_type, json.dumps({output_key: False, "reason": "旧记录结论"})),
    )

    result_label = "非竞品" if tab == "competitors" else "非客户"
    result = ai_service.ai_workbench(tab, keyword="ältester", status="已分析", result=result_label)

    assert result["total"] == 1
    assert result["items"][0]["id"] == 1
    assert result["items"][0]["result_reason"] == "旧记录结论"
    beyond_last = ai_service.ai_workbench(tab, keyword="ältester", page=2)
    assert beyond_last["items"] == [] and beyond_last["total"] == 1


@pytest.mark.parametrize("tab", ["history", "failed"])
def test_history_paginates_without_per_job_queries_or_full_json_parsing(
    workbench_db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch, tab: str,
) -> None:
    workbench_db.executemany(
        """
        INSERT INTO analysis_jobs(id, target_type, target_id, status, error, output_payload, updated_at, created_at)
        VALUES(?, 'competitor', ?, 'failed', ?, ?, ?, ?)
        """,
        [(f"job-{index}", index + 1, "API Key 缺失" if index == 0 else "network error",
          json.dumps({"reason": "最旧结果" if index == 0 else "常规结果"}),
          f"2026-01-{2 if index else 1:02d} 00:00:00", f"2026-01-{2 if index else 1:02d} 00:00:00")
         for index in range(2001)],
    )
    queries: list[str] = []
    workbench_db.set_trace_callback(queries.append)
    parsed: list[object] = []
    original_json_payload = ai_service._json_payload

    def count_json_payload(value):
        parsed.append(value)
        return original_json_payload(value)

    monkeypatch.setattr(ai_service, "_json_payload", count_json_payload)
    result = ai_service.ai_workbench(tab, page_size=10)

    assert result["total"] == 2001 and len(result["items"]) == 10
    assert len(parsed) == 20  # 当前页各解析输入、输出一次。
    assert sum(query.lstrip().startswith("SELECT") for query in queries) == 6
    assert not any(query.startswith("SELECT platform, nickname") for query in queries)
    filters = {"keyword": "最旧结果", "status": "failed"} if tab == "history" else {"keyword": "竞品账号 #1", "status": "配置错误"}
    oldest = ai_service.ai_workbench(tab, **filters)
    assert oldest["total"] == 1
    assert oldest["items"][0]["id"] == "job-0"
    assert oldest["items"][0]["target_summary"] == "对象已删除"
    assert "platform" not in oldest["items"][0]
    assert ai_service.ai_workbench(tab, result="竞品")["total"] == 0


def test_lead_page_does_not_repeat_analysis_history_lookup_for_each_evidence(workbench_db: sqlite3.Connection) -> None:
    workbench_db.executemany(
        "INSERT INTO user_accounts(id, platform, platform_user_id, nickname) VALUES(?, 'dy', ?, ?)",
        [(index, str(index), f"客户{index}") for index in range(1, 201)],
    )
    workbench_db.execute(
        "INSERT INTO lead_user_accounts(id, account_id, screening_status, reason, updated_at) "
        "SELECT id, id, '目标客户', '既有结论', '2026-01-01 00:00:00' FROM user_accounts"
    )
    workbench_db.execute(
        "INSERT INTO contents(id, platform, content_id, author_account_id, title) "
        "SELECT id, platform, platform_user_id, id, '来源视频' FROM user_accounts"
    )
    workbench_db.executemany(
        "INSERT INTO comments(id, platform, comment_id, content_id, body) VALUES(?, 'dy', ?, ?, ?)",
        [((lead_id - 1) * 25 + index + 1, f"{lead_id}-{index}", lead_id, f"证据{index}")
         for lead_id in range(1, 201) for index in range(25)],
    )
    workbench_db.execute(
        "INSERT INTO lead_sources(lead_account_id, source_account_id, content_id, comment_id, source_type) "
        "SELECT content_id, content_id, content_id, id, 'comment' FROM comments"
    )
    workbench_db.executemany(
        "INSERT INTO analysis_jobs(id, target_type, target_id, status, output_payload, updated_at, created_at) "
        "VALUES(?, 'lead', ?, 'succeeded', ?, ?, ?)",
        [(f"job-{lead_id}-{index}", lead_id, json.dumps({"is_customer": index != 19, "reason": f"分析{index}"}),
          f"2026-01-02 00:00:{index:02d}", f"2026-01-02 00:00:{index:02d}")
         for lead_id in range(1, 201) for index in range(20)],
    )
    steps = 0

    def count_steps() -> int:
        nonlocal steps
        steps += 1000
        return 0

    # VM 步数不受机器快慢影响；逐条证据重查分析历史会超过 200 万步。
    workbench_db.set_progress_handler(count_steps, 1000)
    page = ai_service.ai_workbench("leads", page_size=10)
    workbench_db.set_progress_handler(None, 0)

    assert steps < 700_000
    assert page["total"] == 200 and [item["id"] for item in page["items"]] == list(range(200, 190, -1))
    for item in page["items"]:
        assert item["job_id"] == f"job-{item['id']}-19"
        assert item["analysis_status"] == "已分析" and item["result_label"] == "非客户"
        assert item["result_reason"] == "分析19" and item["source_count"] == 25
        assert set(item["comment_samples"].split(",")) == {f"证据{index}" for index in range(25)}
