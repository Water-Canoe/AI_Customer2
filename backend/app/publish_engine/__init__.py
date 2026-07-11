"""抖音、快手和小红书内容发布引擎。"""

from __future__ import annotations

import os


# Patchright 始终使用项目预装的 Chromium，客户端无需全局浏览器缓存。
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", "0")
