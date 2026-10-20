from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


ROOT = Path(__file__).resolve().parents[2]
POWERSHELL = shutil.which("powershell.exe")
pytestmark = pytest.mark.skipif(not POWERSHELL, reason="Windows PowerShell is required")


def prepare_publisher(tmp_path: Path) -> dict[str, Path]:
    project = tmp_path / "project"
    script = project / "script"
    internal = script / "internal"
    internal.mkdir(parents=True)
    publisher = (ROOT / "script" / "publish_release.ps1").read_text(encoding="utf-8-sig")
    publisher = publisher.replace(
        'Join-Path $ProjectRoot "backend\\.venv\\Scripts\\python.exe"',
        "'" + sys.executable.replace("'", "''") + "'",
    )
    (script / "publish_release.ps1").write_text(publisher, encoding="utf-8-sig")
    shutil.copyfile(ROOT / "script" / "internal" / "release_signing.py", internal / "release_signing.py")
    shutil.copyfile(ROOT / "script" / "internal" / "_version.ps1", internal / "_version.ps1")
    app = project / "backend" / "app"
    app.mkdir(parents=True)
    (app / "__init__.py").write_text("", encoding="utf-8")
    shutil.copyfile(ROOT / "backend" / "app" / "product_config.py", app / "product_config.py")
    archive = project / "program.zip"
    archive.write_bytes(b"verified program fixture")
    metadata = {
        "version": "1.2.99", "environment_version": "1.1.0", "schema_version": 16,
        "updater_version": "1.1.0", "source_revision": "a" * 40, "source_dirty": False,
        "built_at": "2026-10-08T08:00:00+00:00", "size": archive.stat().st_size,
        "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
    }
    # ZIP content is covered by assembler tests; here the real publisher consumes its verified output.
    (internal / "verify_program_zip.py").write_text(
        "import json\nprint(json.dumps(" + repr(metadata) + "))\n", encoding="utf-8",
    )
    key = Ed25519PrivateKey.generate()
    private_key = tmp_path / "private.pem"
    public_key = tmp_path / "public.pem"
    private_key.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    public_key.write_bytes(key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
    registered = {
        "version": "1.2.99", "channel": "stable", "platform": "windows", "arch": "x64",
        "objectKey": "releases/1.2.99/AI_Customer_1.2.99_windows_x64.zip", "releaseId": "test-release",
        "packageSize": metadata["size"], "sha256": metadata["sha256"], "enabled": True,
        "mandatory": True, "rolloutPercent": 10, "notes": "original notes",
    }
    state = tmp_path / "server.json"
    state.write_text(json.dumps(registered), encoding="utf-8")
    return {"project": project, "archive": archive, "private": private_key, "public": public_key, "state": state}


def run_publish(env: dict[str, Path], args: dict[str, object], *, failure: str = "", admin_token: str = "offline-token-" + "x" * 32,
                service_url: str = "https://backend.invalid", powershell: str | None = POWERSHELL
                ) -> tuple[subprocess.CompletedProcess[str], list[dict[str, object]]]:
    tmp_path = env["project"].parent
    arguments = tmp_path / "arguments.json"
    arguments.write_text(json.dumps(args), encoding="utf-8")
    runner = tmp_path / "run.ps1"
    runner.write_text(r'''
$ErrorActionPreference = "Stop"
$global:MockCalls = New-Object System.Collections.Generic.List[object]
$global:MockRelease = Get-Content -LiteralPath $env:MOCK_STATE -Raw | ConvertFrom-Json
function Invoke-RestMethod {
    param($Uri, $Method, $Headers, $ContentType, $Body, $TimeoutSec)
    $global:MockCalls.Add(@{ method = $Method; url = $Uri; path = ([Uri]$Uri).AbsolutePath; body = $Body; timeout = $TimeoutSec })
    if ($Method -eq "GET") { return [pscustomobject]@{ code = 200; data = @($global:MockRelease | Where-Object { $null -ne $_ }) } }
    $Payload = $Body | ConvertFrom-Json
    if ($Uri.EndsWith("/upload-url")) {
        return [pscustomobject]@{ code = 200; data = @{ objectKey = "releases/1.2.99/AI_Customer_1.2.99_windows_x64.zip"; uploadUrl = "https://offline.invalid/upload" } }
    }
    if ($Uri.EndsWith("/release-status")) {
        foreach ($Field in @("enabled", "mandatory", "rolloutPercent", "notes")) { $global:MockRelease.$Field = $Payload.$Field }
        if ($env:MOCK_FAILURE -eq "readback") { $global:MockRelease.notes = "unexpected notes" }
        return [pscustomobject]@{ code = 200; data = $global:MockRelease }
    }
    if ($env:MOCK_FAILURE -eq "register") { return [pscustomobject]@{ code = 500 } }
    $Manifest = $Payload.manifestText | ConvertFrom-Json
    $global:MockRelease = [pscustomobject]@{
        version = $Manifest.version; channel = $Payload.channel; platform = $Manifest.platform; arch = $Manifest.arch
        objectKey = $Manifest.package.object_key; packageSize = $Manifest.package.size; sha256 = $Manifest.package.sha256
        releaseId = "test-release"; enabled = $false; mandatory = $false; rolloutPercent = 0; notes = $Payload.notes
    }
    return [pscustomobject]@{ code = 201; data = $global:MockRelease }
}
function curl.exe {
    $global:MockCalls.Add(@{ method = "OBJECT_PUT"; path = "offline" })
    $global:LASTEXITCODE = 0
}
$Arguments = @{}
(Get-Content -LiteralPath $env:MOCK_ARGS -Raw | ConvertFrom-Json).PSObject.Properties | ForEach-Object { $Arguments[$_.Name] = $_.Value }
$Failed = $false
try { & $env:MOCK_PUBLISHER @Arguments }
catch { Write-Output $_.Exception.Message; $Failed = $true }
finally {
    ConvertTo-Json -InputObject @($global:MockCalls.ToArray()) -Depth 10 | Set-Content -LiteralPath $env:MOCK_CALLS -Encoding UTF8
    ConvertTo-Json -InputObject $global:MockRelease -Depth 10 | Set-Content -LiteralPath $env:MOCK_STATE -Encoding UTF8
}
if ($Failed) { exit 1 }
''', encoding="utf-8")
    calls_path = tmp_path / "calls.json"
    result = subprocess.run(
        [str(powershell), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(runner)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
        env={**os.environ, "PYTHONUTF8": "1", "MINI_BACKEND_ADMIN_TOKEN": admin_token,
             "MINI_BACKEND_URL": service_url,
             "MOCK_STATE": str(env["state"]), "MOCK_ARGS": str(arguments), "MOCK_CALLS": str(calls_path),
             "MOCK_PUBLISHER": str(env["project"] / "script" / "publish_release.ps1"), "MOCK_FAILURE": failure},
    )
    calls = json.loads(calls_path.read_text(encoding="utf-8-sig"))
    return result, calls


@pytest.mark.parametrize("powershell", [POWERSHELL, shutil.which("pwsh.exe")], ids=["windows-powershell", "pwsh"])
@pytest.mark.parametrize("action", ["publish", "set-state"])
def test_distribution_serializes_canonical_field_names(tmp_path: Path, powershell: str | None, action: str) -> None:
    if not powershell:
        pytest.skip("PowerShell executable is unavailable")
    env = prepare_publisher(tmp_path)
    args = {"Action": action, "Version": "1.2.99", "Mandatory": False, "RolloutPercent": 75, "Notes": "updated"}
    if action == "publish":
        args.update({"ZipPath": str(env["archive"]), "PublicKeyPath": str(env["public"]), "Enable": True})
    else:
        args["Enabled"] = True
    result, calls = run_publish(env, args, powershell=powershell)
    assert result.returncode == 0, result.stdout + result.stderr
    # 检查真正发出的 JSON；PowerShell 属性匹配会忽略大小写，无法代表服务端行为。
    payload = json.loads(str(next(call["body"] for call in calls if call["method"] == "PUT")))
    assert set(payload) == {"version", "channel", "platform", "arch", "enabled", "mandatory", "rolloutPercent", "notes"}
    assert payload["enabled"] is True and payload["mandatory"] is False
    assert payload["rolloutPercent"] == 75 and payload["notes"] == "updated"


def test_publisher_defaults_to_production_when_run_from_source(tmp_path: Path) -> None:
    env = prepare_publisher(tmp_path)
    result, calls = run_publish(env, {"Action": "status", "Version": "1.2.99"}, service_url="")
    assert result.returncode == 0, result.stdout + result.stderr
    assert calls[0]["url"] == "https://omgyspevxzji.sealosbja.site/api/v1/products/ai-customer/update/admin/releases"


def test_prepare_requires_explicit_artifact_and_records_source(tmp_path: Path) -> None:
    env = prepare_publisher(tmp_path)
    result, calls = run_publish(env, {})
    assert result.returncode != 0
    assert "Specify -Version" in result.stdout and "-ZipPath" in result.stdout
    assert not calls
    result, calls = run_publish(env, {"ZipPath": str(env["archive"]), "PrivateKeyPath": str(env["private"]), "PublicKeyPath": str(env["public"])})
    assert result.returncode == 0, result.stdout + result.stderr
    assert not calls
    manifest = next((env["project"] / "output").glob("*/update-manifest.json"))
    assert json.loads(manifest.read_text())["min_updater_version"] == "1.1.0"
    output = json.loads(manifest.with_name("publish-result.json").read_text())
    assert output["source_revision"] == "a" * 40
    assert output["source_dirty"] is False
    result, calls = run_publish(env, {"ZipPath": str(env["archive"]), "PublicKeyPath": str(env["public"]), "MinUpdaterVersion": "1.0.0"})
    assert result.returncode != 0
    assert "must match" in result.stdout
    assert not calls


def test_version_selects_only_its_single_artifact(tmp_path: Path) -> None:
    env = prepare_publisher(tmp_path)
    delivery = env["project"] / "deliverables" / "1.2.99"
    delivery.mkdir(parents=True)
    shutil.copyfile(env["archive"], delivery / "AI_Customer_Program_1.2.99.zip")
    args = {"Version": "1.2.99", "PrivateKeyPath": str(env["private"]), "PublicKeyPath": str(env["public"])}
    result, calls = run_publish(env, args)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not calls
    shutil.copyfile(env["archive"], delivery / "AI_Customer_Program_ambiguous.zip")
    result, calls = run_publish(env, args)
    assert result.returncode != 0 and "exactly one" in result.stdout
    assert not calls


def test_missing_delivery_and_status_version_fail_before_network(tmp_path: Path) -> None:
    env = prepare_publisher(tmp_path)
    result, calls = run_publish(env, {"Version": "1.2.99", "PublicKeyPath": str(env["public"])})
    assert result.returncode != 0 and "No delivery directory" in result.stdout
    assert str(env["project"] / "deliverables" / "1.2.99") in result.stdout
    assert not calls
    result, calls = run_publish(env, {"Action": "status"})
    assert result.returncode != 0 and "Specify -Version for status" in result.stdout
    assert not calls


@pytest.mark.parametrize("enabled", [True, False])
def test_existing_release_can_change_distribution_without_upload(tmp_path: Path, enabled: bool) -> None:
    env = prepare_publisher(tmp_path)
    stored = json.loads(env["state"].read_text())
    stored["enabled"] = enabled
    env["state"].write_text(json.dumps(stored))
    result, calls = run_publish(env, {"ZipPath": str(env["archive"]), "PublicKeyPath": str(env["public"]), "Enable": True, "Mandatory": False, "Notes": "updated"})
    assert result.returncode == 0, result.stdout + result.stderr
    assert [call["method"] for call in calls] == ["GET", "PUT", "GET"]
    assert all(call["timeout"] == 30 for call in calls)
    state = json.loads(env["state"].read_text(encoding="utf-8-sig"))
    assert state["enabled"] is True and state["mandatory"] is False
    assert state["rolloutPercent"] == 100 and state["notes"] == "updated"


def test_status_can_disable_without_package_or_key_and_checks_readback(tmp_path: Path) -> None:
    env = prepare_publisher(tmp_path)
    result, calls = run_publish(env, {"Action": "set-state", "Version": "1.2.99", "Enabled": False})
    assert result.returncode == 0, result.stdout + result.stderr
    state = json.loads(env["state"].read_text(encoding="utf-8-sig"))
    assert state["enabled"] is False and state["mandatory"] is True
    assert state["rolloutPercent"] == 10 and state["notes"] == "original notes"
    assert [call["method"] for call in calls] == ["GET", "PUT", "GET"]
    result, calls = run_publish(env, {"Action": "status", "Version": "1.2.99"})
    assert result.returncode == 0
    assert [call["method"] for call in calls] == ["GET"]
    assert calls[0]["path"] == "/api/v1/products/ai-customer/update/admin/releases"
    result, _ = run_publish(env, {"Action": "set-state", "Version": "1.2.99", "Notes": "changed"}, failure="readback")
    assert result.returncode != 0 and "read-back differs" in result.stdout


def test_invalid_archive_fails_before_token_or_remote_request(tmp_path: Path) -> None:
    env = prepare_publisher(tmp_path)
    (env["project"] / "script" / "internal" / "verify_program_zip.py").write_text("raise SystemExit(1)\n")
    result, calls = run_publish(
        env, {"ZipPath": str(env["archive"]), "PublicKeyPath": str(env["public"]), "Upload": True}, admin_token="",
    )
    assert result.returncode != 0
    assert "Program ZIP verification failed" in result.stdout
    assert not calls


def test_failed_registration_resumes_exact_signature_without_reupload(tmp_path: Path) -> None:
    env = prepare_publisher(tmp_path)
    env["state"].write_text("null")
    args = {"ZipPath": str(env["archive"]), "PrivateKeyPath": str(env["private"]), "PublicKeyPath": str(env["public"]), "Upload": True}
    result, calls = run_publish(env, args, failure="register")
    assert result.returncode != 0, result.stdout
    assert any(call["method"] == "OBJECT_PUT" for call in calls)
    manifest = next((env["project"] / "output").glob("*/update-manifest.json"))
    original = manifest.read_bytes()
    signature = manifest.with_suffix(".sig").read_bytes()
    result, calls = run_publish(env, {"ZipPath": str(env["archive"]), "PublicKeyPath": str(env["public"]), "ResumeManifest": str(manifest)})
    assert result.returncode == 0, result.stdout + result.stderr
    assert [call["method"] for call in calls] == ["GET", "POST", "GET"]
    assert all(not str(call["path"]).endswith("upload-url") for call in calls)
    assert manifest.read_bytes() == original and manifest.with_suffix(".sig").read_bytes() == signature
    registration = json.loads(str(calls[1]["body"]))
    assert registration["manifestText"].encode() == original
    assert registration["signature"].encode() == signature
    manifest.write_bytes(original.replace(b"1.2.99", b"1.2.98"))
    result, calls = run_publish(env, {"ZipPath": str(env["archive"]), "PublicKeyPath": str(env["public"]), "ResumeManifest": str(manifest)})
    assert result.returncode != 0 and "signature verification failed" in result.stdout
    assert not calls


@pytest.mark.parametrize("enable", [True, False])
def test_new_release_can_be_registered_disabled_or_enabled_explicitly(tmp_path: Path, enable: bool) -> None:
    env = prepare_publisher(tmp_path)
    env["state"].write_text("null")
    args = {"ZipPath": str(env["archive"]), "PrivateKeyPath": str(env["private"]), "PublicKeyPath": str(env["public"])}
    args["Enable" if enable else "Upload"] = True
    result, calls = run_publish(env, args)
    assert result.returncode == 0, result.stdout + result.stderr
    state = json.loads(env["state"].read_text(encoding="utf-8-sig"))
    assert state["enabled"] is enable
    assert state["rolloutPercent"] == (100 if enable else 0)
    assert [call["method"] for call in calls].count("OBJECT_PUT") == 1


def test_existing_version_refuses_different_package(tmp_path: Path) -> None:
    env = prepare_publisher(tmp_path)
    state = json.loads(env["state"].read_text())
    state["sha256"] = "f" * 64
    env["state"].write_text(json.dumps(state))
    result, calls = run_publish(env, {"ZipPath": str(env["archive"]), "PublicKeyPath": str(env["public"]), "Enable": True})
    assert result.returncode != 0 and "differs" in result.stdout
    assert [call["method"] for call in calls] == ["GET"]


def test_missing_shared_admin_token_stops_before_remote_request(tmp_path: Path) -> None:
    env = prepare_publisher(tmp_path)
    result, calls = run_publish(env, {"Action": "status", "Version": "1.2.99"}, admin_token="")
    assert result.returncode != 0 and "MINI_BACKEND_ADMIN_TOKEN" in result.stdout
    assert not calls
