from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.publish_engine import service
from app.publish_engine.uploader.base_video import BaseVideoUploader


class _FakeUploader:
    def __init__(self, *, fail_after_submit: bool = False) -> None:
        self.submitted = False
        self.before_submit = None
        self.fail_after_submit = fail_after_submit

    async def main(self) -> None:
        await BaseVideoUploader.begin_submit(self)
        if self.fail_after_submit:
            raise RuntimeError("结果未确认")


def test_publish_marks_uncertain_result_for_manual_review(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    account_file = tmp_path / "account.json"
    account_file.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(service, "_build_uploader", lambda **_: _FakeUploader(fail_after_submit=True))

    with pytest.raises(service.PublishReviewRequired):
        asyncio.run(
            service.publish(
                platform="dy",
                content_type="video",
                account_file=account_file,
                title="测试",
                description="测试内容",
                tags=[],
                media_paths=[tmp_path / "video.mp4"],
            )
        )
