from __future__ import annotations

import ast
from pathlib import Path


SERVICES = Path(__file__).resolve().parents[1] / "app" / "services"


def service_imports(module: str) -> set[str]:
    tree = ast.parse((SERVICES / f"{module}.py").read_text(encoding="utf-8"))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or not node.module:
            continue
        if node.module == "app.services":
            imports.update(alias.name for alias in node.names)
        elif node.module.startswith("app.services."):
            imports.add(node.module.rsplit(".", 1)[-1])
    return imports


def test_queue_and_account_center_keep_one_way_service_dependencies() -> None:
    # 队列仅依赖浏览器资源管理；业务处理器只能由启动层注入。
    assert service_imports("job_queue") == {"profile_manager"}
    assert service_imports("account_center") == set()
    assert "content_workbench" not in service_imports("content_publish")


def test_service_dependency_graph_has_no_cycles() -> None:
    modules = {path.stem for path in SERVICES.glob("*.py") if path.stem != "__init__"}
    graph = {module: service_imports(module) & modules for module in modules}
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(module: str) -> None:
        if module in visiting:
            raise AssertionError(f"检测到服务循环依赖：{module}")
        if module in visited:
            return
        visiting.add(module)
        for dependency in graph[module]:
            visit(dependency)
        visiting.remove(module)
        visited.add(module)

    for module in modules:
        visit(module)
