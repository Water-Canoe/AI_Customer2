from __future__ import annotations

import asyncio
import sys
from pathlib import Path


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
