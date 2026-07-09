from __future__ import annotations

import json
import importlib.util
import sys
from pathlib import Path

import pytest


WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(WORKSPACE_ROOT))


def test_bootstrap_resolves_only_valid_installed_version(tmp_path: Path) -> None:
    module_path = WORKSPACE_ROOT / "packaging" / "ai_customer_bootstrap.py"
    spec = importlib.util.spec_from_file_location("ai_customer_bootstrap", module_path)
    assert spec and spec.loader
    ai_customer_bootstrap = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ai_customer_bootstrap)

    version_dir = tmp_path / "versions" / "1.1.0"
    version_dir.mkdir(parents=True)
    executable = version_dir / "AI_Customer.exe"
    executable.write_bytes(b"test")
    (tmp_path / "current-version.json").write_text(json.dumps({"version": "1.1.0"}), encoding="utf-8")

    assert ai_customer_bootstrap.current_version(tmp_path) == "1.1.0"
    assert ai_customer_bootstrap.version_executable(tmp_path, "1.1.0") == executable

    (tmp_path / "current-version.json").write_text(json.dumps({"version": "../unsafe"}), encoding="utf-8")
    with pytest.raises(RuntimeError, match="格式不正确"):
        ai_customer_bootstrap.current_version(tmp_path)
