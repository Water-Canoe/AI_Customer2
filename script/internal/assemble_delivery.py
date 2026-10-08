from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import re
import shutil
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

# 组装和安装拒绝相同的危险文件名，避免发布后才发现无法解压。
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
from app.release_paths import safe_release_path


APPLICATION_ENTRYPOINT = "runtime/application/AI_Customer_App.exe"
UPDATER_VERSION = "1.1.0"
VERSION_PATTERN = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$")
REVISION_PATTERN = re.compile(r"^[0-9a-f]{40,64}$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
CRAWLER_IGNORED_DIRS = {
    ".git",
    ".venv",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "__pycache__",
    "browser_data",
    "docs",
    "node_modules",
    "test",
    "tests",
}
CRAWLER_IGNORED_FILES = {".env", "*.db", "*.db-*", "*.log", "*.sqlite", "*.sqlite-*", "*.sqlite3", "*.sqlite3-*"}


def _copy_tree(source: Path, destination: Path, *, ignored_dirs: set[str] | None = None, ignored_files: set[str] | None = None) -> None:
    """Copy one owned tree while rejecting links and generated files."""
    ignored_dirs = ignored_dirs or set()
    ignored_files = ignored_files or set()
    for source_path in sorted(source.rglob("*")):
        relative = source_path.relative_to(source)
        if any(part in ignored_dirs for part in relative.parts):
            continue
        if source_path.is_symlink():
            raise RuntimeError(f"交付源目录不能包含符号链接：{source_path}")
        target = destination / relative
        if source_path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        if any(fnmatch.fnmatch(source_path.name, pattern) for pattern in ignored_files):
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, target)


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _valid_component_provenance(root: Path, info: dict[str, object], artifacts: dict[str, Path]) -> bool:
    if not REVISION_PATTERN.fullmatch(str(info.get("source_revision") or "")) or not isinstance(info.get("source_dirty"), bool):
        return False
    for field, relative in artifacts.items():
        expected = str(info.get(field) or "").lower()
        artifact = root / relative
        if not SHA256_PATTERN.fullmatch(expected) or not artifact.is_file() or _hash(artifact) != expected:
            return False
    return True


def _program_manifest(
    program_root: Path, version: str, environment_version: str, schema_version: int,
    source_revision: str, source_dirty: bool,
) -> dict[str, object]:
    files = []
    # 清单只拥有程序文件，Core 合并的浏览器和采集组件不会被日常更新覆盖。
    for path in sorted(program_root.rglob("*")):
        if not path.is_file() or path.name == "release-manifest.json":
            continue
        files.append(
            {
                "path": safe_release_path(path.relative_to(program_root).as_posix()),
                "size": path.stat().st_size,
                "sha256": _hash(path),
            }
        )
    return {
        "format": 1,
        "product": "AI Customer Desktop",
        "version": version,
        "schema_version": schema_version,
        "environment_version": environment_version,
        "updater_version": UPDATER_VERSION,
        "source_revision": source_revision,
        "source_dirty": source_dirty,
        "built_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "entrypoint": APPLICATION_ENTRYPOINT,
        "files": files,
    }


def _zip_directory(source: Path, destination: Path) -> None:
    with zipfile.ZipFile(destination, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as archive:
        for path in sorted(source.rglob("*")):
            if path.is_file():
                archive.write(path, safe_release_path(path.relative_to(source).as_posix()))


def assemble(
    *,
    packaged_app: Path,
    stable_launcher: Path,
    staging_root: Path,
    delivery_root: Path,
    version: str,
    environment_version: str,
    schema_version: int,
    source_revision: str,
    source_dirty: bool,
    crawler_component_root: Path | None,
    cloakbrowser_root: Path | None,
    vox_component_root: Path | None,
    voice_models_root: Path | None,
    readme: Path,
    program_only: bool = False,
    include_voice: bool = False,
) -> tuple[Path, Path | None, Path | None]:
    """生成完整首装 Core、日常更新 Program 和按需安装的 Voice ZIP。"""
    if not VERSION_PATTERN.fullmatch(version) or not VERSION_PATTERN.fullmatch(environment_version) or schema_version < 0:
        raise RuntimeError("程序版本、环境版本或数据库版本无效")
    if not REVISION_PATTERN.fullmatch(source_revision) or not isinstance(source_dirty, bool):
        raise RuntimeError("必须记录有效的源码提交和工作区状态")
    if program_only and include_voice:
        raise RuntimeError("ProgramOnly 不包含可选语音组件")
    if delivery_root.exists():
        raise RuntimeError(f"交付目录已存在，请先提升版本号：{delivery_root}")
    # Short staging names keep deeply nested PyTorch headers below Windows MAX_PATH.
    program_root = staging_root / "p"
    artifact_root = staging_root / "a"
    program_runtime = program_root / "runtime"
    for path in (program_root, artifact_root):
        path.mkdir(parents=True, exist_ok=False)

    shutil.copy2(stable_launcher, program_root / "AI_Customer.exe")
    shutil.copy2(readme, program_root / "README.txt")
    application_program = program_runtime / "application"
    application_program.mkdir(parents=True, exist_ok=True)
    required_program_paths = ["AI_Customer_App.exe", "python311.dll", "playwright/driver/node.exe", "curl_cffi/_wrapper.pyd", "frontend_dist/index.html"]
    missing = [relative for relative in required_program_paths if not (packaged_app / relative).is_file()]
    if missing or not (packaged_app / "app").is_dir():
        raise RuntimeError("完整程序运行时缺失：" + ", ".join(missing or ["app/"]))
    # Nuitka standalone 的依赖必须随程序更新，不能与业务 EXE 分属两个版本。
    for source in sorted(packaged_app.iterdir()):
        target = program_runtime / "frontend_dist" if source.name == "frontend_dist" else application_program / source.name
        if source.is_dir():
            _copy_tree(source, target)
        else:
            if source.is_symlink():
                raise RuntimeError(f"交付源文件不能包含符号链接：{source}")
            shutil.copy2(source, target)

    _write_json(program_root / "release-manifest.json", _program_manifest(
        program_root, version, environment_version, schema_version, source_revision, source_dirty,
    ))
    program_zip = artifact_root / f"AI_Customer_Program_{version}.zip"
    _zip_directory(program_root, program_zip)
    core_zip = None
    voice_zip = None
    checksums = f"{_hash(program_zip)}  {program_zip.name}\n"

    # 日常更新只重建程序，不读取低频组件或语音模型。
    if program_only:
        (artifact_root / "SHA256.txt").write_text(checksums, encoding="ascii")
        (artifact_root / "README.txt").write_text(
            f"本目录为日常更新 Program ZIP，首次安装请使用 Core ZIP；要求基础环境 {environment_version}。\n",
            encoding="utf-8-sig",
        )
        delivery_root.parent.mkdir(parents=True, exist_ok=True)
        artifact_root.replace(delivery_root)
        return delivery_root / program_zip.name, None, None

    if crawler_component_root is None or cloakbrowser_root is None:
        raise RuntimeError("生成 Core ZIP 时必须提供采集组件和浏览器")
    environment_runtime = program_runtime

    crawler_info = json.loads((crawler_component_root / "component-info.json").read_text(encoding="utf-8-sig"))
    if (
        crawler_info.get("format") != 1
        or crawler_info.get("product") != "AI Customer Component"
        or crawler_info.get("component") != "mycrawler"
        or crawler_info.get("compiler") != "nuitka"
        or crawler_info.get("entrypoint") != "MyCrawler.exe"
        or not VERSION_PATTERN.fullmatch(str(crawler_info.get("version") or ""))
        or not _valid_component_provenance(
            crawler_component_root,
            crawler_info,
            {"entrypoint_sha256": Path("MyCrawler.exe")},
        )
    ):
        raise RuntimeError("MyCrawler 组件清单无效")
    crawler_destination = environment_runtime / "MyCrawler"
    _copy_tree(crawler_component_root, crawler_destination, ignored_dirs=CRAWLER_IGNORED_DIRS, ignored_files=CRAWLER_IGNORED_FILES)
    _copy_tree(cloakbrowser_root, environment_runtime / "cloakbrowser_browser")

    required_paths = [
        "cloakbrowser_browser/chrome.exe",
        "MyCrawler/MyCrawler.exe",
        "MyCrawler/playwright/driver/node.exe",
        "MyCrawler/wordcloud/stopwords",
    ]
    missing = [relative for relative in required_paths if not (environment_runtime / relative).is_file()]
    if missing:
        raise RuntimeError("基础环境缺少必需文件：" + ", ".join(missing))
    _write_json(environment_runtime / "environment-manifest.json", {
        "format": 1,
        "product": "AI Customer Environment",
        "version": environment_version,
        "built_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "source_revision": source_revision,
        "source_dirty": source_dirty,
        "required_paths": required_paths,
    })
    core_zip = artifact_root / f"AI_Customer_Core_{version}.zip"
    _zip_directory(program_root, core_zip)
    checksums += f"{_hash(core_zip)}  {core_zip.name}\n"

    if include_voice:
        if vox_component_root is None or voice_models_root is None:
            raise RuntimeError("IncludeVoice 必须提供语音组件和模型")
        voice_root = staging_root / "v"
        voice_runtime = voice_root / "runtime"
        voice_runtime.mkdir(parents=True, exist_ok=False)
        voice_version = _assemble_voice(vox_component_root, voice_models_root, voice_runtime)
        (voice_root / "README_语音组件.txt").write_text(
            "需要语音克隆时，将本 ZIP 解压到 Core 安装目录中并合并 runtime，然后重新启动软件。\n",
            encoding="utf-8-sig",
        )
        voice_zip = artifact_root / f"AI_Customer_Voice_{voice_version}.zip"
        _zip_directory(voice_root, voice_zip)
        checksums += f"{_hash(voice_zip)}  {voice_zip.name}\n"

    (artifact_root / "SHA256.txt").write_text(checksums, encoding="ascii")
    (artifact_root / "README.txt").write_text(
        "首次使用：解压 Core ZIP，双击 AI_Customer.exe。\n日常更新：通过软件安装 Program ZIP。\nVoice ZIP 为可选语音组件，不影响采集、筛选和私信。\n",
        encoding="utf-8-sig",
    )
    delivery_root.parent.mkdir(parents=True, exist_ok=True)
    artifact_root.replace(delivery_root)
    return delivery_root / program_zip.name, delivery_root / core_zip.name, delivery_root / voice_zip.name if voice_zip else None


def _assemble_voice(vox_component_root: Path, voice_models_root: Path, environment_runtime: Path) -> str:
    """复用已有组件来源校验，只将运行时和模型放入可选语音包。"""

    component_info = json.loads((vox_component_root / "component-info.json").read_text(encoding="utf-8-sig"))
    component_version = str(component_info.get("version") or "")
    native_module = str(component_info.get("native_module") or "")
    if (
        component_info.get("format") != 1
        or component_info.get("product") != "AI Customer Component"
        or component_info.get("component") != "voxcpm2"
        or component_info.get("compiler") != "nuitka"
        or component_info.get("runtime_packager") != "pyinstaller"
        or component_info.get("entrypoint") != "VoxCPM_Runtime.exe"
        or not re.fullmatch(r"r/ai_customer_voxcpm_native[^/]*\.pyd", native_module)
        or not (vox_component_root / Path(*PurePosixPath(native_module).parts)).is_file()
        or not VERSION_PATTERN.fullmatch(component_version)
        or not _valid_component_provenance(
            vox_component_root,
            component_info,
            {
                "entrypoint_sha256": Path("VoxCPM_Runtime.exe"),
                "native_module_sha256": Path(*PurePosixPath(native_module).parts),
            },
        )
    ):
        raise RuntimeError("VoxCPM2 组件清单无效")
    component_destination = environment_runtime / "components" / "voxcpm2"
    _copy_tree(vox_component_root, component_destination / "versions" / component_version)
    _write_json(
        component_destination / "current.json",
        {"component": "voxcpm2", "version": component_version, "entrypoint": "VoxCPM_Runtime.exe"},
    )
    _copy_tree(voice_models_root, environment_runtime / "models")

    model_files = sorted(path for path in (environment_runtime / "models" / "VoxCPM2").rglob("*") if path.is_file())
    if not model_files:
        raise RuntimeError("VoxCPM2 模型目录为空，无法生成完整环境包")
    required_paths = [
        "components/voxcpm2/current.json",
        "components/voxcpm2/versions/" + component_version + "/VoxCPM_Runtime.exe",
        "components/voxcpm2/versions/" + component_version + "/" + native_module,
        model_files[0].relative_to(environment_runtime).as_posix(),
    ]
    missing = [relative for relative in required_paths if not (environment_runtime / relative).is_file()]
    if missing:
        raise RuntimeError("语音包缺少必需文件：" + ", ".join(missing))
    return component_version


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Assemble AI Customer portable delivery ZIP files")
    for name in (
        "packaged-app",
        "stable-launcher",
        "staging-root",
        "delivery-root",
        "readme",
    ):
        parser.add_argument("--" + name, type=Path, required=True)
    for name in (
        "crawler-component-root",
        "cloakbrowser-root",
        "vox-component-root",
        "voice-models-root",
    ):
        parser.add_argument("--" + name, type=Path)
    parser.add_argument("--version", required=True)
    parser.add_argument("--environment-version", required=True)
    parser.add_argument("--schema-version", type=int, required=True)
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--source-dirty", action="store_true")
    parser.add_argument("--program-only", action="store_true")
    parser.add_argument("--include-voice", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = vars(_arguments())
    program_zip, core_zip, voice_zip = assemble(**args)
    print(json.dumps({"program_zip": str(program_zip), "core_zip": str(core_zip) if core_zip else None, "voice_zip": str(voice_zip) if voice_zip else None}, ensure_ascii=False))


if __name__ == "__main__":
    main()
