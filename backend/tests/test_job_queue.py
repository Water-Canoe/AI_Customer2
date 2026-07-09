from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import pytest


BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))


def prepare_queue(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_CUSTOMER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AI_CUSTOMER_DB", str(tmp_path / "ai_customer.sqlite3"))
    from app import database

    database.init_db()


def wait_for_job(job_id: str, expected: set[str], timeout: float = 3.0) -> dict[str, object]:
    from app.services import job_queue

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = job_queue.get_job(job_id)
        if str(job["status"]) in expected:
            return job
        time.sleep(0.05)
    raise AssertionError(f"任务 {job_id} 未在限时内进入 {expected}")


def test_runtime_queue_executes_and_persists_result(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    prepare_queue(tmp_path, monkeypatch)
    from app.services import job_queue

    monkeypatch.setattr(job_queue, "_execute_job", lambda job: {"handled": job["payload"]["value"]})
    monkeypatch.setattr(job_queue, "_domain_outcome", lambda job, result=None: {"status": "succeeded", "error": ""})

    job = job_queue.enqueue("test_job", entity_id="entity-1", payload={"value": 7})
    job_queue.start()
    try:
        finished = wait_for_job(str(job["id"]), {"succeeded"})
    finally:
        job_queue.shutdown(1)

    assert finished["result"] == {"handled": 7}
    assert int(finished["attempt"]) == 1
    assert finished["heartbeat_at"]


def test_runtime_queue_cancels_queued_job(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    prepare_queue(tmp_path, monkeypatch)
    from app.services import job_queue

    job = job_queue.enqueue("test_job", entity_id="entity-2")
    cancelled = job_queue.request_cancel(str(job["id"]))

    assert cancelled["status"] == "cancelled"
    assert cancelled["cancel_requested"] is True
    assert job_queue.delete_job(str(job["id"])) == {"ok": True, "id": job["id"]}


def test_cancelling_queued_runtime_job_updates_domain_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    prepare_queue(tmp_path, monkeypatch)
    from app import database
    from app.services import job_queue

    with database.connect() as conn:
        conn.execute(
            "INSERT INTO crawl_jobs(id, name, mode, platform, login_type, crawler_type, status) VALUES('crawl-cancel', '取消测试', 'search', 'dy', 'qrcode', 'search', 'pending')"
        )
    job = job_queue.enqueue_crawl_task("crawl-cancel")

    job_queue.request_cancel(str(job["id"]), reason="测试取消")

    with database.connect() as conn:
        assert conn.execute("SELECT status FROM crawl_jobs WHERE id = 'crawl-cancel'").fetchone()[0] == "cancelled"


def test_runtime_recovery_releases_browser_jobs_and_resumes_ai(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    prepare_queue(tmp_path, monkeypatch)
    from app import database
    from app.services import job_queue

    with database.connect() as conn:
        conn.execute("INSERT INTO traffic_plans(id, name) VALUES('plan-1', '恢复测试')")
        conn.execute("INSERT INTO traffic_runs(id, plan_id, status) VALUES('run-1', 'plan-1', 'running')")
        conn.execute("INSERT INTO analysis_jobs(id, target_type, target_id, status) VALUES('ai-1', 'lead', 1, 'running')")
        conn.execute(
            "INSERT INTO runtime_jobs(id, kind, entity_id, resource, payload, status, attempt, max_attempts) VALUES('runtime-browser', 'traffic_run', 'run-1', 'browser', ?, 'running', 1, 1)",
            ('{"run_id":"run-1"}',),
        )
        conn.execute(
            "INSERT INTO runtime_jobs(id, kind, entity_id, resource, payload, status, attempt, max_attempts) VALUES('runtime-ai', 'ai_job', 'ai-1', 'ai', ?, 'running', 1, 2)",
            ('{"job_id":"ai-1"}',),
        )

    result = job_queue.recover_interrupted_jobs()

    assert result == {"recovered": 1, "resumed": 1}
    assert job_queue.get_job("runtime-browser")["status"] == "interrupted"
    assert job_queue.get_job("runtime-ai")["status"] == "queued"
    with database.connect() as conn:
        assert conn.execute("SELECT status FROM traffic_runs WHERE id = 'run-1'").fetchone()[0] == "stopped"
        assert conn.execute("SELECT status FROM analysis_jobs WHERE id = 'ai-1'").fetchone()[0] == "pending"


def test_browser_queue_waits_for_interactive_login(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    prepare_queue(tmp_path, monkeypatch)
    from app.services import job_queue, profile_manager

    class FakeProcess:
        pid = 101
        returncode = None

        def poll(self):
            return self.returncode

    fake = FakeProcess()
    monkeypatch.setattr(profile_manager.subprocess, "Popen", lambda *args, **kwargs: fake)
    monkeypatch.setattr(profile_manager.time, "sleep", lambda _: None)
    monkeypatch.setattr(profile_manager, "_terminate_process_tree", lambda process, timeout_seconds=5.0: setattr(process, "returncode", 0))
    monkeypatch.setattr(job_queue, "_execute_job", lambda job: {"ok": True})
    monkeypatch.setattr(job_queue, "_domain_outcome", lambda job, result=None: {"status": "succeeded", "error": ""})

    profile_manager.launch_interactive("dy", ["fake"], cwd=tmp_path, log_path=tmp_path / "login.log")
    job = job_queue.enqueue("test_browser", resource="browser")
    job_queue.start()
    try:
        time.sleep(0.15)
        assert job_queue.get_job(str(job["id"]))["status"] == "queued"
        profile_manager.close_interactive()
        finished = wait_for_job(str(job["id"]), {"succeeded"})
    finally:
        job_queue.shutdown(1)
        profile_manager.shutdown()

    assert finished["status"] == "succeeded"


def test_shutdown_requeues_safe_ai_job(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    prepare_queue(tmp_path, monkeypatch)
    from app import database
    from app.services import job_queue

    release = threading.Event()
    monkeypatch.setattr(job_queue, "_execute_job", lambda job: release.wait(2) or {"ok": True})
    monkeypatch.setattr(job_queue, "_domain_outcome", lambda job, result=None: {"status": "succeeded", "error": ""})
    with database.connect() as conn:
        conn.execute("INSERT INTO analysis_jobs(id, target_type, target_id, status) VALUES('ai-safe', 'lead', 1, 'running')")
    job = job_queue.enqueue("ai_job", entity_id="ai-safe", payload={"job_id": "ai-safe"}, resource="ai", max_attempts=2)
    job_queue.start()
    wait_for_job(str(job["id"]), {"running"})

    result = job_queue.shutdown(0.05)
    release.set()
    deadline = time.monotonic() + 2
    while any(job_queue.active_summary().values()) and time.monotonic() < deadline:
        time.sleep(0.01)

    assert result == {"requested": 1, "interrupted": 0, "resumed": 1}
    assert job_queue.get_job(str(job["id"]))["status"] == "queued"
    with database.connect() as conn:
        assert conn.execute("SELECT status FROM analysis_jobs WHERE id = 'ai-safe'").fetchone()[0] == "pending"


def test_account_analysis_releases_browser_before_ai_execution(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    prepare_queue(tmp_path, monkeypatch)
    from app.services import account_actions, job_queue

    captured: dict[str, object] = {}
    monkeypatch.setattr(
        account_actions,
        "prepare_account_analysis_jobs",
        lambda account_ids, task_id: {"task_id": task_id, "job_ids": ["ai-11", "ai-12"], "errors": [], "ready": True},
    )

    def fake_enqueue(job_ids: list[str], entity_id: str = "") -> dict[str, object]:
        captured["job_ids"] = job_ids
        captured["entity_id"] = entity_id
        return {"id": "runtime-ai"}

    monkeypatch.setattr(job_queue, "enqueue_ai_batch", fake_enqueue)
    result = job_queue._execute_job(
        {
            "kind": "account_analysis",
            "payload": {"account_ids": [11, 12], "task_id": "crawl-analysis"},
        }
    )

    assert captured == {"job_ids": ["ai-11", "ai-12"], "entity_id": "account-analysis:crawl-analysis"}
    assert result["ai_runtime_job"] == {"id": "runtime-ai"}
