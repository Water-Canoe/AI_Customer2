"""发行边界与版本排序共用的语义化版本规则。"""

import re


_NUMBER = r"(?:0|[1-9][0-9]*)"
_IDENTIFIER = r"(?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*)"
_RELEASE_PATTERN = (
    rf"({_NUMBER})\.({_NUMBER})\.({_NUMBER})"
    rf"(?:-({_IDENTIFIER}(?:\.{_IDENTIFIER})*))?"
)
# 线上发行契约暂不使用构建标识；通用排序仍遵守完整 SemVer 2.0.0。
RELEASE_VERSION_PATTERN = re.compile(_RELEASE_PATTERN)
VERSION_PATTERN = re.compile(_RELEASE_PATTERN + r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?")


def version_key(value: str) -> tuple:
    """数字预发布段按数值排序，构建标识不参与优先级。"""
    match = VERSION_PATTERN.fullmatch(value)
    if not match:
        raise ValueError(f"版本号不是标准 SemVer：{value}")
    core = tuple(int(match[index]) for index in (1, 2, 3))
    suffix = match[4]
    identifiers = tuple(
        (0, int(part)) if part.isdigit() else (1, part)
        for part in suffix.split(".")
    ) if suffix else ()
    return core, suffix is None, identifiers
