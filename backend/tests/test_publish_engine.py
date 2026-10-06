from __future__ import annotations

import asyncio
import importlib
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.publish_engine import service
from app.publish_engine.browser import launch_publish_context
from app.publish_engine.uploader.base_video import BaseVideoUploader


class _LoginContext:
    """模拟用户关窗事件，区分关窗前的主动关闭和关窗后的驱动清理。"""

    def __init__(self) -> None:
        self.closed = False
        self.listeners = []
        self.page = MagicMock(url="https://example.com/login")
        self.page.goto = AsyncMock()
        self.page.is_closed.side_effect = lambda: self.closed
        self.page.get_by_text.return_value.locator.return_value.first.count = AsyncMock(return_value=0)
        self.pages = [self.page]
        self.new_page = AsyncMock(return_value=self.page)
        self.close = AsyncMock(side_effect=self.manual_close)

    def on(self, event, callback) -> None:
        assert event == "close"
        self.listeners.append(callback)

    def manual_close(self) -> None:
        self.closed = True
        for callback in self.listeners:
            callback(self)


def _mock_account_login(
    kind: str,
    profile: Path,
    monkeypatch: pytest.MonkeyPatch,
    context: _LoginContext,
    checked: asyncio.Event,
    *,
    success: bool,
    already_logged_in: bool = False,
    cancel_check=None,
):
    """保留实际登录服务和会话收尾，仅模拟平台网页的登录检测。"""
    if kind == "user":
        from app.services import platform_login

        async def user_logged_in(*_args):
            checked.set()
            return success

        monkeypatch.setattr(platform_login, "launch_publish_context", AsyncMock(return_value=context))
        monkeypatch.setattr(platform_login, "_cookie_map", AsyncMock(return_value={}))
        monkeypatch.setattr(platform_login, "_open_login_page", AsyncMock())
        monkeypatch.setattr(platform_login, "_is_logged_in", user_logged_in)
        return platform_login.login_account("dy", profile, cancel_check=cancel_check, timeout_seconds=1)

    package, qr_function, detector = {
        "dy": ("douyin_uploader", "_save_douyin_qrcode", "_is_douyin_login_completed"),
        "ks": ("ks_uploader", "_save_ks_qrcode", "_is_ks_login_page_gone"),
        "xhs": ("xiaohongshu_uploader", "_save_xhs_qrcode", "_is_xhs_login_completed"),
    }[kind]
    module = importlib.import_module(f"app.publish_engine.uploader.{package}.main")
    monkeypatch.setattr(module, "launch_publish_context", AsyncMock(return_value=context))
    monkeypatch.setattr(module, "profile_has_state", lambda _: True)
    monkeypatch.setattr(module, "cookie_auth", AsyncMock(side_effect=AssertionError("打开登录不应仅检查已有登录态")))
    qr_saved = False

    async def creator_logged_in(*_args):
        if already_logged_in or qr_saved:
            checked.set()
            return success
        return False

    async def save_qr(*_args, **_kwargs):
        nonlocal qr_saved
        assert not already_logged_in, "已登录页面应跳过二维码提取"
        qr_saved = True
        return {"image_path": str(profile.parent / "login-qrcode.png")}

    async def goto(url, **_kwargs):
        context.page.url = module.KUAISHOU_UPLOAD_URL if kind == "ks" and already_logged_in else url
        if kind == "ks" and already_logged_in:
            checked.set()

    context.page.goto.side_effect = goto
    monkeypatch.setattr(module, detector, creator_logged_in)
    monkeypatch.setattr(module, qr_function, save_qr)
    if kind == "ks":
        monkeypatch.setattr(module, "_is_ks_qrcode_expired", AsyncMock(return_value=False))
    return service.login_account(kind, profile, cancel_check=cancel_check)


@pytest.mark.parametrize("kind", ["user", "dy", "ks", "xhs"])
@pytest.mark.parametrize("already_logged_in", [False, True])
def test_account_login_keeps_window_until_manual_close(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str, already_logged_in: bool
) -> None:
    async def check() -> None:
        context = _LoginContext()
        detected = asyncio.Event()
        task = asyncio.create_task(_mock_account_login(
            kind, tmp_path / "profile", monkeypatch, context, detected,
            success=True, already_logged_in=already_logged_in,
        ))
        await asyncio.wait_for(detected.wait(), 1)
        # 已识别登录后任务继续占用会话，等待用户处理保存账号提示。
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(asyncio.shield(task), 0.02)
        context.close.assert_not_awaited()
        context.manual_close()
        assert (await asyncio.wait_for(task, 1))["success"] is True
        context.close.assert_awaited_once()

    asyncio.run(check())


@pytest.mark.parametrize("kind", ["user", "dy", "ks", "xhs"])
@pytest.mark.parametrize("ending", ["early_close", "cancel_before_login", "cancel_after_login"])
def test_account_login_early_close_or_cancel_releases_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str, ending: str
) -> None:
    async def check() -> None:
        context = _LoginContext()
        detected = asyncio.Event()
        cancelled = False
        task = asyncio.create_task(_mock_account_login(
            kind, tmp_path / "profile", monkeypatch, context, detected,
            success=ending == "cancel_after_login", cancel_check=lambda: cancelled,
        ))
        await asyncio.wait_for(detected.wait(), 1)
        await asyncio.sleep(0)
        if ending == "early_close":
            context.manual_close()
            message = "尚未确认登录成功"
        else:
            cancelled = True
            message = "取消"
        with pytest.raises(RuntimeError, match=message):
            await asyncio.wait_for(task, 1)
        context.close.assert_awaited_once()

    asyncio.run(check())


@pytest.mark.parametrize("platform", ["dy", "ks", "xhs"])
def test_creator_login_does_not_treat_closed_page_as_success(platform: str) -> None:
    from app.publish_engine.uploader.douyin_uploader.main import _is_douyin_login_completed
    from app.publish_engine.uploader.ks_uploader.main import _is_ks_login_page_gone
    from app.publish_engine.uploader.xiaohongshu_uploader.main import _is_xhs_login_completed

    page = MagicMock()
    page.is_closed.return_value = True
    checker = {"dy": _is_douyin_login_completed, "ks": _is_ks_login_page_gone, "xhs": _is_xhs_login_completed}[platform]
    assert asyncio.run(checker(page)) is False


@pytest.mark.parametrize("kind", ["user", "dy", "ks", "xhs"])
def test_account_login_immediate_cancel_does_not_leave_browser(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    async def check() -> None:
        context = _LoginContext()
        with pytest.raises(RuntimeError, match="取消"):
            await _mock_account_login(
                kind, tmp_path / "profile", monkeypatch, context, asyncio.Event(),
                success=False, cancel_check=lambda: True,
            )
        # 创作者在启动登录协程前取消；用户登录已创建上下文，必须清理。
        assert context.close.await_count == int(kind == "user")
        context.new_page.assert_not_awaited()

    asyncio.run(check())


def test_failed_login_result_closes_context() -> None:
    from app.publish_engine.browser import run_login_session

    context = _LoginContext()
    result = {"success": False, "message": "等待用户登录超时"}
    assert asyncio.run(run_login_session(context, AsyncMock(return_value=result)())) == result
    context.close.assert_awaited_once()


@pytest.mark.parametrize("package,setup_name", [
    ("douyin_uploader", "douyin_setup"),
    ("ks_uploader", "ks_setup"),
    ("xiaohongshu_uploader", "xiaohongshu_setup"),
])
def test_creator_automatic_check_keeps_existing_valid_profile_behavior(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, package: str, setup_name: str
) -> None:
    module = importlib.import_module(f"app.publish_engine.uploader.{package}.main")
    checker = AsyncMock(return_value=True)
    monkeypatch.setattr(module, "profile_has_state", lambda _: True)
    monkeypatch.setattr(module, "cookie_auth", checker)
    monkeypatch.setattr(module, "launch_publish_context", AsyncMock(side_effect=AssertionError("自动校验不应打开交互登录")))
    result = asyncio.run(getattr(module, setup_name)(str(tmp_path / "profile"), handle=False, return_detail=True))
    assert result["success"] is True and result["status"] == "cookie_valid"
    checker.assert_awaited_once()


@pytest.mark.parametrize("package,detector", [
    ("ks_uploader", "_is_ks_login_page_gone"),
    ("xiaohongshu_uploader", "_is_xhs_login_completed"),
])
def test_creator_login_query_error_does_not_mean_success(package: str, detector: str) -> None:
    module = importlib.import_module(f"app.publish_engine.uploader.{package}.main")
    page = MagicMock(url="https://example.com/login")
    page.is_closed.return_value = False
    page.locator.return_value.first.count = AsyncMock(return_value=1)
    page.locator.return_value.first.is_visible = AsyncMock(side_effect=RuntimeError("页面已关闭"))
    assert asyncio.run(getattr(module, detector)(page)) is False


def _create_publish_account(platform: str, name: str, *, is_default: bool = False) -> dict[str, object]:
    from app.services import account_center

    account = account_center.create_account(
        platform,
        name,
        "brand",
        ["publish"],
        ["publish"] if is_default else [],
    )
    account_center.set_account_state(str(account["id"]), "ready", checked=True)
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
            "INSERT INTO video_jobs(id, subject, script, status) VALUES('job-publish', '测试主题', '测试正文', 'succeeded')",
        )
        conn.execute("INSERT INTO video_outputs(id,video_job_id,attempt,output_index,name,relative_path,subject,script) VALUES('output-publish','job-publish',1,0,'final.mp4','tasks/job-publish/attempt-1/final.mp4','测试主题','测试正文')")

    tasks = content_publish.create_video_output_tasks(output_ids=['output-publish'])
    assert len(tasks) == 1
    assert tasks[0]["platform"] == "dy"
    assert tasks[0]["title"] == "测试主题"


def test_publish_task_pagination_keeps_global_summary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_CUSTOMER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AI_CUSTOMER_DB", str(tmp_path / "ai_customer.sqlite3"))
    from app import database
    from app.services import content_publish

    database.init_db()
    account = _create_publish_account("dy", "分页账号")
    with database.connect() as conn:
        for index, status in enumerate(("queued", "running", "succeeded", "failed")):
            conn.execute(
                """
                INSERT INTO publish_tasks(id, batch_id, account_id, source_type, content_type, title, status)
                VALUES(?, 'batch-page', ?, 'asset_video', 'video', ?, ?)
                """,
                (f"publish-page-{index}", account["id"], f"任务{index}", status),
            )

    result = content_publish.list_tasks(page=1, page_size=2)

    assert len(result["items"]) == 2
    assert result["total"] == 4
    assert result["summary"] == {"pending": 1, "running": 1, "succeeded": 1, "review": 1, "active": 2}


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
            "INSERT INTO video_jobs(id, subject, script, status) VALUES('queue-video', '队列测试', '', 'succeeded')",
        )
        conn.execute("INSERT INTO video_outputs(id,video_job_id,attempt,output_index,name,relative_path,subject) VALUES('queue-output','queue-video',1,0,'final.mp4','tasks/queue-video/attempt-1/final.mp4','队列测试')")
    queued = content_publish.create_video_output_tasks(output_ids=['queue-output'])

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
    with database.connect() as conn:
        for index, name in enumerate(['one.mp4', 'two.mp4']):
            conn.execute('INSERT INTO video_outputs(id,video_job_id,attempt,output_index,name,relative_path,subject) VALUES(?,?,?,?,?,?,?)', (f'auto-output-{index}', 'auto-video', 1, index, name, f'auto-video/{name}', '自动发布'))

    bound = content_publish.bind_waiting_video_tasks(
        "auto-video",
        [{"id": "auto-output-0", "name": "one.mp4"}, {"id": "auto-output-1", "name": "two.mp4"}],
    )
    assert [task["output_name"] for task in bound] == ["one.mp4", "two.mp4"]
    assert [task['output_id'] for task in bound] == ['auto-output-0', 'auto-output-1']
