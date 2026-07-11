from pathlib import Path

from app.publish_engine.conf import RUNTIME_DIR

Path(RUNTIME_DIR / "accounts").mkdir(parents=True, exist_ok=True)
