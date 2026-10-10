from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from app.semver import RELEASE_VERSION_PATTERN, version_key
from app.services import desktop_update, voice_runtime


ORDERED = [
    "1.0.0-alpha", "1.0.0-alpha.1", "1.0.0-alpha.beta", "1.0.0-beta",
    "1.0.0-beta.2", "1.0.0-beta.10", "1.0.0-beta.11", "1.0.0-rc.1", "1.0.0", "1.0.1",
]
INVALID = [
    "", "v1.0.0", "01.0.0", "1.00.0", "1.0.01", "1.0.0-01", "1.0.0-beta.01",
    "1.0.0-", "1.0.0-a..b", "1.0.0+", "1.0.0+a..b", "1.0.0\n", "１.0.0",
]


def test_runtime_consumers_share_semver_ordering() -> None:
    for current, candidate in zip(ORDERED, ORDERED[1:]):
        assert version_key(candidate) > version_key(current)
        for compare in (desktop_update._is_newer_version, voice_runtime._is_newer_version):
            assert compare(candidate, current)
            assert not compare(current, candidate)
    assert version_key("1.0.0+build.2") == version_key("1.0.0+build.10") == version_key("1.0.0")
    assert version_key("1.0.0-01a") < version_key("1.0.0")


@pytest.mark.parametrize("value", INVALID)
def test_invalid_versions_are_rejected(value: str) -> None:
    with pytest.raises(ValueError):
        version_key(value)
    for compare in (desktop_update._is_newer_version, voice_runtime._is_newer_version):
        with pytest.raises(RuntimeError):
            compare(value, "1.0.0")


def test_powershell_and_python_release_contract_match(tmp_path: Path) -> None:
    powershell = shutil.which("powershell.exe")
    if not powershell:
        pytest.skip("Windows PowerShell is required")
    vectors = ORDERED + INVALID + ["1.0.0+build.2", "1.0.0-01a", "0.0.0", "1.0.0-A"]
    inputs = tmp_path / "versions.json"
    inputs.write_text(json.dumps(vectors), encoding="utf-8")
    runner = tmp_path / "versions.ps1"
    # 批量调用真实格式校验器；不运行打包、签名或网络操作。
    runner.write_text('''
$ErrorActionPreference = "Stop"
. $env:VERSION_HELPER
$Versions = Get-Content -LiteralPath $env:VERSION_INPUTS -Raw -Encoding utf8 | ConvertFrom-Json
$Results = @($Versions | ForEach-Object {
    try { Assert-SemVer $_ "Version"; $true } catch { $false }
})
ConvertTo-Json -InputObject $Results -Compress
''', encoding="utf-8-sig")
    result = subprocess.run(
        [powershell, "-NoProfile", "-File", str(runner)], capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=15,
        env={**os.environ, "VERSION_INPUTS": str(inputs), "VERSION_HELPER": str(
            Path(__file__).resolve().parents[2] / "script/internal/_version.ps1"
        )},
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == [bool(RELEASE_VERSION_PATTERN.fullmatch(value)) for value in vectors]
    assert not RELEASE_VERSION_PATTERN.fullmatch("1.0.0+build.2")
