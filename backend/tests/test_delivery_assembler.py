from __future__ import annotations

import importlib.util
import json
import zipfile
from pathlib import Path

import pytest


WORKSPACE_ROOT = Path(__file__).resolve().parents[2]


def _module():
    module_path = WORKSPACE_ROOT / "script" / "internal" / "assemble_delivery.py"
    spec = importlib.util.spec_from_file_location("assemble_delivery", module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _verifier_module():
    module_path = WORKSPACE_ROOT / "script" / "internal" / "verify_program_zip.py"
    spec = importlib.util.spec_from_file_location("verify_program_zip", module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _file(path: Path, content: bytes = b"test") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def test_core_install_and_program_update_own_complete_application(tmp_path: Path) -> None:
    assembler = _module()
    packaged = tmp_path / "packaged"
    _file(packaged / "AI_Customer_App.exe")
    _file(packaged / "frontend_dist" / "index.html")
    _file(packaged / "app" / "resource.txt")
    _file(packaged / "python311.dll")
    _file(packaged / "playwright" / "driver" / "node.exe")
    _file(packaged / "curl_cffi" / "_wrapper.pyd", b"private-message-native")
    _file(packaged / "library.dll")

    crawler = tmp_path / "crawler-component"
    _file(crawler / "MyCrawler.exe")
    _file(crawler / "LICENSE")
    _file(crawler / "database" / "sqlite_tables.db", b"generated")
    _file(crawler / "playwright" / "driver" / "node.exe")
    _file(crawler / "wordcloud" / "stopwords")
    crawler_hash = assembler._hash(crawler / "MyCrawler.exe")
    (crawler / "component-info.json").write_text(
        json.dumps(
            {
                "format": 1,
                "product": "AI Customer Component",
                "component": "mycrawler",
                "version": "1.0.0",
                "entrypoint": "MyCrawler.exe",
                "compiler": "nuitka",
                "source_revision": "a" * 40,
                "source_dirty": False,
                "entrypoint_sha256": crawler_hash,
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
    component_hash = assembler._hash(component / "VoxCPM_Runtime.exe")
    native_hash = assembler._hash(component / native_module)
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
                "source_revision": "b" * 40,
                "source_dirty": False,
                "entrypoint_sha256": component_hash,
                "native_module_sha256": native_hash,
            }
        ),
        encoding="utf-8",
    )
    models = tmp_path / "voice_models"
    _file(models / "VoxCPM2" / "model.bin")
    stable = _file(tmp_path / "AI_Customer.exe")
    readme = _file(tmp_path / "PACKAGE_README.txt")

    program_zip, core_zip, voice_zip = assembler.assemble(
        packaged_app=packaged,
        stable_launcher=stable,
        staging_root=tmp_path / "staging",
        delivery_root=tmp_path / "deliverables" / "1.2.3",
        version="1.2.3",
        environment_version="1.1.0",
        schema_version=7,
        source_revision="c" * 40,
        source_dirty=False,
        crawler_component_root=crawler,
        cloakbrowser_root=cloak,
        vox_component_root=None,
        voice_models_root=None,
        readme=readme,
    )

    with zipfile.ZipFile(program_zip) as archive:
        program_names = set(archive.namelist())
        manifest = json.loads(archive.read("release-manifest.json"))
    assert core_zip is not None
    assert voice_zip is None
    # Windows 默认以压缩包名建立解压目录；安装名固定，版本由父目录区分。
    assert core_zip == tmp_path / "deliverables" / "1.2.3" / "AI_Customer.zip"
    with zipfile.ZipFile(core_zip) as archive:
        core_names = set(archive.namelist())
        environment_manifest = json.loads(archive.read("runtime/environment-manifest.json"))
        core_program_manifest = json.loads(archive.read("release-manifest.json"))

    assert "AI_Customer.exe" in program_names
    assert "runtime/application/AI_Customer_App.exe" in program_names
    assert "runtime/application/app/resource.txt" in program_names
    assert "runtime/frontend_dist/index.html" in program_names
    assert "runtime/application/library.dll" in program_names
    assert "runtime/application/python311.dll" in program_names
    assert "runtime/application/playwright/driver/node.exe" in program_names
    assert "runtime/application/curl_cffi/_wrapper.pyd" in program_names
    assert manifest["environment_version"] == "1.1.0"
    assert manifest["updater_version"] == "1.1.0"
    assert manifest["source_revision"] == "c" * 40
    assert manifest["source_dirty"] is False
    assert manifest["built_at"]
    assert manifest["entrypoint"] == "runtime/application/AI_Customer_App.exe"
    declared_program = {item["path"] for item in manifest["files"]}
    assert "AI_Customer.exe" in declared_program
    assert core_program_manifest == manifest
    assert "runtime/MyCrawler/MyCrawler.exe" in core_names
    assert "runtime/MyCrawler/wordcloud/stopwords" in core_names
    assert "runtime/MyCrawler/database/sqlite_tables.db" not in core_names
    assert not any(name.startswith("runtime/MyCrawler/") and name.endswith(".py") for name in core_names)
    assert not any(name.startswith(("runtime/components/", "runtime/models/")) for name in core_names)
    assert not any(name.startswith(("application/", "components/", "models/")) for name in environment_manifest["required_paths"])
    assert "runtime/environment-manifest.json" not in declared_program
    assert not any(name.startswith(("runtime/MyCrawler/", "runtime/cloakbrowser_browser/")) for name in declared_program)

    verified = _verifier_module().verify(str(program_zip), "1.2.3")
    assert verified["version"] == "1.2.3"
    assert verified["environment_version"] == "1.1.0"
    assert verified["source_revision"] == "c" * 40
    assert verified["source_dirty"] is False

    # 语音是独立安装包，选择后也不进入 Core 或日常更新的归属清单。
    _, voice_core_zip, voice_zip = assembler.assemble(
        packaged_app=packaged,
        stable_launcher=stable,
        staging_root=tmp_path / "voice-staging",
        delivery_root=tmp_path / "deliverables" / "1.2.4",
        version="1.2.4",
        environment_version="1.1.0",
        schema_version=7,
        source_revision="c" * 40,
        source_dirty=True,
        crawler_component_root=crawler,
        cloakbrowser_root=cloak,
        vox_component_root=component,
        voice_models_root=models,
        readme=readme,
        include_voice=True,
    )
    assert voice_zip is not None
    assert voice_core_zip == tmp_path / "deliverables" / "1.2.4" / core_zip.name
    with zipfile.ZipFile(voice_zip) as archive:
        assert "runtime/components/voxcpm2/versions/1.0.0/" + native_module in archive.namelist()
        assert "runtime/models/VoxCPM2/model.bin" in archive.namelist()
        assert "runtime/components/voxcpm2/current.json" in archive.namelist()
        assert "runtime/environment-manifest.json" not in archive.namelist()
    with zipfile.ZipFile(voice_core_zip) as archive:
        assert not any(name.startswith(("runtime/components/", "runtime/models/")) for name in archive.namelist())


def test_program_only_delivery_skips_environment_sources(tmp_path: Path) -> None:
    assembler = _module()
    packaged = tmp_path / "packaged"
    _file(packaged / "AI_Customer_App.exe")
    _file(packaged / "frontend_dist" / "index.html")
    _file(packaged / "app" / "resource.txt")
    _file(packaged / "python311.dll")
    _file(packaged / "playwright" / "driver" / "node.exe")
    _file(packaged / "curl_cffi" / "_wrapper.pyd")

    program_zip, core_zip, voice_zip = assembler.assemble(
        packaged_app=packaged,
        stable_launcher=_file(tmp_path / "AI_Customer.exe"),
        staging_root=tmp_path / "staging",
        delivery_root=tmp_path / "deliverables" / "1.2.4",
        version="1.2.4",
        environment_version="1.1.0",
        schema_version=7,
        source_revision="c" * 40,
        source_dirty=True,
        crawler_component_root=None,
        cloakbrowser_root=None,
        vox_component_root=None,
        voice_models_root=None,
        readme=_file(tmp_path / "PACKAGE_README.txt"),
        program_only=True,
    )

    assert core_zip is None
    assert voice_zip is None
    assert program_zip.is_file()
    assert not any(path.name.startswith("AI_Customer_Environment_") for path in program_zip.parent.iterdir())
    verified = _verifier_module().verify(str(program_zip), "1.2.4")
    assert verified["environment_version"] == "1.1.0"
    assert verified["source_dirty"] is True
    with zipfile.ZipFile(program_zip) as archive:
        assert "runtime/application/python311.dll" in archive.namelist()
        assert "runtime/application/curl_cffi/_wrapper.pyd" in archive.namelist()
        assert not any(name.startswith(("runtime/MyCrawler/", "runtime/components/", "runtime/models/", "runtime/cloakbrowser_browser/")) for name in archive.namelist())

    # 入口现在属于发布清单，内容被改动时也必须拒绝发布。
    tampered = tmp_path / "tampered-launcher.zip"
    with zipfile.ZipFile(program_zip) as original, zipfile.ZipFile(tampered, "x") as archive:
        for entry in original.infolist():
            archive.writestr(entry, b"evil" if entry.filename == "AI_Customer.exe" else original.read(entry))
    with pytest.raises(RuntimeError, match="文件校验失败：AI_Customer.exe"):
        _verifier_module().verify(str(tampered), "1.2.4")

    # 编译运行库纳入白名单，环境/数据仍然严格拒绝混入更新包。
    with zipfile.ZipFile(program_zip, "a") as archive:
        archive.writestr("runtime/MyCrawler/unexpected.dll", b"unsafe environment overwrite")
    with pytest.raises(RuntimeError, match="清单不一致"):
        _verifier_module().verify(str(program_zip), "1.2.4")


def test_component_provenance_rejects_modified_artifact(tmp_path: Path) -> None:
    assembler = _module()
    executable = _file(tmp_path / "component" / "MyCrawler.exe", b"original")
    info = {
        "source_revision": "a" * 40,
        "source_dirty": False,
        "entrypoint_sha256": assembler._hash(executable),
    }

    assert assembler._valid_component_provenance(executable.parent, info, {"entrypoint_sha256": Path(executable.name)})
    executable.write_bytes(b"modified")
    assert not assembler._valid_component_provenance(executable.parent, info, {"entrypoint_sha256": Path(executable.name)})


def test_incomplete_native_runtime_cannot_be_released(tmp_path: Path) -> None:
    assembler = _module()
    packaged = tmp_path / "packaged"
    _file(packaged / "AI_Customer_App.exe")
    _file(packaged / "frontend_dist" / "index.html")
    _file(packaged / "app" / "resource.txt")
    _file(packaged / "python311.dll")
    _file(packaged / "playwright" / "driver" / "node.exe")
    with pytest.raises(RuntimeError, match="curl_cffi/_wrapper.pyd"):
        assembler.assemble(
            packaged_app=packaged,
            stable_launcher=_file(tmp_path / "AI_Customer.exe"),
            staging_root=tmp_path / "stage",
            delivery_root=tmp_path / "delivery",
            version="1.2.3",
            environment_version="1.1.0",
            schema_version=16,
            source_revision="a" * 40,
            source_dirty=False,
            crawler_component_root=None,
            cloakbrowser_root=None,
            vox_component_root=None,
            voice_models_root=None,
            readme=_file(tmp_path / "README.txt"),
            program_only=True,
        )


@pytest.mark.parametrize("filename", ["NUL", "a.dll.", "a?b.dll"])
def test_program_verifier_rejects_windows_unsafe_names(tmp_path: Path, filename: str) -> None:
    archive_path = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive_path, "x") as archive:
        archive.writestr("runtime/application/" + filename, b"unsafe")
    with pytest.raises(RuntimeError, match="不安全"):
        _verifier_module().verify(str(archive_path))


def test_program_verifier_rejects_case_aliases_in_zip_and_manifest(tmp_path: Path) -> None:
    assembler = _module()
    archive_path = tmp_path / "aliases.zip"
    with zipfile.ZipFile(archive_path, "x") as archive:
        archive.writestr("runtime/application/a.dll", b"test")
        archive.writestr("runtime/application/A.dll", b"test")
    with pytest.raises(RuntimeError, match="重复"):
        _verifier_module().verify(str(archive_path))

    program = tmp_path / "program"
    _file(program / "runtime" / "application" / "a.dll")
    manifest = assembler._program_manifest(program, "1.2.3", "1.1.0", 16, "a" * 40, False)
    manifest["files"].append({**manifest["files"][0], "path": "runtime/application/A.dll"})
    manifest_archive = tmp_path / "manifest-aliases.zip"
    with zipfile.ZipFile(manifest_archive, "x") as archive:
        archive.writestr("runtime/application/a.dll", b"test")
        archive.writestr("release-manifest.json", json.dumps(manifest))
    with pytest.raises(RuntimeError, match="非程序文件"):
        _verifier_module().verify(str(manifest_archive))
