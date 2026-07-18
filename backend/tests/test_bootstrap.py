from __future__ import annotations

import base64
import hashlib
import importlib.util
import io
import json
import sqlite3
import sys
import zipfile
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


def _write_release(root: Path, version: str, contents: dict[str, bytes]) -> None:
    for relative_path, content in contents.items():
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    files = [
        {"path": path, "size": len(content), "sha256": hashlib.sha256(content).hexdigest()}
        for path, content in sorted(contents.items())
    ]
    (root / "release-manifest.json").write_text(
        json.dumps(
            {
                "format": 1,
                "product": "AI Customer Desktop",
                "version": version,
                "environment_version": "1.0.0",
                "entrypoint": "AI_Customer_App.exe",
                "files": files,
            }
        ),
        encoding="utf-8",
    )


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
    _write_release(
        release,
        "1.1.1",
        {
            "AI_Customer_App.exe": b"application-new",
            "runtime/frontend_dist/index.html": b"frontend-new",
        },
    )

    install_root = tmp_path / "installed"
    _write_release(
        install_root,
        "1.1.0",
        {
            "AI_Customer_App.exe": b"application-old",
            "runtime/frontend_dist/index.html": b"frontend-old",
            "runtime/frontend_dist/obsolete.js": b"obsolete",
        },
    )
    (install_root / "runtime" / "python").mkdir(parents=True)
    (install_root / "runtime" / "python" / "python.exe").write_bytes(b"environment")
    (install_root / "data").mkdir()
    (install_root / "data" / "ai_customer.sqlite3").write_bytes(b"customer-data")
    (install_root / "AI_Customer.exe").write_bytes(b"stable-running-launcher")

    assert ai_customer_bootstrap.apply_release(release, install_root) == "1.1.1"

    assert (install_root / "AI_Customer.exe").read_bytes() == b"stable-running-launcher"
    assert (install_root / "AI_Customer_App.exe").read_bytes() == b"application-new"
    assert (install_root / "runtime" / "frontend_dist" / "index.html").read_bytes() == b"frontend-new"
    assert not (install_root / "runtime" / "frontend_dist" / "obsolete.js").exists()
    assert (install_root / "runtime" / "python" / "python.exe").read_bytes() == b"environment"
    assert (install_root / "data" / "ai_customer.sqlite3").read_bytes() == b"customer-data"
    assert json.loads((install_root / "release-manifest.json").read_text(encoding="utf-8"))["version"] == "1.1.1"
    assert not (install_root / "updates" / "update-transaction.json").exists()
    assert not (install_root / "updates" / "rollback").exists()


def test_release_update_rolls_back_all_program_files_on_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ai_customer_bootstrap = _bootstrap_module()
    install_root = tmp_path / "installed"
    release = tmp_path / "release"
    _write_release(
        install_root,
        "1.1.0",
        {
            "AI_Customer_App.exe": b"application-old",
            "runtime/frontend_dist/index.html": b"frontend-old",
            "runtime/frontend_dist/obsolete.js": b"obsolete",
        },
    )
    _write_release(
        release,
        "1.1.1",
        {
            "AI_Customer_App.exe": b"application-new",
            "runtime/frontend_dist/index.html": b"frontend-new",
            "runtime/frontend_dist/new.js": b"new",
        },
    )
    original_replace = ai_customer_bootstrap._replace_file

    def fail_new_application(source: Path, destination: Path) -> None:
        if release in source.parents and source.name == "AI_Customer_App.exe":
            raise OSError("simulated install failure")
        original_replace(source, destination)

    monkeypatch.setattr(ai_customer_bootstrap, "_replace_file", fail_new_application)
    with pytest.raises(OSError, match="simulated install failure"):
        ai_customer_bootstrap.apply_release(release, install_root)

    assert ai_customer_bootstrap.current_version(install_root) == "1.1.0"
    assert (install_root / "AI_Customer_App.exe").read_bytes() == b"application-old"
    assert (install_root / "runtime/frontend_dist/index.html").read_bytes() == b"frontend-old"
    assert (install_root / "runtime/frontend_dist/obsolete.js").read_bytes() == b"obsolete"
    assert not (install_root / "runtime/frontend_dist/new.js").exists()
    assert not (install_root / "updates/update-transaction.json").exists()


def test_launcher_recovers_a_prepared_update_transaction_after_interruption(tmp_path: Path) -> None:
    ai_customer_bootstrap = _bootstrap_module()
    install_root = tmp_path / "installed"
    _write_release(
        install_root,
        "1.1.0",
        {
            "AI_Customer_App.exe": b"application-old",
            "runtime/frontend_dist/index.html": b"frontend-old",
        },
    )
    old_paths = ["AI_Customer_App.exe", "runtime/frontend_dist/index.html"]
    new_paths = ["AI_Customer_App.exe", "runtime/frontend_dist/new.js"]
    ai_customer_bootstrap._prepare_update_transaction(install_root, old_paths, new_paths)
    (install_root / "AI_Customer_App.exe").write_bytes(b"application-new")
    (install_root / "runtime/frontend_dist/index.html").unlink()
    (install_root / "runtime/frontend_dist/new.js").write_bytes(b"new")

    assert ai_customer_bootstrap._recover_interrupted_update(install_root) is True
    assert ai_customer_bootstrap.current_version(install_root) == "1.1.0"
    assert (install_root / "AI_Customer_App.exe").read_bytes() == b"application-old"
    assert (install_root / "runtime/frontend_dist/index.html").read_bytes() == b"frontend-old"
    assert not (install_root / "runtime/frontend_dist/new.js").exists()


def test_update_extraction_replaces_an_interrupted_staging_directory(tmp_path: Path) -> None:
    ai_customer_bootstrap = _bootstrap_module()
    release = tmp_path / "release"
    _write_release(release, "1.1.1", {"AI_Customer_App.exe": b"application-new"})
    archive = tmp_path / "update.zip"
    with zipfile.ZipFile(archive, "w") as output:
        for path in release.rglob("*"):
            if path.is_file():
                output.write(path, path.relative_to(release).as_posix())
    stale = tmp_path / "updates" / "1.1.1.extracting"
    stale.mkdir(parents=True)
    (stale / "partial.tmp").write_bytes(b"partial")

    extracted = ai_customer_bootstrap._extract_update(archive, tmp_path, "1.1.1")

    assert extracted == stale
    assert not (extracted / "partial.tmp").exists()
    assert (extracted / "AI_Customer_App.exe").read_bytes() == b"application-new"


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


def test_update_identity_uses_windows_bound_device_code(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ai_customer_bootstrap = _bootstrap_module()
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    with sqlite3.connect(data_dir / "ai_customer.sqlite3") as connection:
        connection.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        connection.executemany(
            "INSERT INTO settings(key, value) VALUES(?, ?)",
            [("license_code", "LIC-TEST"), ("device_code", "AI-CUS-COPIED")],
        )
    machine_code = "AI-CUS-11111111-22222222-33333333-44444444"
    monkeypatch.setattr(ai_customer_bootstrap.device_identity, "get_device_code", lambda: machine_code)

    assert ai_customer_bootstrap._read_update_identity(tmp_path) == ("LIC-TEST", machine_code)


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
