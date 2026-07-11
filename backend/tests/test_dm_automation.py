from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path

import pytest


WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(WORKSPACE_ROOT))


def test_click_send_uses_ready_icon_without_fixed_delay() -> None:
    from tools.douyin_dm_automation import automation

    class Mouse:
        def __init__(self) -> None:
            self.clicks: list[tuple[float, float]] = []

        async def click(self, x: float, y: float) -> None:
            self.clicks.append((x, y))

    class Keyboard:
        async def press(self, key: str) -> None:
            raise AssertionError(f"不应退回键盘发送：{key}")

    class Page:
        def __init__(self) -> None:
            self.mouse = Mouse()
            self.keyboard = Keyboard()
            self.waits: list[int] = []

        async def evaluate(self, script: str, selector: str):
            return {"x": 12.5, "y": 18.5}

        async def wait_for_timeout(self, value: int) -> None:
            self.waits.append(value)

    page = Page()
    asyncio.run(automation.click_send(page))

    assert page.mouse.clicks == [(12.5, 18.5)]
    assert page.waits == []


def test_private_message_button_clicks_only_exact_dm_target(monkeypatch: pytest.MonkeyPatch) -> None:
    from tools.douyin_dm_automation import automation

    class Entry:
        def __init__(self) -> None:
            self.scripts: list[str] = []
            self.scroll_timeouts: list[int] = []
            self.click_timeouts: list[int] = []

        async def evaluate(self, script: str) -> str:
            self.scripts.append(script)
            return "私信"

        async def scroll_into_view_if_needed(self, *, timeout: int) -> None:
            self.scroll_timeouts.append(timeout)

        async def click(self, *, timeout: int) -> None:
            self.click_timeouts.append(timeout)

    entry = Entry()

    async def discover(_page: object, _seconds: int) -> Entry:
        return entry

    monkeypatch.setattr(automation, "wait_for_private_message_button", discover)
    asyncio.run(automation.click_private_message_button(object(), 25))

    assert len(entry.scripts) == 1
    assert "target.innerText" in entry.scripts[0]
    assert "target.click()" not in entry.scripts[0]
    assert entry.scroll_timeouts == [automation.DM_CLICK_TIMEOUT_MS]
    assert entry.click_timeouts == [automation.DM_CLICK_TIMEOUT_MS]
    assert all(":text-is(" in selector and ":has-text(" not in selector for selector in automation.PROFILE_DM_SELECTORS)


def test_open_dm_panel_relocates_button_after_first_click_has_no_effect(monkeypatch: pytest.MonkeyPatch) -> None:
    from tools.douyin_dm_automation import automation

    class Page:
        url = "https://www.douyin.com/user/abc"

        async def wait_for_timeout(self, _value: int) -> None:
            return None

    page = Page()
    click_attempts = 0

    async def visible(_page: object, selectors: list[str]) -> object | None:
        if selectors is automation.CHAT_INPUT_SELECTORS:
            return object() if click_attempts >= 2 else None
        if selectors is automation.PROFILE_DM_SELECTORS:
            return object()
        raise AssertionError(f"未知选择器：{selectors}")

    async def discover(_page: object, _seconds: int) -> object:
        return object()

    async def click(_page: object, _seconds: int) -> None:
        nonlocal click_attempts
        click_attempts += 1

    async def no_op(_page: object) -> None:
        return None

    monkeypatch.setattr(automation, "first_visible", visible)
    monkeypatch.setattr(automation, "wait_for_private_message_button", discover)
    monkeypatch.setattr(automation, "click_private_message_button", click)
    monkeypatch.setattr(automation, "dismiss_easy_popups", no_op)
    monkeypatch.setattr(automation, "DM_CLICK_RESULT_WAIT_SECONDS", 0)

    asyncio.run(automation.open_dm_panel(page, 25))

    assert click_attempts == 2


def test_send_page_starts_work_after_navigation_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    from tools.douyin_dm_automation import automation

    class Page:
        def __init__(self) -> None:
            self.goto_options: dict[str, object] = {}

        async def goto(self, _url: str, **kwargs: object) -> None:
            self.goto_options = kwargs

    async def no_op(*_args: object, **_kwargs: object) -> None:
        return None

    page = Page()
    monkeypatch.setattr(automation, "open_dm_panel", no_op)
    monkeypatch.setattr(automation, "type_message", no_op)
    asyncio.run(automation.send_douyin_dm_on_page(page, "https://www.douyin.com/user/abc", "你好", dry_run=True))

    assert page.goto_options == {"wait_until": "commit", "timeout": 60000}


def test_message_workbench_loads_dm_module_from_backend_cwd() -> None:
    backend_root = WORKSPACE_ROOT / "backend"
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "from app.services.message_workbench import _load_douyin_dm_module; print(_load_douyin_dm_module().__name__)",
        ],
        cwd=backend_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "tools.douyin_dm_automation.automation"
