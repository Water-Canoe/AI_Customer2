from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolate_device_identity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # 测试设备身份必须与真实机器的 ProgramData 隔离。
    monkeypatch.setenv("AI_CUSTOMER_DEVICE_IDENTITY_PATH", str(tmp_path / "device_identity.bin"))
