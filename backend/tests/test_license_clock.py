from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app import database, device_identity, license_clock
from app.services import license_service


@pytest.fixture
def licensing(tmp_path, monkeypatch):
    monkeypatch.setenv('AI_CUSTOMER_DB', str(tmp_path / 'license.sqlite3'))
    database.init_db()
    device = 'AI-CUS-AAAAAAAA-BBBBBBBB-CCCCCCCC-DDDDDDDD'
    monkeypatch.setattr(device_identity, 'get_device_code', lambda: device)
    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    monkeypatch.setattr(license_service.product_config, 'license_public_key_pem', lambda: public)
    clock = SimpleNamespace(wall=datetime(2026, 10, 9, tzinfo=timezone.utc), tick=0.0)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock.wall if tz else clock.wall.replace(tzinfo=None)

    monkeypatch.setattr(license_clock, '_clock', None)
    monkeypatch.setattr(license_clock, 'datetime', Clock)
    monkeypatch.setattr(license_service, 'datetime', Clock)
    monkeypatch.setattr(license_clock, 'monotonic', lambda: clock.tick)

    def offline(*args):
        raise httpx.ConnectError('isolated offline test')

    monkeypatch.setattr(license_service, '_request_license', offline)
    with database.connect() as conn:
        database.set_setting(conn, 'device_code', device)
        database.set_setting(conn, 'license_code', 'LIC-TEST')

    def remote(issued=None, *, product_id='ai-customer', entitlements=None):
        issued = issued or clock.wall
        payload = {'version': 1, 'licenseId': 'test', 'deviceId': device,
                   'entitlements': entitlements if entitlements is not None else ['lead', 'traffic', 'content'],
                   'issuedAt': issued.isoformat(), 'expiresAt': (issued + timedelta(hours=72)).isoformat()}
        if product_id is not None:
            payload['productId'] = product_id
        text = json.dumps(payload, separators=(',', ':'))
        return {'data': {'permission': True, 'leaseText': text,
                         'signature': base64.b64encode(key.sign(text.encode())).decode()}}

    return clock, remote, device


@pytest.mark.parametrize('product_id', [None, 'blind-watermark'])
def test_signed_lease_requires_current_product(licensing, product_id):
    # 即使签名有效，也拒绝别的产品或缺少产品身份的租约。
    _, remote, device = licensing
    with pytest.raises(ValueError, match='当前产品'):
        license_service._accept_remote(None, remote(product_id=product_id), device)
    assert not license_service.license_overview()['authorized']


@pytest.mark.parametrize('cached', [True, False])
def test_renewal_cannot_grant_removed_entitlement(licensing, monkeypatch, cached):
    # 续租返回 content 权益时，本次 lead 操作仍必须被拒绝。
    clock, remote, device = licensing
    if cached:
        license_service._accept_remote(None, remote(), device)
        clock.wall += timedelta(hours=7)
        clock.tick += 7 * 3600
    monkeypatch.setattr(license_service, '_request_license', lambda *_: remote(entitlements=['content']))
    with pytest.raises(ValueError, match='未包含'):
        license_service.ensure_authorized_for('lead')
    assert license_service.ensure_authorized_for('content')['authorized']


def test_normal_offline_lease_retains_72_hour_limit(licensing):
    clock, remote, device = licensing
    license_service._accept_remote(None, remote(), device)
    clock.wall += timedelta(hours=71)
    clock.tick += 71 * 3600
    assert license_service.ensure_authorized()['authorized']
    clock.wall += timedelta(hours=2)
    clock.tick += 2 * 3600
    assert not license_service.license_overview()['authorized']
    with pytest.raises(ValueError, match='过期'):
        license_service.ensure_authorized()


def test_rollback_survives_restart_and_replaying_database_cache(licensing, monkeypatch):
    clock, remote, device = licensing
    old = remote()
    license_service._accept_remote(None, old, device)
    start = clock.wall
    clock.wall += timedelta(hours=10)
    clock.tick += 10 * 3600
    assert license_service.ensure_authorized()['authorized']
    # 回放旧数据库字段并模拟重启；独立的时间记录不能随数据库回退。
    with database.connect() as conn:
        database.set_setting(conn, 'license_lease_text', old['data']['leaseText'])
        database.set_setting(conn, 'license_lease_signature', old['data']['signature'])
        database.set_setting(conn, 'license_last_status', 'authorized')
    monkeypatch.setattr(license_clock, '_clock', None)
    clock.wall, clock.tick = start + timedelta(hours=1), 0.0
    result = license_service.check_license()
    assert not result['authorized'] and result['reason'] == 'CLOCK_UNTRUSTED'
    with pytest.raises(ValueError, match='回拨'):
        license_service.ensure_authorized()


def test_monotonic_time_expires_lease_even_during_small_clock_drift(licensing):
    clock, remote, device = licensing
    license_service._accept_remote(None, remote(), device)
    clock.wall += timedelta(hours=71, minutes=58)
    clock.tick += 71 * 3600 + 58 * 60
    assert license_service.ensure_authorized()['authorized']
    clock.tick += 3 * 60
    assert not license_service.license_overview()['authorized']


@pytest.mark.parametrize('change', ['missing', 'damaged', 'wrong_device'])
def test_unusable_checkpoint_requires_online_confirmation(licensing, monkeypatch, change):
    clock, remote, device = licensing
    license_service._accept_remote(None, remote(), device)
    path = license_clock._clock.path
    if change == 'missing':
        path.unlink()
    elif change == 'damaged':
        path.write_bytes(b'tampered')
    else:
        device_identity._write_protected_secret(path, json.dumps({'deviceId': 'other', 'observedAt': clock.wall.isoformat()}).encode())
    monkeypatch.setattr(license_clock, '_clock', None)
    result = license_service.check_license()
    assert not result['authorized'] and result['reason'] == 'CLOCK_UNTRUSTED'
    with pytest.raises(ValueError, match='时间记录'):
        license_service.ensure_authorized()


def test_correcting_clock_and_receiving_fresh_signed_lease_restores_access(licensing, monkeypatch):
    clock, remote, device = licensing
    license_service._accept_remote(None, remote(), device)
    clock.tick += 600
    assert license_service.license_overview()['reason'] == 'CLOCK_UNTRUSTED'
    clock.wall += timedelta(seconds=600)
    # 仅改回系统时间不能自行解除异常；必须取得新的服务器签名租约。
    monkeypatch.setattr(license_clock, '_clock', None)
    assert not license_service.license_overview()['authorized']
    monkeypatch.setattr(license_service, '_request_license', lambda *_: remote())
    assert license_service.check_license()['authorized']
    assert license_service.ensure_authorized()['authorized']


def test_old_signed_server_response_cannot_reset_clock_baseline(licensing):
    clock, remote, device = licensing
    old = remote()
    license_service._accept_remote(None, old, device)
    clock.wall += timedelta(hours=10)
    clock.tick += 10 * 3600
    with pytest.raises(license_clock.ClockError, match='当前时间'):
        license_service._accept_remote(None, old, device)


def test_replaying_old_response_after_rollback_and_restart_is_rejected(licensing, monkeypatch):
    clock, remote, device = licensing
    start = clock.wall
    old = remote()
    license_service._accept_remote(None, old, device)
    clock.wall += timedelta(hours=10)
    clock.tick += 10 * 3600
    assert license_service.ensure_authorized()['authorized']
    monkeypatch.setattr(license_clock, '_clock', None)
    clock.wall, clock.tick = start, 0.0
    with pytest.raises(license_clock.ClockError, match='重放'):
        license_service._accept_remote(None, old, device)
    assert not license_service.license_overview()['authorized']


def test_lease_expiring_during_failed_renewal_does_not_allow_operation(licensing, monkeypatch):
    clock, remote, device = licensing
    license_service._accept_remote(None, remote(), device)
    clock.wall += timedelta(hours=71, minutes=59, seconds=50)
    clock.tick += 72 * 3600 - 10

    def timeout(*args):
        clock.wall += timedelta(seconds=15)
        clock.tick += 15
        raise httpx.ConnectError('renewal timed out')

    monkeypatch.setattr(license_service, '_request_license', timeout)
    with pytest.raises(ValueError, match='过期'):
        license_service.ensure_authorized()


def test_invalid_signature_does_not_create_trusted_time_record(licensing):
    _, remote, device = licensing
    forged = remote()
    forged['data']['leaseText'] = forged['data']['leaseText'].replace('"lead"', '"invalid"')
    with pytest.raises(ValueError, match='签名'):
        license_service._accept_remote(None, forged, device)
    assert not device_identity.identity_path().with_name('license_clock.bin').exists()


def test_checkpoint_write_failure_does_not_grant_access(licensing, monkeypatch):
    _, remote, device = licensing

    def denied(*args):
        raise PermissionError('test directory is read-only')

    monkeypatch.setattr(device_identity, '_write_protected_secret', denied)
    with pytest.raises(license_clock.ClockError, match='目录权限'):
        license_service._accept_remote(None, remote(), device)
    assert not license_service.license_overview()['authorized']


def test_future_server_time_reports_clock_error_instead_of_bad_signature(licensing, monkeypatch):
    clock, remote, _ = licensing
    response = remote(clock.wall + timedelta(hours=1))
    monkeypatch.setattr(license_service, '_request_license', lambda *_: response)
    result = license_service.check_license()
    assert not result['authorized'] and result['reason'] == 'CLOCK_UNTRUSTED'
    assert '校准' in result['message']
