from __future__ import annotations

import base64
import hashlib
import importlib.util
import io
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
        "mandatory": False,
        "notes": "",
    }
    with pytest.raises(RuntimeError, match="不适用于当前程序"):
        ai_customer_bootstrap._validate_update_offer(offer, "1.1.1", "2.0.0")

    offer["signature"] = base64.b64encode(b"0" * 64).decode("ascii")
    with pytest.raises(InvalidSignature):
        ai_customer_bootstrap._validate_update_offer(offer, "1.1.1", "1.0.0")


def test_remote_update_waits_for_confirmation_and_forwards_progress(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ai_customer_bootstrap = _bootstrap_module()
    update = {"version": "1.1.2", "size": 10, "sha256": "a" * 64, "download_url": "https://example.test/update.zip"}
    events: list[tuple[str, int]] = []
    calls: list[str] = []
    monkeypatch.setattr(ai_customer_bootstrap, "current_version", lambda _: "1.1.1")
    monkeypatch.setattr(ai_customer_bootstrap, "validate_environment", lambda _: "1.0.0")
    monkeypatch.setattr(ai_customer_bootstrap, "_read_update_identity", lambda _: ("license", "device"))
    monkeypatch.setattr(ai_customer_bootstrap, "_request_update_offer", lambda *_: {"available": True})
    monkeypatch.setattr(ai_customer_bootstrap, "_validate_update_offer", lambda *_: update)

    def download(_: dict[str, object], __: Path, progress) -> Path:
        calls.append("download")
        progress("正在下载", 50)
        return tmp_path / "update.zip"

    monkeypatch.setattr(ai_customer_bootstrap, "_download_update", download)
    monkeypatch.setattr(ai_customer_bootstrap, "_extract_update", lambda *_: tmp_path / "release")
    monkeypatch.setattr(ai_customer_bootstrap, "apply_release", lambda *_: "1.1.2")

    assert ai_customer_bootstrap.apply_remote_update(tmp_path, confirm=lambda _: False, progress=lambda *item: events.append(item)) is False
    assert calls == []
    assert ai_customer_bootstrap.apply_remote_update(tmp_path, confirm=lambda _: True, progress=lambda *item: events.append(item)) is True
    assert calls == ["download"]
    assert events == [("正在下载", 50)]

    update["mandatory"] = True
    def fail_download(*_: object) -> Path:
        raise RuntimeError("network")

    monkeypatch.setattr(ai_customer_bootstrap, "_download_update", fail_download)
    with pytest.raises(RuntimeError, match="必须更新失败"):
        ai_customer_bootstrap.apply_remote_update(tmp_path, confirm=lambda _: True)


def test_manual_update_reports_latest_and_validates_wait_pid(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ai_customer_bootstrap = _bootstrap_module()
    messages: list[str] = []
    monkeypatch.setattr(ai_customer_bootstrap, "current_version", lambda _: "1.1.1")
    monkeypatch.setattr(ai_customer_bootstrap, "validate_environment", lambda _: "1.0.0")
    monkeypatch.setattr(ai_customer_bootstrap, "_read_update_identity", lambda _: ("license", "device"))
    monkeypatch.setattr(ai_customer_bootstrap, "_request_update_offer", lambda *_: {"available": False})

    assert ai_customer_bootstrap.apply_remote_update(tmp_path, check_feedback=messages.append) is False
    assert messages == ["当前已是最新版本。"]
    assert ai_customer_bootstrap._manual_update_parent_pid(["AI_Customer.exe"]) is None
    assert ai_customer_bootstrap._manual_update_parent_pid(["AI_Customer.exe", "--wait-for-pid", "123"]) == 123
    with pytest.raises(RuntimeError, match="进程编号无效"):
        ai_customer_bootstrap._manual_update_parent_pid(["AI_Customer.exe", "--wait-for-pid", "invalid"])


def test_update_download_reports_real_byte_progress(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ai_customer_bootstrap = _bootstrap_module()
    payload = b"program-update"
    events: list[tuple[str, int]] = []
    monkeypatch.setattr(ai_customer_bootstrap.urllib.request, "urlopen", lambda *_args, **_kwargs: io.BytesIO(payload))

    archive = ai_customer_bootstrap._download_update(
        {
            "version": "1.1.2",
            "size": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
            "download_url": "https://example.test/update.zip",
        },
        tmp_path,
        lambda *item: events.append(item),
    )

    assert archive.read_bytes() == payload
    assert events[0] == ("正在下载程序更新", 0)
    assert events[-1] == ("更新包下载完成，正在校验", 82)
    assert any(percent == 80 for _, percent in events)
