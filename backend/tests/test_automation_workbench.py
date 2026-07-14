from __future__ import annotations

import os
import sqlite3
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


def traffic_plan_payload(**overrides: object):
    from app.schemas import AutomationPlanCreate

    data = {
        "name": "自动引流",
        "plan_type": "traffic",
        "weekdays": [1, 2, 3, 4, 5, 6, 7],
        "run_time": "09:00",
        "enabled": True,
        "config": {
            "platform": "dy",
            "source_mode": "random_feed",
            "source_value": "",
            "action_like": False,
            "action_collect": False,
            "action_follow": False,
            "action_comment_text": False,
            "action_comment_image": False,
            "round_video_limit": 5,
        },
    }
    data.update(overrides)
    return AutomationPlanCreate.model_validate(data)


@pytest.mark.parametrize(
    ("source_mode", "source_value"),
    [
        ("random_feed", ""),
        ("competitor_videos", ""),
        ("collected_keyword", "AI客服"),
        ("search_keyword", "AI获客"),
    ],
)
def test_traffic_plan_schema_supports_douyin_sources_and_pure_browsing(source_mode: str, source_value: str) -> None:
    from app.schemas import TrafficAutomationPlanConfig

    payload = traffic_plan_payload(
        config={
            "platform": "dy",
            "source_mode": source_mode,
            "source_value": source_value,
            "round_video_limit": 200,
        }
    )

    assert isinstance(payload.config, TrafficAutomationPlanConfig)
    assert payload.config.source_mode == source_mode
    assert payload.config.round_video_limit == 200
    assert not any(
        (
            payload.config.action_like,
            payload.config.action_collect,
            payload.config.action_follow,
            payload.config.action_comment_text,
            payload.config.action_comment_image,
        )
    )


@pytest.mark.parametrize(
    ("config", "message"),
    [
        ({"platform": "ks", "source_mode": "search_keyword", "source_value": "AI客服"}, "只支持随机推荐流"),
        ({"platform": "ks", "source_mode": "random_feed", "action_comment_image": True}, "不支持评论图片"),
        ({"platform": "dy", "source_mode": "search_keyword", "source_value": ""}, "必须填写关键词"),
        ({"platform": "dy", "source_mode": "random_feed", "round_video_limit": 201}, "less than or equal to 200"),
    ],
)
def test_traffic_plan_schema_rejects_invalid_platform_dependencies(config: dict[str, object], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        traffic_plan_payload(config=config)


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


def test_scheduler_start_only_catches_up_ten_minutes() -> None:
    from app.services import automation_workbench

    now = datetime(2026, 7, 13, 9, 8, 30)
    assert automation_workbench._scheduler_scan_start(now) == datetime(2026, 7, 13, 8, 58, 30)


def test_scheduled_plan_failure_does_not_block_later_plan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    prepare_project(tmp_path)
    from app.services import automation_workbench, license_service

    first = automation_workbench.create_plan(keyword_plan_payload(name="异常计划"))
    second = automation_workbench.create_plan(keyword_plan_payload(name="正常计划"))
    calls: list[str] = []

    def fake_create_run(plan_id: str, _trigger_type: str, _scheduled_at: datetime | None):
        calls.append(plan_id)
        if plan_id == first["id"]:
            raise RuntimeError("模拟调度异常")
        return {"id": "second-run", "plan_id": plan_id}

    monkeypatch.setattr(automation_workbench, "create_run", fake_create_run)
    monkeypatch.setattr(license_service, "ensure_authorized", lambda: {"authorized": True})
    runs = automation_workbench.trigger_due_plans(
        datetime(2026, 7, 13, 8, 59, 50),
        datetime(2026, 7, 13, 9, 0, 5),
    )

    assert calls == [first["id"], second["id"]]
    assert runs[0]["status"] == "failed"
    assert runs[1]["id"] == "second-run"


def test_drag_order_serializes_same_time_plans(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    prepare_project(tmp_path)
    from app import database
    from app.schemas import AutomationPlanCreate
    from app.services import automation_workbench, job_queue, license_service

    automation_workbench.update_message_limits(100, 40)
    message_plan = automation_workbench.create_plan(
        AutomationPlanCreate.model_validate(
            {
                "name": "自动私信",
                "plan_type": "message",
                "weekdays": [1, 2, 3, 4, 5, 6, 7],
                "run_time": "09:00",
                "enabled": True,
                "config": {"keyword_scope": "all", "count": 20, "script_mode": "fixed", "fixed_script": "你好"},
            }
        )
    )
    lead_plan = automation_workbench.create_plan(keyword_plan_payload(name="关键词自动获客"))
    traffic_plan = automation_workbench.create_plan(traffic_plan_payload(name="自动引流"))
    ordered = automation_workbench.reorder_plans([traffic_plan["id"], lead_plan["id"], message_plan["id"]])

    monkeypatch.setattr(license_service, "ensure_authorized", lambda: {"authorized": True})
    monkeypatch.setattr(license_service, "ensure_authorized_for", lambda scope: {"authorized": True, "scope": scope})
    runs = automation_workbench.trigger_due_plans(datetime(2026, 7, 13, 8, 59, 50), datetime(2026, 7, 13, 9, 0, 5))
    with database.connect() as conn:
        queued = conn.execute(
            """
            SELECT r.plan_id, j.resource, j.priority
            FROM runtime_jobs j JOIN automation_runs r ON r.id = j.entity_id
            WHERE j.kind = 'automation_run' ORDER BY j.priority DESC
            """
        ).fetchall()

    assert [plan["id"] for plan in ordered["items"]] == [traffic_plan["id"], lead_plan["id"], message_plan["id"]]
    assert [run["plan_id"] for run in runs] == [traffic_plan["id"], lead_plan["id"], message_plan["id"]]
    assert [(row["plan_id"], row["resource"], row["priority"]) for row in queued] == [
        (traffic_plan["id"], "automation", 0),
        (lead_plan["id"], "automation", -1),
        (message_plan["id"], "automation", -2),
    ]
    assert job_queue.RESOURCE_LIMITS["automation"] == 1


def test_traffic_run_uses_snapshot_and_hides_internal_plan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    prepare_project(tmp_path)
    from app import database
    from app.schemas import AutomationPlanPatch, TrafficAutomationPlanConfig
    from app.services import automation_workbench, job_queue, traffic_workbench

    monkeypatch.setattr(job_queue, "enqueue_automation_run", lambda run_id: {"id": f"automation-job-{run_id}"})
    monkeypatch.setattr(job_queue, "enqueue_traffic_run", lambda run_id: {"id": f"traffic-job-{run_id}"})
    plan = automation_workbench.create_plan(
        traffic_plan_payload(
            config={
                "platform": "dy",
                "source_mode": "search_keyword",
                "source_value": "旧关键词",
                "action_like": True,
                "round_video_limit": 3,
            }
        )
    )
    run = automation_workbench.create_run(plan["id"])
    automation_workbench.update_plan(
        plan["id"],
        AutomationPlanPatch(
            config={
                "platform": "dy",
                "source_mode": "search_keyword",
                "source_value": "新关键词",
                "action_like": False,
                "round_video_limit": 9,
            }
        ),
    )

    def complete_without_browser(_: str, __: str, *, running_stage: str = "") -> dict[str, object]:
        assert running_stage == "traffic_running"
        with database.connect() as conn:
            conn.execute(
                """
                UPDATE traffic_runs
                SET status = 'completed', total_videos = 3, browsed_count = 3,
                    action_success_count = 2, skipped_count = 1, failed_count = 0,
                    finished_at = datetime('now', 'localtime')
                WHERE id = ?
                """,
                (f"automation:{run['id']}",),
            )
        return {}

    monkeypatch.setattr(automation_workbench, "_wait_existing_child", complete_without_browser)
    snapshot = TrafficAutomationPlanConfig.model_validate(run["config_snapshot"])
    automation_workbench._run_traffic_plan(run["id"], snapshot)

    internal_id = f"automation:{run['id']}"
    internal_plan = traffic_workbench.get_plan(internal_id)
    detail = automation_workbench.get_run(run["id"])
    assert internal_plan is not None
    assert internal_plan["automation_managed"] is True
    assert internal_plan["source_value"] == "旧关键词"
    assert internal_plan["action_like"] is True
    assert internal_plan["round_video_limit"] == 3
    assert traffic_workbench.list_plans() == []
    assert traffic_workbench.list_plans(include_archived=True) == []
    assert detail["status"] == "completed"
    assert detail["traffic_run_id"] == internal_id
    assert detail["traffic_run"]["browsed_count"] == 3
    assert detail["traffic_run"]["action_success_count"] == 2
    assert traffic_workbench.create_automation_run(run["id"], plan["name"], snapshot)["id"] == internal_id
    with database.connect() as conn:
        assert conn.execute("SELECT COUNT(*) AS c FROM traffic_plans WHERE id = ?", (internal_id,)).fetchone()["c"] == 1
        assert conn.execute("SELECT COUNT(*) AS c FROM traffic_runs WHERE id = ?", (internal_id,)).fetchone()["c"] == 1


def test_cancelling_traffic_automation_stops_child_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    prepare_project(tmp_path)
    from app import database
    from app.schemas import TrafficAutomationPlanConfig
    from app.services import automation_workbench, job_queue, traffic_workbench

    monkeypatch.setattr(job_queue, "enqueue_automation_run", lambda run_id: {"id": f"automation-job-{run_id}"})
    plan = automation_workbench.create_plan(traffic_plan_payload())
    run = automation_workbench.create_run(plan["id"])
    traffic_run = traffic_workbench.create_automation_run(
        run["id"],
        plan["name"],
        TrafficAutomationPlanConfig.model_validate(run["config_snapshot"]),
    )
    child_job = job_queue.enqueue_traffic_run(traffic_run["id"])
    with database.connect() as conn:
        conn.execute(
            """
            UPDATE automation_runs
            SET status = 'running', traffic_run_id = ?, traffic_runtime_job_id = ?
            WHERE id = ?
            """,
            (traffic_run["id"], child_job["id"], run["id"]),
        )

    cancelled = automation_workbench.cancel_run(run["id"])

    assert cancelled["stop_requested"] is True
    assert job_queue.get_job(child_job["id"])["status"] == "cancelled"
    assert traffic_workbench.get_run(traffic_run["id"])["status"] == "stopped"


def test_automation_routes_split_lead_and_traffic_authorization(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    prepare_project(tmp_path)
    from fastapi.testclient import TestClient

    from app.main import app
    from app.services import job_queue, license_service

    calls: list[str] = []
    monkeypatch.setattr(license_service, "ensure_authorized", lambda: calls.append("lead") or {"authorized": True})
    monkeypatch.setattr(license_service, "ensure_authorized_for", lambda scope: calls.append(scope) or {"authorized": True})
    monkeypatch.setattr(job_queue, "enqueue_automation_run", lambda run_id: {"id": f"runtime-{run_id}"})
    client = TestClient(app)

    traffic_response = client.post("/api/automation/plans", json=traffic_plan_payload().model_dump(mode="json"))
    assert traffic_response.status_code == 200
    traffic_run_response = client.post(f"/api/automation/plans/{traffic_response.json()['id']}/run")
    assert traffic_run_response.status_code == 200
    lead_response = client.post("/api/automation/plans", json=keyword_plan_payload().model_dump(mode="json"))
    assert lead_response.status_code == 200
    assert calls == ["traffic", "traffic", "lead"]

    monkeypatch.setattr(license_service, "ensure_authorized_for", lambda scope: (_ for _ in ()).throw(ValueError(f"{scope} 未授权")))
    rejected = client.post("/api/automation/plans", json=traffic_plan_payload(name="未授权引流").model_dump(mode="json"))
    assert rejected.status_code == 403
    assert "traffic 未授权" in rejected.json()["detail"]


def test_migration_10_preserves_existing_automation_data(tmp_path: Path) -> None:
    from app import database, migrations

    db_path = tmp_path / "migration-v9.sqlite3"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        for migration in migrations.MIGRATIONS[:9]:
            migration.action(conn, database.SCHEMA_SQL)
        conn.execute(
            """
            INSERT INTO automation_plans(id, name, plan_type, weekdays, run_time, config, sort_order)
            VALUES('old-plan', '旧自动化计划', 'keyword_lead', '[1]', '09:00', '{}', 4)
            """
        )
        conn.execute(
            """
            INSERT INTO automation_runs(id, plan_id, plan_name, plan_type, trigger_type, config_snapshot, status)
            VALUES('old-run', 'old-plan', '旧自动化计划', 'keyword_lead', 'manual', '{}', 'completed')
            """
        )
        conn.execute("INSERT INTO automation_run_items(run_id, keyword, status) VALUES('old-run', '旧关键词', 'succeeded')")
        conn.execute("INSERT INTO traffic_plans(id, name) VALUES('normal-traffic', '普通引流计划')")
        conn.commit()

        migrations.MIGRATIONS[9].action(conn, database.SCHEMA_SQL)

        preserved_plan = conn.execute("SELECT name, sort_order FROM automation_plans WHERE id = 'old-plan'").fetchone()
        assert (preserved_plan["name"], preserved_plan["sort_order"]) == ("旧自动化计划", 4)
        assert conn.execute("SELECT status FROM automation_runs WHERE id = 'old-run'").fetchone()["status"] == "completed"
        assert conn.execute("SELECT keyword FROM automation_run_items WHERE run_id = 'old-run'").fetchone()["keyword"] == "旧关键词"
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(automation_runs)")}
        assert {"traffic_run_id", "traffic_runtime_job_id"}.issubset(columns)
        assert conn.execute("SELECT automation_managed FROM traffic_plans WHERE id = 'normal-traffic'").fetchone()[0] == 0
        conn.execute(
            """
            INSERT INTO automation_plans(id, name, plan_type, weekdays, run_time, config, sort_order)
            VALUES('traffic-plan', '自动引流', 'traffic', '[1]', '10:00', '{}', 5)
            """
        )
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        conn.close()


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
