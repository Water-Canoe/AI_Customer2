from __future__ import annotations

import os


PRODUCT_ID = "ai-customer"
DEFAULT_SERVICE_URL = "https://puxaxxvlzefd.sealosbja.site"
_LICENSE_PUBLIC_KEY_PEM = b"""-----BEGIN PUBLIC KEY-----
MCowBQYDK2VwAyEAb+P18TfnmdEjCEaxr/rf4kZBK3DsJaGHXKJd6pS5aUQ=
-----END PUBLIC KEY-----
"""


def license_endpoint() -> str:
    """授权、更新和组件发布共用同一产品入口。"""
    root = os.getenv("MINI_BACKEND_URL", DEFAULT_SERVICE_URL).strip().rstrip("/")
    return f"{root}/api/v1/products/{PRODUCT_ID}"


def license_public_key_pem() -> bytes:
    """Return the embedded public key used to verify signed license leases."""
    return _LICENSE_PUBLIC_KEY_PEM
