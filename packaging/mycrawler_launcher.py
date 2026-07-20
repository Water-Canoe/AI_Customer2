from __future__ import annotations

import sys
from pathlib import Path

# SQLite stores its runtime database beside the standalone component.
Path(sys.argv[0]).resolve().parent.joinpath("database").mkdir(exist_ok=True)

import sitecustomize  # noqa: F401
import main as crawler_app
from tools.app_runner import run


def _force_stop() -> None:
    crawler = crawler_app.crawler
    cdp_manager = getattr(crawler, "cdp_manager", None) if crawler else None
    launcher = getattr(cdp_manager, "launcher", None)
    if launcher:
        launcher.cleanup()


if __name__ == "__main__":
    run(crawler_app.main, crawler_app.async_cleanup, cleanup_timeout_seconds=15.0, on_first_interrupt=_force_stop)
