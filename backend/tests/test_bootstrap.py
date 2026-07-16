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


def test_bootstrap_resolves_only_valid_portable_version(tmp_path: Path) -> None:
    ai_customer_bootstrap = _bootstrap_module()
    executable = tmp_path / "AI_Customer_App.exe"
    executable.write_bytes(b"test")
    current_manifest = {
        "format": 1,
        "product": "AI Customer Desktop",
        "version": "1.1.0",
        "environment_version": "1.0.0",
        "entrypoint": "AI_Customer_App.exe",
    }
    (tmp_path / "release-manifest.json").write_text(json.dumps(current_manifest), encoding="utf-8")

    assert ai_customer_bootstrap.current_version(tmp_path) == "1.1.0"
    assert ai_customer_bootstrap.application_executable(tmp_path) == executable

    current_manifest["version"] = "../unsafe"
    (tmp_path / "release-manifest.json").write_text(json.dumps(current_manifest), encoding="utf-8")
    with pytest.raises(RuntimeError, match="格式不正确"):
        ai_customer_bootstrap.current_version(tmp_path)


def test_release_update_replaces_program_files_and_preserves_environment_and_data(tmp_path: Path) -> None:
    ai_customer_bootstrap = _bootstrap_module()
    release = tmp_path / "release"
    frontend = release / "runtime" / "frontend_dist"
    frontend.mkdir(parents=True)
    (release / "AI_Customer.exe").write_bytes(b"launcher")
    (release / "AI_Customer_App.exe").write_bytes(b"application-new")
    (frontend / "index.html").write_bytes(b"frontend-new")
    declared = ["AI_Customer_App.exe", "runtime/frontend_dist/index.html"]
    files = [
        {"path": relative, "size": (release / relative).stat().st_size, "sha256": _sha256(release / relative)}
        for relative in declared
    ]
    (release / "release-manifest.json").write_text(
        json.dumps(
            {
                "format": 1,
                "product": "AI Customer Desktop",
                "version": "1.1.1",
                "environment_version": "1.0.0",
                "entrypoint": "AI_Customer_App.exe",
                "files": files,
            }
        ),
        encoding="utf-8",
    )

    install_root = tmp_path / "installed"
    (install_root / "runtime" / "python").mkdir(parents=True)
    (install_root / "runtime" / "python" / "python.exe").write_bytes(b"environment")
    (install_root / "data").mkdir()
    (install_root / "data" / "ai_customer.sqlite3").write_bytes(b"customer-data")
    (install_root / "AI_Customer.exe").write_bytes(b"stable-running-launcher")

    assert ai_customer_bootstrap.apply_release(release, install_root) == "1.1.1"

    assert (install_root / "AI_Customer.exe").read_bytes() == b"stable-running-launcher"
    assert (install_root / "AI_Customer_App.exe").read_bytes() == b"application-new"
    assert (install_root / "runtime" / "frontend_dist" / "index.html").read_bytes() == b"frontend-new"
    assert (install_root / "runtime" / "python" / "python.exe").read_bytes() == b"environment"
    assert (install_root / "data" / "ai_customer.sqlite3").read_bytes() == b"customer-data"
    assert json.loads((install_root / "release-manifest.json").read_text(encoding="utf-8"))["version"] == "1.1.1"


def test_environment_manifest_requires_matching_version_and_files(tmp_path: Path) -> None:
    ai_customer_bootstrap = _bootstrap_module()
    (tmp_path / "release-manifest.json").write_text(
        json.dumps({"version": "1.1.1", "environment_version": "1.0.0"}), encoding="utf-8"
    )
    runtime = tmp_path / "runtime"
    (runtime / "python").mkdir(parents=True)
    (runtime / "python" / "python.exe").write_bytes(b"python")
    (runtime / "environment-manifest.json").write_text(
        json.dumps(
            {
                "format": 1,
                "product": "AI Customer Environment",
                "version": "1.0.0",
                "required_paths": ["python/python.exe"],
            }
        ),
        encoding="utf-8",
    )

    assert ai_customer_bootstrap.validate_environment(tmp_path) == "1.0.0"
    (runtime / "python" / "python.exe").unlink()
    with pytest.raises(RuntimeError, match="环境包文件不完整"):
        ai_customer_bootstrap.validate_environment(tmp_path)


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
            "environment_version": "1.0.0",
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

    assert ai_customer_bootstrap._validate_update_offer(offer, "1.1.1", "1.0.0") == {
        "version": "1.1.2",
        "size": 123,
        "sha256": "a" * 64,
        "download_url": "https://example.test/AI_Customer_1.1.2.zip",
    }
    with pytest.raises(RuntimeError, match="不适用于当前程序"):
        ai_customer_bootstrap._validate_update_offer(offer, "1.1.1", "2.0.0")

    offer["signature"] = base64.b64encode(b"0" * 64).decode("ascii")
    with pytest.raises(InvalidSignature):
        ai_customer_bootstrap._validate_update_offer(offer, "1.1.1", "1.0.0")
