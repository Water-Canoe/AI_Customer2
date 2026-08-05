from __future__ import annotations

import importlib.util
from pathlib import Path

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
