from __future__ import annotations

import sys
from pathlib import Path

import pytest


BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))


def _walk_routes(routes: list[object]):
    # FastAPI 0.139 keeps included routers nested instead of copying their routes.
    for route in routes:
        nested_router = getattr(route, "original_router", None)
        if nested_router is not None:
            yield from _walk_routes(nested_router.routes)
            continue
        yield route


def test_api_routes_have_unique_method_paths_and_business_owners() -> None:
    from app.main import app

    routes: dict[tuple[str, str], object] = {}
    for route in _walk_routes(app.routes):
        for method in getattr(route, "methods", set()):
            key = (method, route.path)
            if route.path.startswith("/api/"):
                assert key not in routes, f"重复接口：{method} {route.path}"
                routes[key] = route

    expected_owners = {
        ("POST", "/api/tasks"): "app.routers.tasks",
        ("GET", "/api/overview/tree"): "app.routers.overview",
        ("GET", "/api/overview/children"): "app.routers.overview",
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


def test_frontend_static_files_cannot_escape_dist_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app import main

    dist = tmp_path / "frontend_dist"
    sibling = tmp_path / "frontend_dist_private"
    dist.mkdir()
    sibling.mkdir()
    (dist / "index.html").write_text("index", encoding="utf-8")
    (sibling / "secret.txt").write_text("secret", encoding="utf-8")
    monkeypatch.setattr(main, "_frontend_dist", lambda: dist)

    response = main.serve_frontend("../frontend_dist_private/secret.txt")

    assert Path(response.path).resolve() == (dist / "index.html").resolve()
