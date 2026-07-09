from __future__ import annotations

import base64
import hashlib
import os
import subprocess
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


ROOT = Path(__file__).resolve().parents[2]
SIGNING_TOOL = ROOT / "script" / "release_signing.py"
TRUSTED_PUBLIC_KEY = ROOT / "packaging" / "update_signing_public.pem"


def run_tool(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SIGNING_TOOL), *args],
        check=check,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONUTF8": "1"},
    )


def test_release_signing_round_trip_and_tamper_rejection(tmp_path: Path) -> None:
    private_key = tmp_path / "release-private.pem"
    public_key = tmp_path / "release-public.pem"
    manifest = tmp_path / "manifest.json"
    signature = tmp_path / "manifest.sig"
    manifest.write_text('{"version":"1.2.0"}', encoding="utf-8")

    run_tool(
        "generate",
        "--private-key",
        str(private_key),
        "--public-key",
        str(public_key),
    )
    run_tool(
        "sign",
        "--private-key",
        str(private_key),
        "--input",
        str(manifest),
        "--output",
        str(signature),
    )
    run_tool(
        "verify",
        "--public-key",
        str(public_key),
        "--input",
        str(manifest),
        "--signature",
        str(signature),
    )
    assert len(base64.b64decode(signature.read_text(encoding="ascii"), validate=True)) == 64

    # Any byte change must invalidate the detached signature.
    manifest.write_text('{"version":"9.9.9"}', encoding="utf-8")
    result = run_tool(
        "verify",
        "--public-key",
        str(public_key),
        "--input",
        str(manifest),
        "--signature",
        str(signature),
        check=False,
    )
    assert result.returncode != 0

    signature.write_text("invalid-base64", encoding="ascii")
    result = run_tool(
        "verify",
        "--public-key",
        str(public_key),
        "--input",
        str(manifest),
        "--signature",
        str(signature),
        check=False,
    )
    assert result.returncode != 0
    assert "Base64" in result.stderr


def test_release_key_generation_refuses_overwrite(tmp_path: Path) -> None:
    private_key = tmp_path / "release-private.pem"
    public_key = tmp_path / "release-public.pem"
    run_tool(
        "generate",
        "--private-key",
        str(private_key),
        "--public-key",
        str(public_key),
    )
    result = run_tool(
        "generate",
        "--private-key",
        str(private_key),
        "--public-key",
        str(public_key),
        check=False,
    )
    assert result.returncode != 0


def test_committed_update_public_key_is_ed25519() -> None:
    key = serialization.load_pem_public_key(TRUSTED_PUBLIC_KEY.read_bytes())
    assert isinstance(key, Ed25519PublicKey)
    raw = key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    assert hashlib.sha256(raw).hexdigest() == "187b00ee49f5ba2666b4722a3a569ec119bd3f6731300a2abb8e536abc0499f3"
