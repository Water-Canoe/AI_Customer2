from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.publish_engine import service
from app.publish_engine.browser import launch_publish_context
from app.publish_engine.uploader.base_video import BaseVideoUploader


def _create_publish_account(platform: str, name: str, *, is_default: bool = False) -> dict[str, object]:
    from app.services import account_center, content_publish

    account = account_center.create_account(
        platform,
        name,
        "brand",
        ["publish"],
        ["publish"] if is_default else [],
    )
    content_publish.set_account_state(str(account["id"]), "ready", checked=True)
    account_center.set_feature_status(str(account["id"]), "publish", "ready")
    return account_center.get_account(str(account["id"]))


def test_publish_engine_uses_cloakbrowser(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import cloakbrowser

    context = MagicMock()
    context.add_init_script = AsyncMock()
    launcher = AsyncMock(return_value=context)
    monkeypatch.setattr(cloakbrowser, "launch_persistent_context_async", launcher)
    profile_dir = tmp_path / "profile"

    result = asyncio.run(launch_publish_context(headless=False, account_file=profile_dir))

    assert result is context
    launcher.assert_awaited_once_with(
        str(profile_dir),
        headless=False,
        viewport=None,
        locale="zh-CN",
        args=["--start-maximized"],
    )


def test_empty_profile_check_does_not_start_browser(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    profile_dir = tmp_path / "empty-profile"
    profile_dir.mkdir()
    monkeypatch.setattr(service, "_account_handlers", lambda _: pytest.fail("空 Profile 不应启动浏览器"))

    assert asyncio.run(service.check_account("dy", profile_dir)) is False


def test_creator_login_can_be_cancelled_while_waiting_for_scan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cancelled = {"checks": 0, "closed": False}

    async def fake_setup(*_args, **_kwargs):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled["closed"] = True

    def cancel_check() -> bool:
        cancelled["checks"] += 1
        return cancelled["checks"] > 1

    monkeypatch.setattr(service, "_account_handlers", lambda _: (fake_setup, None))

    with pytest.raises(service.PublishCancelled):
        asyncio.run(service.login_account("dy", tmp_path / "creator-profile", cancel_check=cancel_check))

    assert cancelled["closed"] is True


class _FakeUploader:
    def __init__(self, *, fail_after_submit: bool = False) -> None:
        self.submitted = False
        self.before_submit = None
        self.fail_after_submit = fail_after_submit

    async def main(self) -> None:
        await BaseVideoUploader.begin_submit(self)
        if self.fail_after_submit:
            raise RuntimeError("结果未确认")


def test_douyin_login_waiter_remains_active_before_scan(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.publish_engine.uploader.douyin_uploader import main

    page = MagicMock(url="https://creator.douyin.com/")
    page.get_by_text.return_value.locator.return_value.first.count = AsyncMock(return_value=0)
    monkeypatch.setattr(main, "_is_douyin_login_completed", AsyncMock(return_value=False))
    monkeypatch.setattr(main.asyncio, "sleep", AsyncMock())
    result = asyncio.run(main._wait_for_douyin_login(page, "account.json", {}, max_checks=1))
    assert result["status"] == "timeout"


def test_publish_marks_uncertain_result_for_manual_review(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    account_file = tmp_path / "profile"
    account_file.mkdir()
    monkeypatch.setattr(service, "_build_uploader", lambda **_: _FakeUploader(fail_after_submit=True))

    with pytest.raises(service.PublishReviewRequired):
        asyncio.run(
            service.publish(
                platform="dy",
                content_type="video",
                account_file=account_file,
                title="测试",
                description="测试内容",
                tags=[],
                media_paths=[tmp_path / "video.mp4"],
            )
        )


def test_publish_accounts_and_tasks_do_not_expose_cookie_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_CUSTOMER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AI_CUSTOMER_DB", str(tmp_path / "ai_customer.sqlite3"))
    from app import database
    from app.services import content_publish

    database.init_db()
    account = _create_publish_account("dy", "主账号", is_default=True)
    assert "auth_relative_path" not in account
    with database.connect() as conn:
        conn.execute(
            "INSERT INTO video_jobs(id, subject, script, status, outputs) VALUES('job-publish', '测试主题', '测试正文', 'succeeded', ?)",
            ('[{"name":"final.mp4","relative_path":"tasks/job-publish/attempt-1/final.mp4"}]',),
        )

    tasks = content_publish.create_video_output_tasks(video_job_id="job-publish", output_name="final.mp4")
    assert len(tasks) == 1
    assert tasks[0]["platform"] == "dy"
    assert tasks[0]["title"] == "测试主题"


def test_publish_asset_order_and_delete_guard(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_CUSTOMER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AI_CUSTOMER_DB", str(tmp_path / "ai_customer.sqlite3"))
    from io import BytesIO
    from app import database
    from app.services import content_assets, content_publish

    database.init_db()
    monkeypatch.setattr(content_assets, "_read_metadata", lambda *_: {"width": 1, "height": 1, "duration": None, "thumbnail_path": ""})
    first = content_assets.import_asset_file("1.png", BytesIO(b"1"), "image/png")
    second = content_assets.import_asset_file("2.png", BytesIO(b"2"), "image/png")
    account = _create_publish_account("xhs", "图文账号")
    tasks = content_publish.create_asset_tasks(
        asset_ids=[second["id"], first["id"]],
        account_ids=[account["id"]],
        title="图文测试",
    )

    assert [asset["id"] for asset in tasks[0]["assets"]] == [second["id"], first["id"]]
    with pytest.raises(RuntimeError, match="发布任务"):
        content_assets.delete_asset(first["id"])


def test_publish_tasks_use_serial_browser_queue_and_old_http_route_is_removed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_CUSTOMER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AI_CUSTOMER_DB", str(tmp_path / "ai_customer.sqlite3"))
    from app import database
    from app.main import app
    from app.services import content_publish

    database.init_db()
    account = _create_publish_account("ks", "默认快手", is_default=True)
    with database.connect() as conn:
        conn.execute(
            "INSERT INTO video_jobs(id, subject, script, status, outputs) VALUES('queue-video', '队列测试', '', 'succeeded', ?)",
            ('[{"name":"final.mp4","relative_path":"tasks/queue-video/attempt-1/final.mp4"}]',),
        )
    tasks = content_publish.create_video_output_tasks(video_job_id="queue-video", output_name="final.mp4")
    queued = content_publish.enqueue_tasks(tasks)

    with database.connect() as conn:
        runtime = conn.execute("SELECT kind, resource, priority, max_attempts FROM runtime_jobs WHERE id = ?", (queued[0]["runtime_job_id"],)).fetchone()
    assert dict(runtime) == {"kind": "content_publish", "resource": "browser", "priority": 50, "max_attempts": 100}
    # OpenAPI includes all nested routers and is the stable public route inventory.
    assert "/api/content/video-jobs/{video_job_id}/publish" not in app.openapi()["paths"]


def test_auto_publish_waits_for_generated_output_then_queues(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_CUSTOMER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AI_CUSTOMER_DB", str(tmp_path / "ai_customer.sqlite3"))
    from app import database
    from app.services import content_publish

    database.init_db()
    account = _create_publish_account("xhs", "自动发布账号")
    with database.connect() as conn:
        conn.execute("INSERT INTO video_jobs(id, subject, script, status) VALUES('auto-video', '自动发布', '正文', 'queued')")
    waiting = content_publish.create_waiting_video_tasks("auto-video", [account["id"]], 2, "all", "immediate", "")
    assert [task["status"] for task in waiting] == ["waiting_media", "waiting_media"]

    bound = content_publish.bind_waiting_video_tasks(
        "auto-video",
        [{"name": "one.mp4"}, {"name": "two.mp4"}],
    )
    assert [task["output_name"] for task in bound] == ["one.mp4", "two.mp4"]
