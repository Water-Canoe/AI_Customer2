from __future__ import annotations

import asyncio
import threading
from contextlib import asynccontextmanager, contextmanager
from typing import AsyncIterator, Callable, Iterator


class BrowserQueueCancelled(RuntimeError):
    """Raised when a queued browser task is cancelled before it gets the slot."""


_BROWSER_LOCK = threading.Lock()
_POLL_SECONDS = 1.0


def _notify(callback: Callable[[str], None] | None, message: str) -> None:
    if not callback:
        return
    try:
        callback(message)
    except Exception:
        # Queue state must not depend on best-effort UI/log notifications.
        return


class BrowserSlot:
    def __init__(self, owner: str) -> None:
        self.owner = owner
        self._released = False

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        _BROWSER_LOCK.release()


def acquire(
    owner: str,
    on_wait: Callable[[str], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> BrowserSlot:
    """Acquire the single shared browser resource used by Douyin automation."""
    waited = False
    while True:
        if should_stop and should_stop():
            raise BrowserQueueCancelled(f"{owner} cancelled while waiting for browser")
        if _BROWSER_LOCK.acquire(blocking=False):
            if waited and on_wait:
                _notify(on_wait, "已获得浏览器资源，开始执行")
            return BrowserSlot(owner)
        if not waited:
            waited = True
            _notify(on_wait, "等待浏览器资源，前方已有任务正在使用浏览器")
        if _BROWSER_LOCK.acquire(timeout=_POLL_SECONDS):
            if waited:
                _notify(on_wait, "已获得浏览器资源，开始执行")
            return BrowserSlot(owner)


@contextmanager
def browser_slot(
    owner: str,
    on_wait: Callable[[str], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> Iterator[BrowserSlot]:
    slot = acquire(owner, on_wait=on_wait, should_stop=should_stop)
    try:
        yield slot
    finally:
        slot.release()


@asynccontextmanager
async def async_browser_slot(
    owner: str,
    on_wait: Callable[[str], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> AsyncIterator[BrowserSlot]:
    waited = False
    while True:
        if should_stop and should_stop():
            raise BrowserQueueCancelled(f"{owner} cancelled while waiting for browser")
        if _BROWSER_LOCK.acquire(blocking=False):
            break
        if not waited:
            waited = True
            _notify(on_wait, "等待浏览器资源，前方已有任务正在使用浏览器")
        await asyncio.sleep(_POLL_SECONDS)
    if waited:
        _notify(on_wait, "已获得浏览器资源，开始执行")
    slot = BrowserSlot(owner)
    try:
        yield slot
    finally:
        slot.release()
