from __future__ import annotations

import sys
from pathlib import Path

import pytest


BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))


def test_profile_manager_excludes_runtime_and_interactive_sessions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import profile_manager

    class FakeProcess:
        pid = 102
        returncode = None

        def poll(self):
            return self.returncode

    fake = FakeProcess()
    profile_manager.shutdown()
    assert profile_manager.acquire_runtime("runtime-1") is True
    with pytest.raises(ValueError, match="自动化任务正在执行"):
        profile_manager.launch_interactive("dy", ["fake"], cwd=tmp_path, log_path=tmp_path / "login.log")
    profile_manager.release_runtime("runtime-1")

    monkeypatch.setattr(profile_manager.subprocess, "Popen", lambda *args, **kwargs: fake)
    monkeypatch.setattr(profile_manager.time, "sleep", lambda _: None)
    monkeypatch.setattr(profile_manager, "_terminate_process_tree", lambda process, timeout_seconds=5.0: setattr(process, "returncode", 0))
    opened = profile_manager.launch_interactive("dy", ["fake"], cwd=tmp_path, log_path=tmp_path / "login.log")

    assert opened == {"started": True, "active": True, "platform": "dy"}
    assert profile_manager.acquire_runtime("runtime-2") is False
    assert profile_manager.status()["interactive_active"] is True
    assert profile_manager.close_interactive() == {"ok": True, "closed": True, "platform": "dy"}
    assert profile_manager.acquire_runtime("runtime-2") is True
    profile_manager.release_runtime("runtime-2")
