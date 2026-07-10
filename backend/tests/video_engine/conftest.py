from collections.abc import Iterator
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolate_video_engine_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    # 上游服务测试只能读写pytest临时目录，不能触碰用户的稳定data目录。
    monkeypatch.setenv("AI_CUSTOMER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AI_CUSTOMER_DB", str(tmp_path / "ai_customer.sqlite3"))
    from app import database
    from app.services import content_workbench
    from app.video_engine.config import config

    database.init_db()
    defaults = content_workbench.default_settings()
    config.apply_runtime_config(defaults)
    yield
    config.apply_runtime_config(defaults)
