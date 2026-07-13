from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

import pytest


BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))


def prepare_project(tmp_path: Path) -> None:
    os.environ["AI_CUSTOMER_DB"] = str(tmp_path / "automation.sqlite3")
    from app import database

    database.init_db()


def keyword_plan_payload(**overrides: object):
    from app.schemas import AutomationPlanCreate

    data = {
        "name": "每日关键词获客",
        "plan_type": "keyword_lead",
        "weekdays": [1, 2, 3, 4, 5, 6, 7],
        "run_time": "09:00",
        "enabled": True,
        "config": {
            "platform": "dy",
            "keywords": ["关键词A", "关键词B", "关键词C"],
            "keyword_count": 2,
            "discovery_content_count": 10,
            "competitor_limit": 10,
            "competitor_content_count": 5,
            "comment_count": 20,
            "collect_sub_comments": False,
            "auto_analyze_leads": True,
        },
    }
    data.update(overrides)
    return AutomationPlanCreate.model_validate(data)


def test_scheduled_window_triggers_only_when_time_is_crossed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    prepare_project(tmp_path)
    from app.services import automation_workbench, license_service

    plan = automation_workbench.create_plan(keyword_plan_payload())
    calls: list[tuple[str, str, datetime | None]] = []

    def fake_create_run(plan_id: str, trigger_type: str, scheduled_at: datetime | None):
        calls.append((plan_id, trigger_type, scheduled_at))
        return {"id": "run-1"}

    monkeypatch.setattr(automation_workbench, "create_run", fake_create_run)
    monkeypatch.setattr(license_service, "ensure_authorized", lambda: {"authorized": True})
    crossed = automation_workbench.trigger_due_plans(datetime(2026, 7, 13, 8, 59, 50), datetime(2026, 7, 13, 9, 0, 5))
    missed = automation_workbench.trigger_due_plans(datetime(2026, 7, 13, 9, 0, 5), datetime(2026, 7, 13, 9, 30, 0))

    assert crossed == [{"id": "run-1"}]
    assert missed == []
    assert calls == [(plan["id"], "scheduled", datetime(2026, 7, 13, 9, 0, 0))]


def test_keyword_selection_prefers_never_run_then_oldest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    prepare_project(tmp_path)
    from app import database
    from app.services import automation_workbench, job_queue

    monkeypatch.setattr(job_queue, "enqueue_automation_run", lambda run_id: {"id": f"runtime-{run_id}"})
    plan = automation_workbench.create_plan(keyword_plan_payload())
    first = automation_workbench.create_run(plan["id"])
    assert [item["keyword"] for item in first["items"]] == ["关键词A", "关键词B"]

    with database.connect() as conn:
        conn.execute("UPDATE automation_runs SET status = 'completed', finished_at = datetime('now', 'localtime') WHERE id = ?", (first["id"],))
        conn.execute("UPDATE automation_run_items SET status = 'succeeded' WHERE run_id = ?", (first["id"],))
    second = automation_workbench.create_run(plan["id"])

    assert [item["keyword"] for item in second["items"]] == ["关键词C", "关键词A"]
    assert second["config_snapshot"]["auto_analyze_leads"] is True


def test_queued_run_can_be_cancelled_without_blocking_plan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    prepare_project(tmp_path)
    from app.services import automation_workbench, job_queue

    monkeypatch.setattr(job_queue, "enqueue_automation_run", lambda run_id: {"id": f"runtime-{run_id}"})
    plan = automation_workbench.create_plan(keyword_plan_payload())
    run = automation_workbench.create_run(plan["id"])

    cancelled = automation_workbench.cancel_run(run["id"])
    next_run = automation_workbench.create_run(plan["id"])

    assert cancelled["status"] == "cancelled"
    assert next_run["status"] == "queued"


def test_same_plan_active_and_same_schedule_are_not_duplicated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    prepare_project(tmp_path)
    from app.services import automation_workbench, job_queue

    monkeypatch.setattr(job_queue, "enqueue_automation_run", lambda run_id: {"id": f"runtime-{run_id}"})
    plan = automation_workbench.create_plan(keyword_plan_payload())
    scheduled_at = datetime(2026, 7, 13, 9, 0, 0)
    first = automation_workbench.create_run(plan["id"], "scheduled", scheduled_at)
    duplicate = automation_workbench.create_run(plan["id"], "scheduled", scheduled_at)
    active_skip = automation_workbench.create_run(plan["id"], "manual")

    assert duplicate["id"] == first["id"]
    assert active_skip["status"] == "skipped"


def test_keyword_failure_continues_and_finishes_partial(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    prepare_project(tmp_path)
    from app import database
    from app.schemas import KeywordLeadPlanConfig
    from app.services import automation_workbench

    run_id = "partial-run"
    with database.connect() as conn:
        conn.execute(
            "INSERT INTO automation_runs(id, plan_name, plan_type, trigger_type, config_snapshot, status, total_count) VALUES(?, '测试', 'keyword_lead', 'manual', '{}', 'running', 2)",
            (run_id,),
        )
        conn.execute("INSERT INTO automation_run_items(run_id, keyword) VALUES(?, '失败词')", (run_id,))
        conn.execute("INSERT INTO automation_run_items(run_id, keyword) VALUES(?, '成功词')", (run_id,))

    def fake_run_item(_: str, item: dict[str, object], __: KeywordLeadPlanConfig) -> None:
        if item["keyword"] == "失败词":
            raise RuntimeError("采集失败")
        automation_workbench._finish_item(int(item["id"]), "succeeded", "")

    monkeypatch.setattr(automation_workbench, "_run_keyword_item", fake_run_item)
    config = keyword_plan_payload().config
    automation_workbench._run_keyword_plan(run_id, config)
    result = automation_workbench.get_run(run_id)

    assert result["status"] == "partial"
    assert [item["status"] for item in result["items"]] == ["failed", "succeeded"]


def test_message_candidates_sort_intention_then_oldest_and_deduplicate(tmp_path: Path) -> None:
    prepare_project(tmp_path)
    from app import database
    from app.schemas import MessagePlanConfig
    from app.services import message_workbench

    with database.connect() as conn:
        for index, intention in enumerate(["中", "高", "高", "低"]):
            account_id = conn.execute(
                "INSERT INTO user_accounts(platform, platform_user_id, nickname, profile_url) VALUES('dy', ?, ?, ?)",
                (f"candidate-{index}", f"客户{index}", f"https://www.douyin.com/user/{index}"),
            ).lastrowid
            lead_id = conn.execute(
                "INSERT INTO lead_user_accounts(account_id, screening_status, follow_status, intention, script, created_at) VALUES(?, '目标客户', '未私信', ?, 'AI话术', ?)",
                (account_id, intention, f"2026-07-0{index + 1} 08:00:00"),
            ).lastrowid
            conn.execute("INSERT INTO lead_sources(lead_account_id, keyword, source_type) VALUES(?, '获客', 'competitor_comment')", (lead_id,))
            if index == 1:
                conn.execute("INSERT INTO lead_sources(lead_account_id, keyword, source_type) VALUES(?, '另一个词', 'competitor_comment')", (lead_id,))
        config = MessagePlanConfig(keyword_scope="all", count=10, script_mode="ai")
        candidates = message_workbench._scheduled_candidates(conn, config)

    assert [item["nickname"] for item in candidates] == ["客户1", "客户2", "客户0", "客户3"]
    assert len({item["lead_id"] for item in candidates}) == 4


def test_message_quota_counts_failures_and_prevents_same_day_retry(tmp_path: Path) -> None:
    prepare_project(tmp_path)
    from app import database
    from app.services import automation_workbench, message_workbench

    with database.connect() as conn:
        account_ids = [
            conn.execute("INSERT INTO user_accounts(platform, platform_user_id) VALUES('dy', ?)", (f"quota-{index}",)).lastrowid
            for index in range(3)
        ]
        lead_ids = [conn.execute("INSERT INTO lead_user_accounts(account_id) VALUES(?)", (account_id,)).lastrowid for account_id in account_ids]
    automation_workbench.update_message_limits(2, 2)

    first = message_workbench.reserve_message_attempt(int(lead_ids[0]), "test", "one")
    message_workbench.finish_message_attempt(first, "failed", "页面状态不明确")
    second = message_workbench.reserve_message_attempt(int(lead_ids[1]), "test", "two")
    message_workbench.finish_message_attempt(second, "succeeded")

    with pytest.raises(message_workbench.MessageQuotaReached, match="今日私信额度已用完"):
        message_workbench.reserve_message_attempt(int(lead_ids[2]), "test", "three")
    with pytest.raises(message_workbench.MessageQuotaReached):
        message_workbench.reserve_message_attempt(int(lead_ids[0]), "test", "retry")
    limits = automation_workbench.get_message_limits()
    assert limits["used_today"] == 2
    assert limits["remaining_today"] == 0


def test_message_plan_requires_limits_and_respects_fill_only_switch(tmp_path: Path) -> None:
    prepare_project(tmp_path)
    from app import database
    from app.schemas import AutomationPlanCreate
    from app.services import automation_workbench

    payload = AutomationPlanCreate.model_validate(
        {
            "name": "积压客户私信",
            "plan_type": "message",
            "weekdays": [1],
            "run_time": "10:00",
            "enabled": True,
            "config": {"keyword_scope": "all", "count": 20, "script_mode": "fixed", "fixed_script": "你好"},
        }
    )
    with pytest.raises(ValueError, match="请先配置"):
        automation_workbench.create_plan(payload)

    automation_workbench.update_message_limits(100, 40)
    with database.connect() as conn:
        database.set_setting(conn, "auto_dm_fill_only", "true")
    with pytest.raises(ValueError, match="只填内容不发送"):
        automation_workbench.create_plan(payload)


def test_selected_keyword_message_config_uses_message_schema() -> None:
    from app.schemas import AutomationPlanCreate, MessagePlanConfig

    payload = AutomationPlanCreate.model_validate(
        {
            "name": "指定关键词私信",
            "plan_type": "message",
            "weekdays": [1],
            "run_time": "10:00",
            "config": {"keyword_scope": "selected", "keywords": ["获客"], "script_mode": "fixed", "fixed_script": "你好"},
        }
    )

    assert isinstance(payload.config, MessagePlanConfig)
