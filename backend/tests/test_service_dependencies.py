from __future__ import annotations

import ast
from pathlib import Path


SERVICES = Path(__file__).resolve().parents[1] / "app" / "services"


def service_imports(module: str) -> set[str]:
    tree = ast.parse((SERVICES / f"{module}.py").read_text(encoding="utf-8"))
    return {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "app.services"
        for alias in node.names
    }


def test_queue_and_account_center_keep_one_way_service_dependencies() -> None:
    # 队列仅依赖浏览器资源管理；业务处理器只能由启动层注入。
    assert service_imports("job_queue") == {"profile_manager"}
    assert service_imports("account_center") == set()
