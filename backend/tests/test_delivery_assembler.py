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
    _file(packaged / "frontend_dist" / "index.html")
    _file(packaged / "app" / "resource.txt")
    _file(packaged / "python311.dll")
    _file(packaged / "playwright" / "driver" / "node.exe")
    _file(packaged / "library.dll")

    crawler = tmp_path / "crawler-component"
    _file(crawler / "MyCrawler.exe")
    _file(crawler / "LICENSE")
    _file(crawler / "database" / "sqlite_tables.db", b"generated")
    _file(crawler / "playwright" / "driver" / "node.exe")
    _file(crawler / "wordcloud" / "stopwords")
    (crawler / "component-info.json").write_text(
        json.dumps(
            {
                "format": 1,
                "product": "AI Customer Component",
                "component": "mycrawler",
                "version": "1.0.0",
                "entrypoint": "MyCrawler.exe",
                "compiler": "nuitka",
            }
        ),
        encoding="utf-8",
    )
    cloak = tmp_path / "cloak"
    _file(cloak / "chrome.exe")
    component = tmp_path / "component"
    _file(component / "VoxCPM_Runtime.exe")
    native_module = "r/ai_customer_voxcpm_native.cp311-win_amd64.pyd"
    _file(component / native_module)
    (component / "component-info.json").write_text(
        json.dumps(
            {
                "format": 1,
                "product": "AI Customer Component",
                "component": "voxcpm2",
                "version": "1.0.0",
                "entrypoint": "VoxCPM_Runtime.exe",
                "compiler": "nuitka",
                "runtime_packager": "pyinstaller",
                "native_module": native_module,
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
        crawler_component_root=crawler,
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
    assert "runtime/application/AI_Customer_App.exe" in program_names
    assert "runtime/application/app/resource.txt" in program_names
    assert "runtime/frontend_dist/index.html" in program_names
    assert "runtime/application/library.dll" not in program_names
    assert manifest["environment_version"] == "1.0.0"
    assert manifest["entrypoint"] == "runtime/application/AI_Customer_App.exe"
    assert "AI_Customer.exe" not in {item["path"] for item in manifest["files"]}
    assert "runtime/application/library.dll" in environment_names
    assert "runtime/application/python311.dll" in environment_names
    assert "runtime/MyCrawler/MyCrawler.exe" in environment_names
    assert "runtime/MyCrawler/wordcloud/stopwords" in environment_names
    assert "runtime/MyCrawler/database/sqlite_tables.db" not in environment_names
    assert not any(name.startswith("runtime/MyCrawler/") and name.endswith(".py") for name in environment_names)
    assert "runtime/components/voxcpm2/versions/1.0.0/" + native_module in environment_names
    assert "runtime/frontend_dist/index.html" not in environment_names

    verified = _verifier_module().verify(str(program_zip), "1.2.3")
    assert verified["version"] == "1.2.3"
    assert verified["environment_version"] == "1.0.0"


def test_program_only_delivery_skips_environment_sources(tmp_path: Path) -> None:
    assembler = _module()
    packaged = tmp_path / "packaged"
    _file(packaged / "AI_Customer_App.exe")
    _file(packaged / "frontend_dist" / "index.html")
    _file(packaged / "app" / "resource.txt")

    program_zip, environment_zip = assembler.assemble(
        packaged_app=packaged,
        stable_launcher=_file(tmp_path / "AI_Customer.exe"),
        staging_root=tmp_path / "staging",
        delivery_root=tmp_path / "deliverables" / "1.2.4",
        version="1.2.4",
        environment_version="1.0.0",
        schema_version=7,
        crawler_component_root=None,
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
