from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(WORKSPACE_ROOT))


def _bootstrap_module():
    module_path = WORKSPACE_ROOT / "packaging" / "ai_customer_bootstrap.py"
    spec = importlib.util.spec_from_file_location("ai_customer_bootstrap", module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_bootstrap_resolves_only_valid_installed_version(tmp_path: Path) -> None:
    ai_customer_bootstrap = _bootstrap_module()
    version_dir = tmp_path / "versions" / "1.1.0"
    version_dir.mkdir(parents=True)
    executable = version_dir / "AI_Customer.exe"
    executable.write_bytes(b"test")
    (tmp_path / "current-version.json").write_text(json.dumps({"version": "1.1.0"}), encoding="utf-8")

    assert ai_customer_bootstrap.current_version(tmp_path) == "1.1.0"
    assert ai_customer_bootstrap.version_executable(tmp_path, "1.1.0") == executable

    (tmp_path / "current-version.json").write_text(json.dumps({"version": "../unsafe"}), encoding="utf-8")
    with pytest.raises(RuntimeError, match="格式不正确"):
        ai_customer_bootstrap.current_version(tmp_path)


def test_release_install_uses_manifest_allowlist_and_preserves_extra_runtime_files(tmp_path: Path) -> None:
    ai_customer_bootstrap = _bootstrap_module()
    release = tmp_path / "release"
    app = release / "app"
    app.mkdir(parents=True)
    (release / "AI_Customer.exe").write_bytes(b"launcher")
    (app / "AI_Customer.exe").write_bytes(b"application")
    extra_database = app / "data" / "ai_customer.sqlite3"
    extra_database.parent.mkdir()
    extra_database.write_bytes(b"runtime-data")
    declared = ["AI_Customer.exe", "app/AI_Customer.exe"]
    files = [
        {"path": relative, "size": (release / relative).stat().st_size, "sha256": _sha256(release / relative)}
        for relative in declared
    ]
    (release / "release-manifest.json").write_text(
        json.dumps({"format": 1, "product": "AI Customer Desktop", "version": "1.1.1", "files": files}),
        encoding="utf-8",
    )

    install_root = tmp_path / "installed"
    assert ai_customer_bootstrap.install_release(release, install_root) == "1.1.1"

    assert (install_root / "AI_Customer.exe").read_bytes() == b"launcher"
    assert (install_root / "versions" / "1.1.1" / "AI_Customer.exe").read_bytes() == b"application"
    assert not (install_root / "versions" / "1.1.1" / "data" / "ai_customer.sqlite3").exists()
    assert json.loads((install_root / "current-version.json").read_text(encoding="utf-8"))["version"] == "1.1.1"


def test_remote_update_offer_requires_a_valid_signature(monkeypatch: pytest.MonkeyPatch) -> None:
    ai_customer_bootstrap = _bootstrap_module()
    private_key = Ed25519PrivateKey.generate()
    monkeypatch.setattr(
        ai_customer_bootstrap,
        "TRUSTED_PUBLIC_KEY_PEM",
        private_key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        ),
    )
    manifest_text = json.dumps(
        {
            "format": 1,
            "product": "AI Customer Desktop",
            "version": "1.1.2",
            "platform": "windows",
            "arch": "x64",
            "min_updater_version": ai_customer_bootstrap.UPDATER_VERSION,
            "package": {"size": 123, "sha256": "a" * 64},
        },
        separators=(",", ":"),
    )
    offer = {
        "available": True,
        "downloadAllowed": True,
        "version": "1.1.2",
        "manifestText": manifest_text,
        "signature": base64.b64encode(private_key.sign(manifest_text.encode("utf-8"))).decode("ascii"),
        "downloadUrl": "https://example.test/AI_Customer_1.1.2.zip",
    }

    assert ai_customer_bootstrap._validate_update_offer(offer, "1.1.1") == {
        "version": "1.1.2",
        "size": 123,
        "sha256": "a" * 64,
        "download_url": "https://example.test/AI_Customer_1.1.2.zip",
    }

    offer["signature"] = base64.b64encode(b"0" * 64).decode("ascii")
    with pytest.raises(InvalidSignature):
        ai_customer_bootstrap._validate_update_offer(offer, "1.1.1")
