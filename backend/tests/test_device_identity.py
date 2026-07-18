from __future__ import annotations

import asyncio
import base64
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app import database, device_identity
from app.services import license_service


DEVICE_PATTERN = re.compile(r"^AI-CUS-(?:[A-F0-9]{8}-){3}[A-F0-9]{8}$")


def _prepare_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_CUSTOMER_DB", str(tmp_path / "ai_customer.sqlite3"))
    database.init_db()


def _signed_lease(device_code: str, monkeypatch: pytest.MonkeyPatch) -> tuple[str, str]:
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    monkeypatch.setattr(license_service.product_config, "license_public_key_pem", lambda: public_key)
    now = datetime.now(timezone.utc)
    lease_text = json.dumps({
        "version": 1,
        "licenseId": "license-test",
        "deviceId": device_code,
        "entitlements": ["lead", "traffic", "content"],
        "issuedAt": now.isoformat(),
        "expiresAt": (now + timedelta(hours=72)).isoformat(),
    }, separators=(",", ":"))
    return lease_text, base64.b64encode(private_key.sign(lease_text.encode("utf-8"))).decode("ascii")


@pytest.mark.skipif(device_identity.os.name != "nt", reason="DPAPI 仅支持 Windows")
def test_dpapi_identity_is_stable_on_current_windows(tmp_path: Path) -> None:
    path = tmp_path / "machine" / "device_identity.bin"

    first = device_identity.get_device_code(path)
    second = device_identity.get_device_code(path)

    assert DEVICE_PATTERN.fullmatch(first)
    assert second == first
    assert path.is_file()


def test_foreign_identity_blob_is_replaced(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    path = tmp_path / "device_identity.bin"
    path.write_bytes(b"local:" + (b"A" * device_identity.SECRET_SIZE))
    monkeypatch.setattr(device_identity, "_unprotect", lambda payload: payload.removeprefix(b"local:"))
    previous = device_identity.get_device_code(path)
    monkeypatch.setattr(device_identity, "_unprotect", lambda _: (_ for _ in ()).throw(OSError("wrong machine")))
    monkeypatch.setattr(device_identity, "_protect", lambda secret: b"local:" + secret)

    replaced = device_identity.get_device_code(path)
    monkeypatch.setattr(device_identity, "_unprotect", lambda payload: payload.removeprefix(b"local:"))

    assert DEVICE_PATTERN.fullmatch(replaced)
    assert replaced != previous
    assert device_identity.get_device_code(path) == replaced
    assert path.read_bytes().startswith(b"local:")


def test_machine_code_replaces_copied_code_and_clears_lease(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _prepare_database(tmp_path, monkeypatch)
    new_code = "AI-CUS-11111111-22222222-33333333-44444444"
    monkeypatch.setattr(device_identity, "get_device_code", lambda: new_code)
    with database.connect() as conn:
        database.set_setting(conn, "license_code", "LIC-KEEP")
        database.set_setting(conn, "device_code", "AI-CUS-OLD00001-OLD00002")
        database.set_setting(conn, "license_lease_text", "copied-lease")
        database.set_setting(conn, "license_lease_signature", "copied-signature")
        database.set_setting(conn, "license_max_devices", "1")
        database.set_setting(conn, "license_active_device_count", "1")

    overview = license_service.license_overview()

    assert overview["device_code"] == new_code
    assert overview["license_code"] == "LIC-KEEP"
    assert overview["authorized"] is False
    assert overview["reason"] == "DEVICE_IDENTITY_CHANGED"
    with database.connect() as conn:
        assert database.get_setting(conn, "license_lease_text") == ""
        assert database.get_setting(conn, "license_lease_signature") == ""
        assert database.get_setting(conn, "license_max_devices") == ""
        assert database.get_setting(conn, "license_active_device_count") == ""


def test_offline_lease_only_applies_to_current_machine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _prepare_database(tmp_path, monkeypatch)
    first_code = "AI-CUS-AAAAAAAA-BBBBBBBB-CCCCCCCC-DDDDDDDD"
    second_code = "AI-CUS-11111111-22222222-33333333-44444444"
    current_code = [first_code]
    monkeypatch.setattr(device_identity, "get_device_code", lambda: current_code[0])
    lease_text, signature = _signed_lease(first_code, monkeypatch)
    with database.connect() as conn:
        database.set_setting(conn, "license_code", "LIC-OFFLINE")
        database.set_setting(conn, "device_code", first_code)
        database.set_setting(conn, "license_lease_text", lease_text)
        database.set_setting(conn, "license_lease_signature", signature)
    monkeypatch.setattr(
        license_service,
        "_request_license",
        lambda *_: (_ for _ in ()).throw(httpx.ConnectError("offline")),
    )

    assert license_service.check_license()["authorized"] is True
    current_code[0] = second_code
    copied = license_service.check_license()

    assert copied["authorized"] is False
    assert copied["reason"] == "LICENSE_SERVER_UNREACHABLE"


def test_expired_lease_is_rejected_while_offline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _prepare_database(tmp_path, monkeypatch)
    device_code = "AI-CUS-AAAAAAAA-BBBBBBBB-CCCCCCCC-DDDDDDDD"
    monkeypatch.setattr(device_identity, "get_device_code", lambda: device_code)
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    monkeypatch.setattr(license_service.product_config, "license_public_key_pem", lambda: public_key)
    now = datetime.now(timezone.utc)
    lease_text = json.dumps({
        "version": 1,
        "licenseId": "license-expired",
        "deviceId": device_code,
        "entitlements": ["lead"],
        "issuedAt": (now - timedelta(hours=73)).isoformat(),
        "expiresAt": (now - timedelta(hours=1)).isoformat(),
    }, separators=(",", ":"))
    signature = base64.b64encode(private_key.sign(lease_text.encode("utf-8"))).decode("ascii")
    with database.connect() as conn:
        database.set_setting(conn, "license_code", "LIC-EXPIRED")
        database.set_setting(conn, "device_code", device_code)
        database.set_setting(conn, "license_lease_text", lease_text)
        database.set_setting(conn, "license_lease_signature", signature)
    monkeypatch.setattr(
        license_service,
        "_request_license",
        lambda *_: (_ for _ in ()).throw(httpx.ConnectError("offline")),
    )

    result = license_service.check_license()

    assert result["authorized"] is False
    assert result["reason"] == "LICENSE_SERVER_UNREACHABLE"


def test_startup_checks_license_before_workers(monkeypatch: pytest.MonkeyPatch) -> None:
    from app import main

    events: list[str] = []
    monkeypatch.setattr(main.database, "init_db", lambda: events.append("database"))
    monkeypatch.setattr(main.license_service, "check_license", lambda: events.append("license"))
    monkeypatch.setattr(main.job_queue, "start", lambda: events.append("queue:start"))
    monkeypatch.setattr(main.automation_workbench, "start_scheduler", lambda: events.append("scheduler:start"))
    monkeypatch.setattr(main.automation_workbench, "stop_scheduler", lambda: events.append("scheduler:stop"))
    monkeypatch.setattr(main.job_queue, "shutdown", lambda: events.append("queue:stop"))
    monkeypatch.setattr(main.profile_manager, "shutdown", lambda: events.append("profile:stop"))

    async def run() -> None:
        async with main.lifespan(main.app):
            events.append("ready")

    asyncio.run(run())

    assert events == [
        "database", "license", "queue:start", "scheduler:start", "ready",
        "scheduler:stop", "queue:stop", "profile:stop",
    ]
