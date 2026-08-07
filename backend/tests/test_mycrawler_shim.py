from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
import sys
import types

import httpx
import pytest


WORKSPACE_ROOT = Path(__file__).resolve().parents[2]


def _module():
    module_path = WORKSPACE_ROOT / "backend" / "app" / "mediacrawler_shims" / "sitecustomize.py"
    spec = importlib.util.spec_from_file_location("ai_customer_mycrawler_shim", module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_signature_guard_rejects_changed_upstream_method() -> None:
    shim = _module()

    def upstream(self, item_id, cursor=0):
        return self, item_id, cursor

    shim._require_signature(upstream, ("self", "item_id", "cursor"))
    def changed_upstream(self, item_id, *, cursor=0):
        return self, item_id, cursor

    with pytest.raises(RuntimeError, match="upstream signature changed"):
        shim._require_signature(changed_upstream, ("self", "item_id", "cursor"))


def test_douyin_creator_info_retries_then_skips_transient_network_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    shim = _module()
    messages: list[str] = []

    class FakeClient:
        def __init__(self, failures: int) -> None:
            self.failures = failures
            self.calls = 0

        async def get_user_info(self, sec_user_id):
            self.calls += 1
            if self.calls <= self.failures:
                raise httpx.ConnectError("offline")
            return {"sec_user_id": sec_user_id}

        async def get_user_aweme_posts(self, sec_user_id, max_cursor=""):
            return {"has_more": 0, "max_cursor": max_cursor, "aweme_list": []}

    class FakeCrawler:
        async def get_aweme_detail(self, aweme_id, semaphore):
            return None

        async def get_comments(self, aweme_id, semaphore):
            return None

    logger = types.SimpleNamespace(
        error=lambda message: messages.append(message),
        warning=lambda message: messages.append(message),
    )
    douyin_client = types.ModuleType("media_platform.douyin.client")
    douyin_client.DouYinClient = FakeClient
    douyin_core = types.ModuleType("media_platform.douyin.core")
    douyin_core.DouYinCrawler = FakeCrawler
    douyin_package = types.ModuleType("media_platform.douyin")
    douyin_package.client = douyin_client
    douyin_package.core = douyin_core
    media_platform = types.ModuleType("media_platform")
    media_platform.douyin = douyin_package
    tools = types.ModuleType("tools")
    tools.utils = types.SimpleNamespace(logger=logger)

    monkeypatch.setenv("AI_CUSTOMER_DY_RESILIENT_HTTP", "1")
    monkeypatch.setitem(sys.modules, "media_platform", media_platform)
    monkeypatch.setitem(sys.modules, "media_platform.douyin", douyin_package)
    monkeypatch.setitem(sys.modules, "media_platform.douyin.client", douyin_client)
    monkeypatch.setitem(sys.modules, "media_platform.douyin.core", douyin_core)
    monkeypatch.setitem(sys.modules, "tools", tools)

    async def no_wait(_seconds: float) -> None:
        return None

    monkeypatch.setattr(shim.asyncio, "sleep", no_wait)
    shim._patch_douyin_http_resilience()

    recovered = FakeClient(failures=2)
    unavailable = FakeClient(failures=9)
    assert asyncio.run(recovered.get_user_info("creator-ok")) == {"sec_user_id": "creator-ok"}
    assert recovered.calls == 3
    assert asyncio.run(unavailable.get_user_info("creator-skip")) == {}
    assert unavailable.calls == 3
    assert any("skip creator info creator-skip" in message for message in messages)
