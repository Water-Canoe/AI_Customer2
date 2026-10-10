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
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
APPLICATION_ENTRYPOINT = "runtime/application/AI_Customer_App.exe"
sys.path.insert(0, str(WORKSPACE_ROOT))


def _bootstrap_module():
    module_path = WORKSPACE_ROOT / "backend" / "app" / "services" / "desktop_update.py"
    spec = importlib.util.spec_from_file_location("ai_customer_bootstrap", module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_release(root: Path, version: str, contents: dict[str, bytes]) -> None:
    contents = {"AI_Customer.exe": f"launcher-{version}".encode(), **contents}
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
                "schema_version": 16,
                "updater_version": "1.1.0",
                "entrypoint": APPLICATION_ENTRYPOINT,
                "files": files,
            }
        ),
        encoding="utf-8",
    )


def _write_environment(root: Path) -> None:
    runtime = root / "runtime"
    (runtime / "cloakbrowser_browser").mkdir(parents=True, exist_ok=True)
    (runtime / "cloakbrowser_browser/chrome.exe").write_bytes(b"browser")
    (runtime / "environment-manifest.json").write_text(json.dumps({
        "format": 1, "product": "AI Customer Environment", "version": "1.0.0",
        "required_paths": ["cloakbrowser_browser/chrome.exe"],
    }), encoding="utf-8")


def _healthy_process(module, monkeypatch) -> None:
    process = type("HealthyProcess", (), {"pid": 100, "poll": lambda self: None})()
    monkeypatch.setattr(module, "_start_application", lambda _: process)
    monkeypatch.setattr(module, "_wait_for_application_health", lambda *_: None)


def test_bootstrap_resolves_only_valid_portable_version(tmp_path: Path) -> None:
    ai_customer_bootstrap = _bootstrap_module()
    executable = tmp_path / APPLICATION_ENTRYPOINT
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"test")
    current_manifest = {
        "format": 1,
        "product": "AI Customer Desktop",
        "version": "1.1.0",
        "environment_version": "1.0.0",
        "schema_version": 16,
        "updater_version": "1.1.0",
        "entrypoint": APPLICATION_ENTRYPOINT,
    }
    (tmp_path / "release-manifest.json").write_text(json.dumps(current_manifest), encoding="utf-8")

    assert ai_customer_bootstrap.current_version(tmp_path) == "1.1.0"
    assert ai_customer_bootstrap.application_executable(tmp_path) == executable

    current_manifest["version"] = "../unsafe"
    (tmp_path / "release-manifest.json").write_text(json.dumps(current_manifest), encoding="utf-8")
    with pytest.raises(RuntimeError, match="格式不正确"):
        ai_customer_bootstrap.current_version(tmp_path)


def test_release_update_replaces_program_files_and_preserves_environment_and_data(tmp_path: Path, monkeypatch) -> None:
    ai_customer_bootstrap = _bootstrap_module()
    _healthy_process(ai_customer_bootstrap, monkeypatch)
    release = tmp_path / "release"
    _write_release(
        release,
        "1.1.1",
        {
            APPLICATION_ENTRYPOINT: b"application-new",
            "runtime/frontend_dist/index.html": b"frontend-new",
        },
    )

    install_root = tmp_path / "installed"
    _write_release(
        install_root,
        "1.1.0",
        {
            APPLICATION_ENTRYPOINT: b"application-old",
            "runtime/frontend_dist/index.html": b"frontend-old",
            "runtime/frontend_dist/obsolete.js": b"obsolete",
        },
    )
    _write_environment(install_root)
    (install_root / "runtime" / "python").mkdir(parents=True)
    (install_root / "runtime" / "python" / "python.exe").write_bytes(b"environment")
    (install_root / "data").mkdir()
    with sqlite3.connect(install_root / "data" / "ai_customer.sqlite3") as connection:
        connection.execute("PRAGMA user_version = 16")
    original_data = (install_root / "data" / "ai_customer.sqlite3").read_bytes()

    assert ai_customer_bootstrap.apply_release(release, install_root) == "1.1.1"

    assert (install_root / "AI_Customer.exe").read_bytes() == b"launcher-1.1.1"
    assert (install_root / APPLICATION_ENTRYPOINT).read_bytes() == b"application-new"
    assert (install_root / "runtime" / "frontend_dist" / "index.html").read_bytes() == b"frontend-new"
    assert not (install_root / "runtime" / "frontend_dist" / "obsolete.js").exists()
    assert (install_root / "runtime" / "python" / "python.exe").read_bytes() == b"environment"
    assert (install_root / "data" / "ai_customer.sqlite3").read_bytes() == original_data
    assert json.loads((install_root / "release-manifest.json").read_text(encoding="utf-8"))["version"] == "1.1.1"
    assert not (install_root / "updates" / "update-transaction.json").exists()
    assert not (install_root / "updates" / "rollback").exists()


def test_release_update_rolls_back_all_program_files_on_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ai_customer_bootstrap = _bootstrap_module()
    _healthy_process(ai_customer_bootstrap, monkeypatch)
    install_root = tmp_path / "installed"
    release = tmp_path / "release"
    _write_release(
        install_root,
        "1.1.0",
        {
            APPLICATION_ENTRYPOINT: b"application-old",
            "runtime/frontend_dist/index.html": b"frontend-old",
            "runtime/frontend_dist/obsolete.js": b"obsolete",
        },
    )
    _write_release(
        release,
        "1.1.1",
        {
            APPLICATION_ENTRYPOINT: b"application-new",
            "runtime/frontend_dist/index.html": b"frontend-new",
            "runtime/frontend_dist/new.js": b"new",
        },
    )
    _write_environment(install_root)
    original_replace = ai_customer_bootstrap._replace_file

    def fail_new_application(source: Path, destination: Path) -> None:
        if release in source.parents and source.name == "AI_Customer_App.exe":
            raise OSError("simulated install failure")
        original_replace(source, destination)

    monkeypatch.setattr(ai_customer_bootstrap, "_replace_file", fail_new_application)
    with pytest.raises(RuntimeError, match="已恢复旧程序.*simulated install failure"):
        ai_customer_bootstrap.apply_release(release, install_root)

    assert ai_customer_bootstrap.current_version(install_root) == "1.1.0"
    assert (install_root / "AI_Customer.exe").read_bytes() == b"launcher-1.1.0"
    assert (install_root / APPLICATION_ENTRYPOINT).read_bytes() == b"application-old"
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
            APPLICATION_ENTRYPOINT: b"application-old",
            "runtime/frontend_dist/index.html": b"frontend-old",
        },
    )
    old_paths = ["AI_Customer.exe", APPLICATION_ENTRYPOINT, "runtime/frontend_dist/index.html"]
    new_paths = ["AI_Customer.exe", APPLICATION_ENTRYPOINT, "runtime/frontend_dist/new.js"]
    ai_customer_bootstrap._prepare_update_transaction(install_root, old_paths, new_paths)
    (install_root / APPLICATION_ENTRYPOINT).write_bytes(b"application-new")
    (install_root / "runtime/frontend_dist/index.html").unlink()
    (install_root / "runtime/frontend_dist/new.js").write_bytes(b"new")

    assert ai_customer_bootstrap._recover_interrupted_update(install_root) is True
    assert ai_customer_bootstrap.current_version(install_root) == "1.1.0"
    assert (install_root / APPLICATION_ENTRYPOINT).read_bytes() == b"application-old"
    assert (install_root / "runtime/frontend_dist/index.html").read_bytes() == b"frontend-old"
    assert not (install_root / "runtime/frontend_dist/new.js").exists()


def test_update_extraction_replaces_an_interrupted_staging_directory(tmp_path: Path) -> None:
    ai_customer_bootstrap = _bootstrap_module()
    release = tmp_path / "release"
    _write_release(release, "1.1.1", {APPLICATION_ENTRYPOINT: b"application-new"})
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
    assert (extracted / APPLICATION_ENTRYPOINT).read_bytes() == b"application-new"


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
            "schema_version": 16,
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
    with pytest.raises(RuntimeError, match="需要核心环境"):
        ai_customer_bootstrap._validate_update_offer(offer, "1.1.1", "2.0.0")

    offer["signature"] = base64.b64encode(b"0" * 64).decode("ascii")
    with pytest.raises(RuntimeError, match="签名或清单校验失败"):
        ai_customer_bootstrap._validate_update_offer(offer, "1.1.1", "1.0.0")


def _signed_offer(module, monkeypatch, package: bytes, **overrides):
    private_key = Ed25519PrivateKey.generate()
    monkeypatch.setattr(module, "TRUSTED_PUBLIC_KEY_PEM", private_key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo,
    ))
    manifest = {
        "format": 1, "product": "AI Customer Desktop", "version": "1.1.2",
        "platform": "windows", "arch": "x64", "environment_version": "1.0.0", "schema_version": 16,
        "min_updater_version": "1.0.0",
        "package": {"size": len(package), "sha256": hashlib.sha256(package).hexdigest()},
        **overrides,
    }
    text = json.dumps(manifest)
    return {
        "available": True, "downloadAllowed": True, "version": manifest["version"],
        "manifestText": text, "signature": base64.b64encode(private_key.sign(text.encode())).decode(),
        "downloadUrl": "https://example.test/download?token=SECRET",
    }


def _package_bytes(root: Path) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for path in root.rglob("*"):
            if path.is_file():
                archive.write(path, path.relative_to(root).as_posix())
    return output.getvalue()


def test_online_check_prepare_and_staged_entry_preserve_running_install(tmp_path, monkeypatch) -> None:
    module = _bootstrap_module()
    installed, release = tmp_path / "installed", tmp_path / "release"
    _write_release(installed, "1.1.1", {APPLICATION_ENTRYPOINT: b"old", "runtime/application/old.dll": b"old-dll"})
    _write_environment(installed)
    _write_release(release, "1.1.2", {APPLICATION_ENTRYPOINT: b"new", "runtime/application/new.dll": b"new-dll"})
    package = _package_bytes(release)
    offer = _signed_offer(module, monkeypatch, package)
    monkeypatch.setattr(module, "_read_update_identity", lambda _: ("license-SECRET", "device-SECRET"))
    monkeypatch.setattr(module, "_request_update_offer", lambda *_: offer)
    checked = module.check_remote_update(installed)
    assert checked["available"] and checked["can_prepare"]
    assert not (installed / "updates/prepared-update.json").exists()
    monkeypatch.setattr(module.urllib.request, "urlopen", lambda *_args, **_kw: io.BytesIO(package))
    progress = []
    assert module.prepare_remote_update(installed, checked, lambda *event: progress.append(event))["ready"]
    assert module.current_version(installed) == "1.1.1"
    assert (installed / APPLICATION_ENTRYPOINT).read_bytes() == b"old"
    stored = (installed / "updates/prepared-update.json").read_text()
    assert "SECRET" not in stored and "downloadUrl" not in stored
    assert progress[-1][1] == 100
    calls = []
    monkeypatch.setenv("AI_CUSTOMER_BOOTSTRAP_PID", "888")
    monkeypatch.setattr(module.subprocess, "Popen", lambda arguments, **kw: calls.append(arguments) or "spawned")
    assert module.start_prepared_update(installed, 777) == "spawned"
    assert calls[0][0] == str(installed / "updates/1.1.2.extracting/AI_Customer.exe")
    assert calls[0][1:] == ["--apply-prepared", "--install-root", str(installed), "--wait-for-pid", "777", "--wait-for-launcher-pid", "888"]
    # ZIP hash 是持久可信边界，修改解压清单不能绕开它。
    archive = installed / "updates/1.1.2.zip"
    archive.write_bytes(b"tampered")
    with pytest.raises(RuntimeError, match="校验失败"):
        module.start_prepared_update(installed, 777)
    assert len(calls) == 1


@pytest.mark.parametrize("overrides,reason", [
    ({"schema_version": 17}, "数据库结构 17"),
    ({"environment_version": "2.0.0"}, "核心环境 2.0.0"),
    ({"min_updater_version": "2.0.0"}, "更新器 2.0.0"),
])
def test_check_explains_install_requirements_before_download(tmp_path, monkeypatch, overrides, reason) -> None:
    module = _bootstrap_module()
    _write_release(tmp_path, "1.1.1", {APPLICATION_ENTRYPOINT: b"old"})
    _write_environment(tmp_path)
    (tmp_path / "data").mkdir()
    with sqlite3.connect(tmp_path / "data/ai_customer.sqlite3") as connection:
        connection.execute("PRAGMA user_version = 16")
    offer = _signed_offer(module, monkeypatch, b"zip", **overrides)
    monkeypatch.setattr(module, "_read_update_identity", lambda _: ("license", "device"))
    monkeypatch.setattr(module, "_request_update_offer", lambda *_: offer)
    checked = module.check_remote_update(tmp_path)
    assert checked["available"] and not checked["can_prepare"]
    assert reason in checked["reason"]
    assert not (tmp_path / "updates/1.1.2.zip").exists()


def test_manual_check_does_not_stop_or_restart_when_no_update(tmp_path, monkeypatch) -> None:
    module = _bootstrap_module()
    _write_release(tmp_path, "1.1.1", {APPLICATION_ENTRYPOINT: b"old"})
    _write_environment(tmp_path)
    monkeypatch.setattr(module, "_read_update_identity", lambda _: ("license", "device"))
    monkeypatch.setattr(module, "_request_update_offer", lambda *_: {"available": False})
    assert module.check_remote_update(tmp_path) == {
        "current_version": "1.1.1", "available": False, "can_prepare": False, "reason": "当前已是最新版本",
    }


def test_failed_startup_stops_new_process_restores_dependencies_and_restarts_old(tmp_path, monkeypatch) -> None:
    module = _bootstrap_module()
    installed, release = tmp_path / "installed", tmp_path / "release"
    _write_release(installed, "1.1.1", {APPLICATION_ENTRYPOINT: b"old", "runtime/application/native.dll": b"old-library"})
    _write_environment(installed)
    _write_release(release, "1.1.2", {APPLICATION_ENTRYPOINT: b"new", "runtime/application/native.dll": b"new-library"})
    events = []
    monkeypatch.setattr(module, "_start_application", lambda _: events.append(module.current_version(installed)) or object())
    monkeypatch.setattr(module, "_stop_application", lambda _: events.append("stop-new"))

    def unhealthy(*_):
        transaction = json.loads((installed / "updates/update-transaction.json").read_text())
        assert transaction["state"] == "prepared"
        assert (installed / "updates/rollback/runtime/application/native.dll").read_bytes() == b"old-library"
        raise RuntimeError("health-failed")

    monkeypatch.setattr(module, "_wait_for_application_health", unhealthy)
    with pytest.raises(RuntimeError, match="已恢复旧程序.*health-failed"):
        module.apply_release(release, installed)
    assert events == ["1.1.2", "stop-new", "1.1.1"]
    assert (installed / "AI_Customer.exe").read_bytes() == b"launcher-1.1.1"
    assert (installed / "runtime/application/native.dll").read_bytes() == b"old-library"


@pytest.mark.parametrize("path", ["../data/db", "data/db", "runtime/models/model.bin", "runtime/MyCrawler/MyCrawler.exe", "C:/data/db", "runtime/application/a:stream", "runtime/application/../x", "runtime/application/CON.exe"])
def test_update_archive_rejects_data_components_and_unsafe_paths(tmp_path, path) -> None:
    module = _bootstrap_module()
    archive = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr(path, b"unsafe")
    with pytest.raises(RuntimeError):
        module._extract_update(archive, tmp_path, "1.1.2")


def test_health_requires_new_version_and_actual_child_pid(monkeypatch) -> None:
    module = _bootstrap_module()
    process = type("Process", (), {"pid": 123, "poll": lambda self: None})()
    manifest = {"version": "1.1.2", "environment_version": "1.0.0", "schema_version": 16, "updater_version": "1.1.0"}
    healthy = {"status": "ok", "product": "ai-customer", "packaged": True, "process_id": 123, **manifest}
    values = iter([
        {**healthy, field: value} for field, value in (
            ("process_id", 99), ("version", "1.1.1"), ("environment_version", "2.0.0"),
            ("schema_version", 17), ("updater_version", "1.0.0"), ("packaged", False), ("product", "other"),
        )
    ] + [healthy])
    monkeypatch.setattr(module.urllib.request, "urlopen", lambda *_args, **_kw: io.BytesIO(json.dumps(next(values)).encode()))
    module._wait_for_application_health(process, manifest)
    with pytest.raises(StopIteration):
        next(values)


def test_launcher_passes_onefile_host_pid_but_not_nuitka_role_to_application(tmp_path, monkeypatch) -> None:
    module = _bootstrap_module()
    _write_release(tmp_path, "1.1.1", {APPLICATION_ENTRYPOINT: b"old"})
    monkeypatch.setenv("NUITKA_ONEFILE_PARENT", "789")
    monkeypatch.setenv("AI_CUSTOMER_BOOTSTRAP_PID", "stale-pid")
    calls = []
    process = type("Process", (), {"wait": lambda self: None})()
    monkeypatch.setattr(module.subprocess, "Popen", lambda *_args, **kwargs: calls.append(kwargs) or process)
    module.launch_current(tmp_path)
    assert calls[0]["env"]["AI_CUSTOMER_BOOTSTRAP_PID"] == "789"
    assert "NUITKA_ONEFILE_PARENT" not in calls[0]["env"]
    module._start_application(tmp_path)
    assert "AI_CUSTOMER_BOOTSTRAP_PID" not in calls[1]["env"]
    assert "NUITKA_ONEFILE_PARENT" not in calls[1]["env"]


def test_normal_start_does_not_contact_update_server(tmp_path, monkeypatch) -> None:
    module = _bootstrap_module()
    _write_release(tmp_path, "1.1.1", {APPLICATION_ENTRYPOINT: b"old"})
    _write_environment(tmp_path)
    monkeypatch.setattr(module.sys, "argv", [str(tmp_path / "AI_Customer.exe")])
    monkeypatch.setattr(module, "_running_workbench_url", lambda: "")
    launched = []
    monkeypatch.setattr(module, "launch_current", launched.append)
    monkeypatch.setattr(module, "_request_update_offer", lambda *_: pytest.fail("ordinary launch must stay offline"))
    module.main()
    assert launched == [tmp_path]


def test_cancelled_prepare_never_publishes_ready_plan(tmp_path, monkeypatch) -> None:
    module = _bootstrap_module()
    installed, release = tmp_path / "installed", tmp_path / "release"
    _write_release(installed, "1.1.1", {APPLICATION_ENTRYPOINT: b"old"})
    _write_environment(installed)
    _write_release(release, "1.1.2", {APPLICATION_ENTRYPOINT: b"new"})
    package = _package_bytes(release)
    offer = _signed_offer(module, monkeypatch, package)
    monkeypatch.setattr(module, "_read_update_identity", lambda _: ("license", "device"))
    monkeypatch.setattr(module, "_request_update_offer", lambda *_: offer)
    checked = module.check_remote_update(installed)
    monkeypatch.setattr(module.urllib.request, "urlopen", lambda *_args, **_kw: io.BytesIO(package))

    def cancel_at_ready(message, percent):
        if percent == 100:
            raise RuntimeError("application-shutdown")

    with pytest.raises(RuntimeError, match="application-shutdown"):
        module.prepare_remote_update(installed, checked, cancel_at_ready)
    assert not module.prepared_update(installed)["ready"]
    assert module.current_version(installed) == "1.1.1"


def test_interrupted_start_delegates_root_entry_recovery_to_rollback_entry(tmp_path, monkeypatch) -> None:
    module = _bootstrap_module()
    _write_release(tmp_path, "1.1.1", {APPLICATION_ENTRYPOINT: b"old"})
    old = ["AI_Customer.exe", APPLICATION_ENTRYPOINT]
    module._prepare_update_transaction(tmp_path, old, old)
    (tmp_path / APPLICATION_ENTRYPOINT).write_bytes(b"partially-updated")
    monkeypatch.setattr(module.sys, "argv", [str(tmp_path / "AI_Customer.exe")])
    monkeypatch.setenv("NUITKA_ONEFILE_PARENT", "789")
    monkeypatch.setattr(module, "_running_workbench_url", lambda: "")
    calls = []
    monkeypatch.setattr(module.subprocess, "Popen", lambda arguments, **kw: calls.append(arguments))
    module.main()
    assert calls == [[
        str(tmp_path / "updates/rollback/AI_Customer.exe"), "--recover-install", "--install-root", str(tmp_path),
        "--wait-for-pid", "789",
    ]]
    assert (tmp_path / APPLICATION_ENTRYPOINT).read_bytes() == b"partially-updated"


def test_bad_remote_signature_is_reported_as_safe_check_reason(tmp_path, monkeypatch) -> None:
    module = _bootstrap_module()
    _write_release(tmp_path, "1.1.1", {APPLICATION_ENTRYPOINT: b"old"})
    _write_environment(tmp_path)
    offer = _signed_offer(module, monkeypatch, b"zip")
    offer["signature"] = base64.b64encode(b"0" * 64).decode()
    monkeypatch.setattr(module, "_read_update_identity", lambda _: ("license", "device"))
    monkeypatch.setattr(module, "_request_update_offer", lambda *_: offer)
    assert "签名或清单校验失败" in module.check_remote_update(tmp_path)["reason"]
    offer["signature"] = "invalid-base64"
    assert "签名或清单校验失败" in module.check_remote_update(tmp_path)["reason"]


@pytest.mark.parametrize("redirected", ["updates", "updates/1.1.2.zip", "updates/1.1.2.extracting", "updates/rollback"])
def test_update_paths_reject_directory_redirection_without_changing_data(tmp_path, redirected) -> None:
    module = _bootstrap_module()
    data = tmp_path / "data"
    data.mkdir()
    sentinel = data / "customer.txt"
    sentinel.write_bytes(b"customer-data")
    link = tmp_path / redirected
    link.parent.mkdir(parents=True, exist_ok=True)
    if module.os.name == "nt":
        # junction 无需管理员权限，模拟 Windows 更新目录被重定向到客户数据。
        result = module.subprocess.run(
            ["cmd", "/d", "/c", "mklink", "/J", str(link), str(data)],
            capture_output=True, creationflags=getattr(module.subprocess, "CREATE_NO_WINDOW", 0),
        )
        assert result.returncode == 0, result.stderr.decode(errors="replace")
    else:
        link.symlink_to(data, target_is_directory=True)
    with pytest.raises(RuntimeError, match="重定向|越界"):
        module._update_path(tmp_path, Path(redirected).name if redirected != "updates" else "update.lock")
    with pytest.raises(RuntimeError, match="重定向|越界"):
        module._remove_tree(link, tmp_path / "updates")
    assert sentinel.read_bytes() == b"customer-data"


def test_stable_launcher_finds_an_existing_workbench(monkeypatch: pytest.MonkeyPatch) -> None:
    ai_customer_bootstrap = _bootstrap_module()

    def fake_urlopen(request: object, **_: object):
        if "8010" not in str(request):
            raise ai_customer_bootstrap.urllib.error.URLError("closed")
        return io.BytesIO(json.dumps({"status": "ok", "product": "ai-customer", "packaged": True}).encode())

    monkeypatch.setattr(ai_customer_bootstrap.urllib.request, "urlopen", fake_urlopen)

    assert ai_customer_bootstrap._running_workbench_url() == "http://127.0.0.1:8010"


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
