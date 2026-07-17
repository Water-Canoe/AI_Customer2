from __future__ import annotations

import importlib.util
import json
import zipfile
from pathlib import Path


WORKSPACE_ROOT = Path(__file__).resolve().parents[2]


def _module():
    module_path = WORKSPACE_ROOT / "script" / "assemble_delivery.py"
    spec = importlib.util.spec_from_file_location("assemble_delivery", module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _verifier_module():
    module_path = WORKSPACE_ROOT / "script" / "verify_program_zip.py"
    spec = importlib.util.spec_from_file_location("verify_program_zip", module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _file(path: Path, content: bytes = b"test") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def test_two_zip_delivery_separates_program_from_reusable_environment(tmp_path: Path) -> None:
    assembler = _module()
    packaged = tmp_path / "packaged"
    _file(packaged / "AI_Customer_App.exe")
    _file(packaged / "runtime" / "frontend_dist" / "index.html")
    _file(packaged / "runtime" / "app" / "resource.txt")
    _file(packaged / "runtime" / "python311.dll")
    _file(packaged / "runtime" / "playwright" / "driver" / "node.exe")
    _file(packaged / "runtime" / "library.zip")

    crawler = tmp_path / "MyCrawler"
    _file(crawler / "main.py")
    _file(crawler / "LICENSE")
    _file(crawler / "database" / "sqlite_tables.db", b"generated")
    _file(crawler / ".venv" / "Lib" / "site-packages" / "playwright" / "driver" / "node.exe")
    python_root = tmp_path / "python"
    _file(python_root / "python.exe")
    cloak = tmp_path / "cloak"
    _file(cloak / "chrome.exe")
    component = tmp_path / "component"
    _file(component / "VoxCPM_Runtime.exe")
    (component / "component-info.json").write_text(
        json.dumps(
            {
                "format": 1,
                "product": "AI Customer Component",
                "component": "voxcpm2",
                "version": "1.0.0",
                "entrypoint": "VoxCPM_Runtime.exe",
            }
        ),
        encoding="utf-8",
    )
    models = tmp_path / "voice_models"
    _file(models / "VoxCPM2" / "model.bin")
    stable = _file(tmp_path / "AI_Customer.exe")
    readme = _file(tmp_path / "PACKAGE_README.txt")

    program_zip, environment_zip = assembler.assemble(
        packaged_app=packaged,
        stable_launcher=stable,
        staging_root=tmp_path / "staging",
        delivery_root=tmp_path / "deliverables" / "1.2.3",
        version="1.2.3",
        environment_version="1.0.0",
        schema_version=7,
        crawler_python_root=python_root,
        crawler_root=crawler,
        cloakbrowser_root=cloak,
        vox_component_root=component,
        voice_models_root=models,
        readme=readme,
    )

    with zipfile.ZipFile(program_zip) as archive:
        program_names = set(archive.namelist())
        manifest = json.loads(archive.read("release-manifest.json"))
    with zipfile.ZipFile(environment_zip) as archive:
        environment_names = set(archive.namelist())

    assert "AI_Customer.exe" in program_names
    assert "AI_Customer_App.exe" in program_names
    assert "runtime/frontend_dist/index.html" in program_names
    assert "runtime/library.zip" not in program_names
    assert manifest["environment_version"] == "1.0.0"
    assert manifest["entrypoint"] == "AI_Customer_App.exe"
    assert "AI_Customer.exe" not in {item["path"] for item in manifest["files"]}
    assert "runtime/library.zip" in environment_names
    assert "runtime/python/python.exe" in environment_names
    assert "runtime/MyCrawler/main.py" in environment_names
    assert "runtime/MyCrawler/database/sqlite_tables.db" not in environment_names
    assert "runtime/frontend_dist/index.html" not in environment_names

    verified = _verifier_module().verify(str(program_zip), "1.2.3")
    assert verified["version"] == "1.2.3"
    assert verified["environment_version"] == "1.0.0"


def test_program_only_delivery_skips_environment_sources(tmp_path: Path) -> None:
    assembler = _module()
    packaged = tmp_path / "packaged"
    _file(packaged / "AI_Customer_App.exe")
    _file(packaged / "runtime" / "frontend_dist" / "index.html")
    _file(packaged / "runtime" / "app" / "resource.txt")

    program_zip, environment_zip = assembler.assemble(
        packaged_app=packaged,
        stable_launcher=_file(tmp_path / "AI_Customer.exe"),
        staging_root=tmp_path / "staging",
        delivery_root=tmp_path / "deliverables" / "1.2.4",
        version="1.2.4",
        environment_version="1.0.0",
        schema_version=7,
        crawler_python_root=None,
        crawler_root=None,
        cloakbrowser_root=None,
        vox_component_root=None,
        voice_models_root=None,
        readme=_file(tmp_path / "PACKAGE_README.txt"),
        program_only=True,
    )

    assert environment_zip is None
    assert program_zip.is_file()
    assert not any(path.name.startswith("AI_Customer_Environment_") for path in program_zip.parent.iterdir())
    assert _verifier_module().verify(str(program_zip), "1.2.4")["environment_version"] == "1.0.0"
