from __future__ import annotations

import os


_LICENSE_ENDPOINT_BYTES = (
    41, 61, 89, 51, 6, 73, 91, 64, 25, 3, 5, 92, 55, 3, 18, 14, 19, 13, 20, 43,
    103, 94, 38, 20, 31, 27, 28, 15, 15, 19, 3, 55, 12, 7, 14, 91, 14, 25, 108, 42,
    88, 48, 1, 28, 25, 10, 31,
)
_LICENSE_ENDPOINT_KEY = b"AI-Customer-Desktop"


def license_endpoint() -> str:
    override = os.getenv("AI_CUSTOMER_LICENSE_ENDPOINT", "").strip()
    if override:
        return override.rstrip("/")
    decoded = bytes(
        value ^ _LICENSE_ENDPOINT_KEY[index % len(_LICENSE_ENDPOINT_KEY)]
        for index, value in enumerate(_LICENSE_ENDPOINT_BYTES)
    )
    return decoded.decode("utf-8").rstrip("/")
