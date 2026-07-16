from __future__ import annotations

import base64
import hashlib
import json
import zipfile

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def test_component_offer_requires_valid_signed_manifest(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import voice_runtime

    private_key = Ed25519PrivateKey.generate()
    public_pem = private_key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    monkeypatch.setattr(voice_runtime, "TRUSTED_PUBLIC_KEY_PEM", public_pem)
    manifest = {
        "format": 1,
        "product": "AI Customer Component",
        "component": "voxcpm2",
        "version": "1.0.0",
        "platform": "windows",
        "arch": "x64",
        "min_app_version": "1.2.1",
        "package": {"size": 10, "sha256": "a" * 64},
    }
    manifest_text = json.dumps(manifest, ensure_ascii=False, separators=(",", ":"))
    signature = base64.b64encode(private_key.sign(manifest_text.encode("utf-8"))).decode("ascii")

    result = voice_runtime._validate_offer({
        "available": True,
        "downloadAllowed": True,
        "version": "1.0.0",
        "manifestText": manifest_text,
        "signature": signature,
        "downloadUrl": "https://example.com/component.zip",
    })

    assert result["version"] == "1.0.0"
    with pytest.raises(RuntimeError, match="签名校验失败"):
        voice_runtime._validate_offer({
            "available": True,
            "downloadAllowed": True,
            "version": "1.0.0",
            "manifestText": manifest_text + " ",
            "signature": signature,
            "downloadUrl": "https://example.com/component.zip",
        })


def test_component_archive_extracts_and_activates(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import voice_runtime

    monkeypatch.setenv("AI_CUSTOMER_RUNTIME_DIR", str(tmp_path / "runtimes"))
    archive = tmp_path / "component.zip"
    info = {
        "format": 1,
        "product": "AI Customer Component",
        "component": "voxcpm2",
        "version": "1.0.0",
        "entrypoint": "VoxCPM_Runtime.exe",
    }
    with zipfile.ZipFile(archive, "w") as target:
        target.writestr("component-info.json", json.dumps(info))
        target.writestr("VoxCPM_Runtime.exe", b"runtime")
        target.writestr("r/library.dll", b"library")

    installed = voice_runtime._extract(archive, "1.0.0", lambda: False)
    voice_runtime._activate("1.0.0", installed)

    assert voice_runtime.status()["installed"] is True
    assert voice_runtime.worker_executable() == installed / "VoxCPM_Runtime.exe"


def test_component_download_resumes_partial_file(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import voice_runtime

    payload = b"1234567890"
    monkeypatch.setenv("AI_CUSTOMER_RUNTIME_DIR", str(tmp_path / "runtimes"))
    downloads = voice_runtime.component_root() / "downloads"
    downloads.mkdir(parents=True)
    (downloads / "1.0.0.zip.part").write_bytes(payload[:4])
    captured: dict[str, object] = {}

    class FakeResponse:
        status_code = 206
        headers = {"Content-Range": "bytes 4-9/10"}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def iter_bytes(self, _size):
            yield payload[4:]

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def stream(self, _method, _url, headers):
            captured.update(headers)
            return FakeResponse()

    monkeypatch.setattr(voice_runtime.httpx, "Client", FakeClient)
    result = voice_runtime._download(
        {
            "version": "1.0.0",
            "size": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
            "download_url": "https://example.com/component.zip",
        },
        lambda _value: None,
        lambda: False,
    )

    assert captured["Range"] == "bytes=4-"
    assert result.read_bytes() == payload
