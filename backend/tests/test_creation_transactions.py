from __future__ import annotations

import json
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app import database
from app.schemas import AutomationPlanCreate, KeywordLeadPlanConfig
from app.services import account_actions, account_center, automation_workbench, job_queue, license_service


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    # 仅使用临时库；不启动 lifespan、队列线程或任何真实平台动作。
    monkeypatch.setenv("AI_CUSTOMER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AI_CUSTOMER_DB", str(tmp_path / "creation.sqlite3"))
    database.init_db()
    with database.connect() as conn:
        database.set_setting(conn, "message_daily_limit", "100")
        database.set_setting(conn, "message_hourly_limit", "40")
        database.set_setting(conn, "auto_dm_fill_only", "false")
    monkeypatch.setattr(license_service, "ensure_authorized", lambda: {"authorized": True})
    monkeypatch.setattr(license_service, "ensure_authorized_for", lambda *_: {"authorized": True})
    from app.main import app

    return TestClient(app, raise_server_exceptions=False)


def seed_customers() -> list[int]:
    with database.connect() as conn:
        ids = []
        for index in range(2):
            account_id = conn.execute(
                "INSERT INTO user_accounts(platform,platform_user_id,nickname,profile_url) VALUES('dy',?,?,?)",
                (f"customer-{index}", f"客户{index}", f"https://example.test/customer-{index}"),
            ).lastrowid
            ids.append(int(conn.execute(
                "INSERT INTO lead_user_accounts(account_id,screening_status,follow_status,script) VALUES(?,'目标客户','未私信','已生成话术')",
                (account_id,),
            ).lastrowid))
        return ids


def create_plan(plan_type="keyword_lead") -> dict:
    if plan_type == "message":
        account = account_center.create_account("dy", "客服账号", "service", ["message"], ["message"])
        account_center.set_account_state(account["id"], "ready", checked=True)
        account_center.set_feature_status(account["id"], "message", "ready")
        config = {"account_id": account["id"], "count": 2}
    else:
        config = {"keywords": ["关键词A", "关键词B"], "keyword_count": 2}
    return automation_workbench.create_plan(AutomationPlanCreate.model_validate({
        "name": "事务测试计划", "plan_type": plan_type, "weekdays": [1], "run_time": "09:00", "config": config,
    }))


@pytest.mark.parametrize("kind", ["ai_single", "ai_batch", "automation", "automation_message"])
def test_runtime_insert_then_failure_rolls_back_the_complete_creation(workspace, monkeypatch, kind):
    ids = seed_customers()
    plan = create_plan("message" if kind == "automation_message" else "keyword_lead") if kind.startswith("automation") else None
    parent = automation_workbench.create_run(plan["id"]) if kind == "automation_message" else None
    with database.connect() as conn:
        before_plan = dict(conn.execute("SELECT * FROM automation_plans WHERE id=?", (plan["id"],)).fetchone()) if plan else None
        runtime_count = conn.execute("SELECT COUNT(*) FROM runtime_jobs").fetchone()[0]
    inserted = []
    enqueue = job_queue.enqueue

    def insert_then_fail(runtime_kind, **kwargs):
        runtime = enqueue(runtime_kind, **kwargs)
        conn = kwargs["conn"]
        assert conn.in_transaction
        assert conn.execute("SELECT id FROM runtime_jobs WHERE id=?", (runtime["id"],)).fetchone()
        # 异常发生在真实运行记录写入后，不能靠空 mock 证明回滚。
        inserted.append(runtime["id"])
        raise RuntimeError("runtime INSERT 后模拟异常")

    monkeypatch.setattr(job_queue, "enqueue", insert_then_fail)
    if kind.startswith("ai"):
        url = "/api/ai/jobs" if kind == "ai_single" else "/api/ai/jobs/batch"
        payload = {"target_type": "lead", "target_id": ids[0]} if kind == "ai_single" else {"target_type": "lead", "target_ids": ids}
        assert workspace.post(url, json=payload).status_code == 500
    elif parent:
        result = automation_workbench.run_automation_run(parent["id"])
        assert result["status"] == "failed"
        assert result["message_batch_id"] == ""
        assert "runtime INSERT" in result["error"]
    else:
        with pytest.raises(RuntimeError, match="runtime INSERT"):
            automation_workbench.create_run(plan["id"])
    assert len(inserted) == 1
    with database.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM runtime_jobs").fetchone()[0] == runtime_count
        assert conn.execute("SELECT COUNT(*) FROM analysis_jobs").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM message_batches").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM message_batch_items").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM automation_runs").fetchone()[0] == int(parent is not None)
        assert conn.execute("SELECT COUNT(*) FROM automation_run_items").fetchone()[0] == 0
        if plan:
            assert dict(conn.execute("SELECT * FROM automation_plans WHERE id=?", (plan["id"],)).fetchone()) == before_plan


@pytest.mark.parametrize("batch", [False, True])
def test_ai_run_now_false_prepares_without_enqueueing(workspace, monkeypatch, batch):
    ids = seed_customers()
    monkeypatch.setattr(job_queue, "enqueue", lambda *_args, **_kwargs: pytest.fail("纯准备不应入队"))
    url = "/api/ai/jobs/batch" if batch else "/api/ai/jobs"
    payload = {"target_type": "lead", "run_now": False, **({"target_ids": ids} if batch else {"target_id": ids[0]})}
    response = workspace.post(url, json=payload)
    assert response.status_code == 200
    jobs = response.json() if batch else [response.json()]
    assert len(jobs) == (2 if batch else 1)
    assert all(job["status"] == "pending" and "runtime_job" not in job for job in jobs)
    with database.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM runtime_jobs").fetchone()[0] == 0


def test_automation_creation_keeps_priority_references_and_skipped_duplicate_rules(workspace):
    plan = create_plan()
    with database.connect() as conn:
        conn.execute("UPDATE automation_plans SET sort_order=17 WHERE id=?", (plan["id"],))
    scheduled_at = datetime(2026, 10, 6, 9)
    run = automation_workbench.create_run(plan["id"], "scheduled", scheduled_at)
    duplicate = automation_workbench.create_run(plan["id"], "scheduled", scheduled_at)
    skipped = automation_workbench.create_run(plan["id"])
    assert duplicate["id"] == run["id"]
    assert skipped["status"] == "skipped"
    assert skipped["runtime_job_id"] == ""
    assert run["total_count"] == 2
    assert [item["keyword"] for item in run["items"]] == ["关键词A", "关键词B"]
    with database.connect() as conn:
        runtime = conn.execute("SELECT * FROM runtime_jobs WHERE id=?", (run["runtime_job_id"],)).fetchone()
        assert runtime["entity_id"] == run["id"] and runtime["priority"] == -17
        assert conn.execute("SELECT COUNT(*) FROM runtime_jobs").fetchone()[0] == 1
        assert conn.execute("SELECT last_triggered_at FROM automation_plans WHERE id=?", (plan["id"],)).fetchone()[0]


@pytest.mark.parametrize("stage", ["analysis", "customers"])
def test_keyword_child_enqueue_failure_rolls_back_task_and_context_without_blocking_next_prepare(workspace, monkeypatch, stage):
    account = account_center.create_account("dy", "拓客测试账号", "operations", ["acquisition"], ["acquisition"])
    account_center.set_account_state(account["id"], "ready", checked=True)
    account_center.set_all_feature_status(account["id"], "ready")
    plan = automation_workbench.create_plan(AutomationPlanCreate.model_validate({
        "name": "关键词子任务事务测试", "plan_type": "keyword_lead", "weekdays": [1], "run_time": "09:00",
        "config": {"keywords": ["关键词A"], "keyword_count": 1, "acquisition_account_id": account["id"], "auto_analyze_leads": False},
    }))
    run = automation_workbench.create_run(plan["id"])
    config = KeywordLeadPlanConfig.model_validate(run["config_snapshot"])
    discovery = automation_workbench._create_discovery_task("关键词A", config)
    discovery_runtime = job_queue.enqueue_crawl_task(discovery["id"])
    context = {"discovery_task_id": discovery["id"], "discovery_runtime_job_id": discovery_runtime["id"]}
    with database.connect() as conn:
        source_id = conn.execute(
            "INSERT INTO user_accounts(platform,platform_user_id,nickname,competitor_status) VALUES('dy','source','竞品来源',?)",
            ("未分析" if stage == "analysis" else "竞品",),
        ).lastrowid
        conn.execute("INSERT INTO account_sources(account_id,keyword,task_id) VALUES(?,'关键词A',?)", (source_id, discovery["id"]))
        conn.execute("UPDATE crawl_jobs SET status='succeeded' WHERE id=?", (discovery["id"],))
        conn.execute("UPDATE runtime_jobs SET status='succeeded' WHERE id=?", (discovery_runtime["id"],))
        conn.execute("UPDATE automation_run_items SET context=? WHERE run_id=?", (json.dumps(context), run["id"]))
        next_number = database.get_setting(conn, "next_task_number", "1")
    enqueue = job_queue.enqueue
    inserted = []

    def insert_then_fail(kind, **kwargs):
        runtime = enqueue(kind, **kwargs)
        conn = kwargs["conn"]
        assert kind == ("account_analysis" if stage == "analysis" else "crawl_batch")
        assert conn.execute("SELECT COUNT(*) FROM crawl_jobs WHERE status='pending'").fetchone()[0] > 0
        assert conn.execute("SELECT id FROM runtime_jobs WHERE id=?", (runtime["id"],)).fetchone()
        inserted.append(runtime["id"])
        raise RuntimeError("关键词子任务 runtime INSERT 后失败")

    # 仅略过已完成的前置采集；真实子任务创建和队列 INSERT 保持执行。
    def wait_completed_discovery(_run_id, job_id):
        assert job_id == discovery_runtime["id"]
        return {}

    monkeypatch.setattr(job_queue, "enqueue", insert_then_fail)
    monkeypatch.setattr(automation_workbench, "_wait_existing_child", wait_completed_discovery)
    result = automation_workbench.run_automation_run(run["id"])
    assert result["status"] == "failed"
    assert result["items"][0]["status"] == "failed"
    assert "runtime INSERT" in result["items"][0]["error"]
    assert result["items"][0]["context"] == context
    assert len(inserted) == 1
    with database.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM crawl_jobs").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM crawl_jobs WHERE status='pending'").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM runtime_jobs").fetchone()[0] == 2
        assert database.get_setting(conn, "next_task_number", "1") == next_number
        assert not account_actions._active_account_analysis_identifiers(conn, "dy")
        assert not account_actions._active_find_customer_identifiers(conn, "dy")
    monkeypatch.setattr(job_queue, "enqueue", enqueue)
    prepared = account_actions.create_task_account_analysis_task(discovery["id"]) if stage == "analysis" else account_actions.create_keyword_find_customer_task("dy", "关键词A")
    assert prepared["task_ids"]
    assert prepared["account_count"] == 1
