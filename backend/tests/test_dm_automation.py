from __future__ import annotations

import asyncio
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


def test_private_message_button_clicks_discovered_visible_entry_once(monkeypatch: pytest.MonkeyPatch) -> None:
    from tools.douyin_dm_automation import automation

    class Entry:
        def __init__(self) -> None:
            self.clicks: list[tuple[int, bool]] = []

        async def click(self, *, timeout: int, force: bool) -> None:
            self.clicks.append((timeout, force))

    entry = Entry()

    async def discover(_page: object, _seconds: int) -> Entry:
        return entry

    monkeypatch.setattr(automation, "wait_for_private_message_button", discover)
    asyncio.run(automation.click_private_message_button(object(), 25))

    assert entry.clicks == [(2000, True)]


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
