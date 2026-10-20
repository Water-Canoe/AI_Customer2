from __future__ import annotations

import json

import pytest

from app import product_config
from app.services import desktop_update, license_service


@pytest.mark.parametrize("override", [None, "", "   "])
def test_default_service_is_production_and_development_has_its_own_address(monkeypatch, override):
    # 未指定覆盖时，交付程序与发布工具都必须使用正式地址。
    if override is None:
        monkeypatch.delenv("MINI_BACKEND_URL", raising=False)
    else:
        monkeypatch.setenv("MINI_BACKEND_URL", override)
    assert product_config.DEFAULT_SERVICE_URL == "https://omgyspevxzji.sealosbja.site"
    assert product_config.DEVELOPMENT_SERVICE_URL == "https://puxaxxvlzefd.sealosbja.site"
    assert product_config.license_endpoint() == "https://omgyspevxzji.sealosbja.site/api/v1/products/ai-customer"


@pytest.mark.parametrize("root", [None, "https://puxaxxvlzefd.sealosbja.site/", "https://test-backend.invalid/"])
@pytest.mark.parametrize("action", ["activate", "renew"])
def test_license_requests_share_product_endpoint(monkeypatch, root, action):
    # 替换 HTTP 传输，验证真实客户端 URL 和字段，不访问公网或激活设备。
    if root is None:
        monkeypatch.delenv("MINI_BACKEND_URL", raising=False)
    else:
        monkeypatch.setenv("MINI_BACKEND_URL", root)
    captured = {}

    class Client:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def post(self, url, **kwargs):
            captured.update(url=url, **kwargs)
            return self

        def json(self):
            return {"code": 200, "data": {}}

    monkeypatch.setattr(license_service.httpx, "Client", Client)
    license_service._request_license(action, "TEST-LICENSE", "AI-CUS-TEST-DEVICE")
    base = (root or product_config.DEFAULT_SERVICE_URL).rstrip("/")
    assert captured["url"] == f"{base}/api/v1/products/ai-customer/license/{action}"
    assert captured["json"]["licenseCode"] == "TEST-LICENSE"
    assert captured["json"]["deviceId"] == "AI-CUS-TEST-DEVICE"


@pytest.mark.parametrize("root", [None, "https://puxaxxvlzefd.sealosbja.site/", "https://test-backend.invalid/"])
def test_desktop_updater_uses_same_product_endpoint(monkeypatch, root):
    if root is None:
        monkeypatch.delenv("MINI_BACKEND_URL", raising=False)
    else:
        monkeypatch.setenv("MINI_BACKEND_URL", root)
    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self):
            return b'{"code":200,"data":{"available":false}}'

    def open_request(request, **kwargs):
        captured.update(url=request.full_url, body=json.loads(request.data))
        return Response()

    monkeypatch.setattr(desktop_update.urllib.request, "urlopen", open_request)
    assert desktop_update._request_update_offer(("TEST-LICENSE", "AI-CUS-TEST-DEVICE"), "1.0.0") == {"available": False}
    base = (root or product_config.DEFAULT_SERVICE_URL).rstrip("/")
    assert captured["url"] == f"{base}/api/v1/products/ai-customer/update/check"
    assert captured["body"]["deviceId"] == "AI-CUS-TEST-DEVICE"
