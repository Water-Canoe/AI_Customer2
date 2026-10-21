"""账号与总览入口必须原子创建业务记录及运行队列。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import database
from app.routers import lead_accounts, overview
from app.services import account_center, bulk_actions, crawler_adapter, job_queue


@pytest.fixture
def account_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    monkeypatch.setenv("AI_CUSTOMER_DB", str(tmp_path / "project.sqlite3"))
    monkeypatch.setenv("AI_CUSTOMER_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(lead_accounts, "require_license", lambda: None)
    monkeypatch.setattr(overview, "require_license", lambda: None)
    database.init_db()
    execution_account = account_center.create_account("dy", "本地测试账号", "operations", ["acquisition"], ["acquisition"])
    account_center.set_account_state(str(execution_account["id"]), "ready", checked=True)
    account_center.set_all_feature_status(str(execution_account["id"]), "ready")
    account_ids: list[int] = []
    lead_ids: list[int] = []
    with database.connect() as conn:
        database.set_setting(conn, "media_crawler_path", str(tmp_path))
        for index in range(2):
            account_id = int(conn.execute(
                "INSERT INTO user_accounts(platform, platform_user_id, nickname, competitor_status) VALUES('dy', ?, ?, '竞品')",
                (f"source-{index}", f"来源{index}"),
            ).lastrowid)
            account_ids.append(account_id)
            content_id = int(conn.execute(
                "INSERT INTO contents(platform, content_id, author_account_id, title, source_keyword) VALUES('dy', ?, ?, '测试内容', '测试关键词')",
                (f"content-{index}", account_id),
            ).lastrowid)
            customer_id = int(conn.execute(
                "INSERT INTO user_accounts(platform, platform_user_id, nickname, signature) VALUES('dy', ?, ?, '已有资料')",
                (f"customer-{index}", f"客户{index}"),
            ).lastrowid)
            lead_id = int(conn.execute("INSERT INTO lead_user_accounts(account_id) VALUES(?)", (customer_id,)).lastrowid)
            lead_ids.append(lead_id)
            conn.execute(
                "INSERT INTO lead_sources(lead_account_id, source_account_id, content_id, source_type) VALUES(?, ?, ?, 'comment')",
                (lead_id, account_ids[0], content_id),
            )
    return {"account_id": account_ids[0], "lead_id": lead_ids[0]}


CASES = ["profile", "profiles", "analysis", "find", "intent", "intents", "keyword_analysis", "keyword_find", "all_find"]


def create_from_route(case: str, project: dict[str, object], *, run_now: bool = True) -> dict[str, object]:
    account_id, lead_id = int(project["account_id"]), int(project["lead_id"])
    if case == "profile":
        return lead_accounts.create_profile_enrichment_task(account_id)
    if case == "profiles":
        return lead_accounts.create_profile_enrichment_batch(limit=10, run_now=run_now)
    if case == "analysis":
        return lead_accounts.create_account_analysis_task(account_id, run_now=run_now)
    if case == "find":
        return lead_accounts.create_account_find_customer_task(account_id, run_now=run_now)
    if case == "all_find":
        return lead_accounts.create_all_find_customer_tasks(run_now=run_now)
    if case == "intent":
        return overview.analyze_overview_customer_intent(lead_id, run_now=run_now)
    if case == "intents":
        return overview.analyze_overview_account_customers(account_id, run_now=run_now)
    if case == "keyword_analysis":
        with database.connect() as conn:
            conn.execute("UPDATE user_accounts SET competitor_status='未分析' WHERE id=?", (account_id,))
        return overview.analyze_keyword_accounts(platform="dy", keyword="测试关键词", run_now=run_now)
    return overview.find_keyword_customers(platform="dy", keyword="测试关键词", run_now=run_now)


def counts() -> tuple[int, int, int, str]:
    with database.connect() as conn:
        return (
            conn.execute("SELECT COUNT(*) FROM crawl_jobs").fetchone()[0],
            conn.execute("SELECT COUNT(*) FROM analysis_jobs").fetchone()[0],
            conn.execute("SELECT COUNT(*) FROM runtime_jobs").fetchone()[0],
            database.get_setting(conn, "next_task_number", "1"),
        )


@pytest.mark.parametrize("case", CASES)
def test_runtime_insert_failure_rolls_back_business_and_queue(account_project, monkeypatch, case: str) -> None:
    original_enqueue = job_queue.enqueue
    inserted: list[dict[str, object]] = []

    def insert_then_fail(*args, **kwargs):
        # 使用真实 INSERT 后才失败，不能只用入队前抛异常的假函数。
        inserted.append(original_enqueue(*args, **kwargs))
        conn = kwargs.get("conn")
        assert conn is not None
        assert conn.execute("SELECT COUNT(*) FROM runtime_jobs").fetchone()[0] == 1
        expected_table = "analysis_jobs" if case in {"intent", "intents"} else "crawl_jobs"
        minimum = 2 if case in {"profiles", "find", "intents", "keyword_find", "all_find"} else 1
        assert conn.execute(f"SELECT COUNT(*) FROM {expected_table}").fetchone()[0] >= minimum
        raise RuntimeError("测试：运行队列插入后失败")

    monkeypatch.setattr(job_queue, "enqueue", insert_then_fail)
    with pytest.raises(RuntimeError, match="运行队列插入后失败"):
        create_from_route(case, account_project)
    assert len(inserted) == 1
    assert counts() == (0, 0, 0, "1")
    with database.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM task_logs").fetchone()[0] == 0


@pytest.mark.parametrize("case", CASES)
def test_creation_commits_readable_business_and_runtime(account_project, case: str) -> None:
    result = create_from_route(case, account_project)
    crawl_count, analysis_count, runtime_count, _ = counts()
    assert runtime_count == 1
    assert crawl_count + analysis_count > 0
    assert result["runtime_job"]["status"] == "queued"
    if "task_ids" in result:
        assert all(crawler_adapter.get_task(task_id) for task_id in result["task_ids"])
    if case == "profiles":
        assert result["task_ids"] == ["0001", "0002"]


@pytest.mark.parametrize("case", CASES[1:])
def test_run_now_false_only_prepares_business(account_project, monkeypatch, case: str) -> None:
    def unexpected_enqueue(*args, **kwargs):
        raise AssertionError("纯准备不能创建运行队列")

    monkeypatch.setattr(job_queue, "enqueue", unexpected_enqueue)
    result = create_from_route(case, account_project, run_now=False)
    crawl_count, analysis_count, runtime_count, _ = counts()
    assert crawl_count + analysis_count > 0
    assert runtime_count == 0
    assert "runtime_job" not in result


@pytest.mark.parametrize("case", ["profiles", "find", "all_find"])
def test_later_crawl_insert_failure_rolls_back_earlier_batch_items(account_project, monkeypatch, case: str) -> None:
    original_create = crawler_adapter.create_task
    created_ids: list[str] = []

    def create_then_fail(*args, **kwargs):
        task = original_create(*args, **kwargs)
        created_ids.append(str(task["id"]))
        if len(created_ids) == 2:
            raise RuntimeError("测试：第二个采集任务插入后失败")
        return task

    monkeypatch.setattr(crawler_adapter, "create_task", create_then_fail)
    with pytest.raises(RuntimeError, match="第二个采集任务插入后失败"):
        create_from_route(case, account_project)
    assert created_ids == ["0001", "0002"]
    assert counts() == (0, 0, 0, "1")


def test_all_competitors_include_multiple_platforms_more_than_100_and_skip_active_tasks(account_project) -> None:
    # 不要求账号具有关键词来源，也不能沿用关键词接口的 100 个账号上限。
    execution = account_center.create_account("xhs", "小红书采集", "operations", ["acquisition"], ["acquisition"])
    account_center.set_account_state(execution["id"], "ready", checked=True)
    account_center.set_all_feature_status(execution["id"], "ready")
    with database.connect() as conn:
        for index in range(103):
            conn.execute("INSERT INTO user_accounts(platform,platform_user_id,nickname,competitor_status) VALUES('dy',?,?,'竞品')", (f"extra-{index}", f"额外竞品{index}"))
        conn.execute("INSERT INTO user_accounts(platform,platform_user_id,nickname,competitor_status) VALUES('xhs','xhs-extra','小红书竞品','竞品')")
        conn.execute("INSERT INTO user_accounts(platform,platform_user_id,nickname,competitor_status) VALUES('dy','excluded','非竞品','非竞品')")
        expected_ids = {row[0] for row in conn.execute("SELECT id FROM user_accounts WHERE competitor_status='竞品'")}
    preview = bulk_actions.preview_bulk_action({"action": "find_customers", "target_type": "competitor", "filters": {"keyword": "不匹配", "page": 999}})
    assert preview["eligible_count"] == len(expected_ids) == 106
    assert counts()[:3] == (0, 0, 0)
    result = lead_accounts.create_all_find_customer_tasks()
    assert {account["account_id"] for account in result["accounts"]} == expected_ids
    assert result["account_count"] == 106
    tasks = [crawler_adapter.get_task(task_id) for task_id in result["task_ids"]]
    assert {task["platform"] for task in tasks} == {"dy", "xhs"}
    assert all(task["collect_comments"] and task["collect_sub_comments"] for task in tasks)
    assert result["runtime_job"]["payload"]["task_ids"] == result["task_ids"]
    repeated = lead_accounts.create_all_find_customer_tasks()
    assert repeated["created"] == 0 and len(repeated["skipped"]) == 106
    assert "runtime_job" not in repeated


def test_runtime_failure_preserves_existing_stale_job_and_rolls_back_new_jobs(account_project, monkeypatch) -> None:
    lead_id = int(account_project["lead_id"])
    with database.connect() as conn:
        conn.execute(
            "INSERT INTO analysis_jobs(id,target_type,target_id,status,updated_at) VALUES('stale','lead',?,'running',datetime('now','localtime','-2 hours'))",
            (lead_id,),
        )
    original_enqueue = job_queue.enqueue

    def insert_then_fail(*args, **kwargs):
        original_enqueue(*args, **kwargs)
        conn = kwargs["conn"]
        # 本次事务已恢复旧任务并创建另一个客户的分析，再验证两种写入一起回滚。
        assert conn.execute("SELECT status FROM analysis_jobs WHERE id='stale'").fetchone()[0] == "pending"
        assert conn.execute("SELECT COUNT(*) FROM analysis_jobs").fetchone()[0] == 2
        raise RuntimeError("测试：恢复任务入队失败")

    monkeypatch.setattr(job_queue, "enqueue", insert_then_fail)
    with pytest.raises(RuntimeError, match="恢复任务入队失败"):
        create_from_route("intents", account_project)
    assert counts() == (0, 1, 0, "1")
    with database.connect() as conn:
        assert conn.execute("SELECT status FROM analysis_jobs WHERE id='stale'").fetchone()[0] == "running"
