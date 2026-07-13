from __future__ import annotations

import sys
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))


def test_api_routes_have_unique_method_paths_and_business_owners() -> None:
    from app.main import app

    routes: dict[tuple[str, str], object] = {}
    for route in app.routes:
        for method in getattr(route, "methods", set()):
            key = (method, route.path)
            if route.path.startswith("/api/"):
                assert key not in routes, f"重复接口：{method} {route.path}"
                routes[key] = route

    expected_owners = {
        ("POST", "/api/tasks"): "app.routers.tasks",
        ("GET", "/api/overview/tree"): "app.routers.overview",
        ("POST", "/api/message-workbench/auto-message-batches"): "app.routers.message",
        ("POST", "/api/ai/jobs"): "app.routers.ai",
        ("GET", "/api/runtime/jobs"): "app.routers.runtime",
        ("POST", "/api/traffic/plans"): "app.routers.traffic",
        ("GET", "/api/automation/plans"): "app.routers.automation",
        ("GET", "/api/system/backups"): "app.routers.system",
    }
    for key, module in expected_owners.items():
        assert key in routes
        assert routes[key].endpoint.__module__ == module
