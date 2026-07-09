from __future__ import annotations

import sys
from pathlib import Path

import pytest


BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))


def test_douyin_ready_wait_returns_on_first_page_signal(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import traffic_workbench

    class Page:
        def __init__(self) -> None:
            self.checks = 0
            self.waits: list[int] = []

        def evaluate(self, _: str) -> bool:
            self.checks += 1
            return self.checks == 2

        def wait_for_timeout(self, value: int) -> None:
            self.waits.append(value)

    page = Page()
    monkeypatch.setattr(traffic_workbench, "_raise_if_stop_requested", lambda _: None)

    assert traffic_workbench._wait_for_douyin_ready_signal(page, "run-1", 4000) is True
    assert page.checks == 2
    assert page.waits == [200]
