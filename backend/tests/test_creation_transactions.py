from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
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


@pytest.mark.parametrize("kind", ["ai_single", "ai_batch", "ai_all", "automation", "automation_message"])
def test_runtime_insert_then_failure_rolls_back_the_complete_creation(workspace, monkeypatch, kind):
    ids = seed_customers()
    if kind == "ai_all":
        with database.connect() as conn:
            conn.execute("UPDATE lead_user_accounts SET screening_status='待筛选', follow_status='待筛选', script=''")
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
        if kind == "ai_all":
            payload = {"target_type": "lead"}
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


@pytest.mark.parametrize("target_type", ["competitor", "lead"])
def test_one_click_previews_and_enqueues_all_unanalysed_targets_once(workspace, target_type):
    # 超过一页的真实记录混合已分析、排队、执行、失败和不可见对象；预览不得写数据。
    with database.connect() as conn:
        conn.executemany(
            "INSERT INTO user_accounts(id,platform,platform_user_id,nickname,account_role) VALUES(?,'dy',?,?,'competitor_candidate')",
            [(index, str(index), f"对象{index}") for index in range(1, 25)],
        )
        conn.execute("INSERT INTO lead_user_accounts(id,account_id) SELECT id,id FROM user_accounts")
        conn.execute("UPDATE user_accounts SET competitor_status='竞品' WHERE id IN (1,7)")
        conn.execute("UPDATE user_accounts SET competitor_status='非竞品' WHERE id IN (2,8)")
        conn.execute("UPDATE user_accounts SET account_role='user' WHERE id=6")
        conn.execute("UPDATE lead_user_accounts SET screening_status='目标客户' WHERE id=1")
        conn.execute("UPDATE lead_user_accounts SET screening_status='非客户' WHERE id=2")
        conn.execute("UPDATE lead_user_accounts SET hidden=1 WHERE id=6")
        conn.execute("UPDATE lead_user_accounts SET reason='已有分析原因' WHERE id=7")
        conn.execute("UPDATE lead_user_accounts SET script='已有分析话术' WHERE id=8")
        conn.executemany(
            "INSERT INTO analysis_jobs(id,target_type,target_id,status) VALUES(?,?,?,?)",
            [(f"existing-{index}", target_type, index, status) for index, status in ((3, "pending"), (4, "running"), (5, "failed"))],
        )
    preview = workspace.post("/api/bulk-actions/preview", json={
        "action": "ai_analyze", "target_type": target_type, "filters": {"unanalysed_only": True},
    })
    assert preview.status_code == 200 and preview.json()["eligible_count"] == 16
    assert "不受列表筛选和分页限制" in preview.json()["confirm_text"]
    with database.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM analysis_jobs").fetchone()[0] == 3
        assert conn.execute("SELECT COUNT(*) FROM runtime_jobs").fetchone()[0] == 0
    # 两次并发点击共用真实 SQLite 事务，只有一次能选中未分析对象。
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: workspace.post("/api/ai/jobs/batch", json={"target_type": target_type}), range(2)))
    assert all(response.status_code == 200 for response in responses)
    assert sorted(len(response.json()) for response in responses) == [0, 16]
    jobs = [job for response in responses for job in response.json()]
    assert {job["target_id"] for job in jobs} == set(range(9, 25))
    assert all(job["status"] == "pending" and job["runtime_job"]["kind"] == "ai_batch" for job in jobs)
    with database.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM analysis_jobs").fetchone()[0] == 19
        runtimes = conn.execute("SELECT payload FROM runtime_jobs").fetchall()
        assert len(runtimes) == 1
        assert set(json.loads(runtimes[0]["payload"])["job_ids"]) == {job["id"] for job in jobs}
    assert workspace.post("/api/ai/jobs/batch", json={"target_type": target_type, "target_ids": []}).json() == []


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
