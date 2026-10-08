from __future__ import annotations

import re
from pathlib import PurePosixPath


_WINDOWS_DEVICE = re.compile(r"^(CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])(?:\.|$)", re.IGNORECASE)


def safe_release_path(value: object) -> str:
    """组装、发布和安装共用 Windows 文件规则，拒绝重定向及特殊设备路径。"""
    path = str(value or "").replace("\\", "/")
    pure = PurePosixPath(path)
    if (
        not path or pure.is_absolute() or pure.as_posix() != path or ".." in pure.parts
        or any(ord(char) < 32 or char in '<>:"|?*' for char in path)
        or any(part.endswith((" ", ".")) or _WINDOWS_DEVICE.match(part) for part in pure.parts)
    ):
        raise RuntimeError(f"发布包包含不安全的文件路径：{path}")
    return path
