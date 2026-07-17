from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import re
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path


PROGRAM_RUNTIME_DIRS = {"app", "frontend_dist"}
VERSION_PATTERN = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$")
CRAWLER_IGNORED_DIRS = {
    ".git",
    ".venv",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "__pycache__",
    "browser_data",
    "cache",
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


def _program_manifest(program_root: Path, version: str, environment_version: str, schema_version: int) -> dict[str, object]:
    files = []
    # 正在运行的稳定入口随初始 ZIP 交付，但不进入远程覆盖白名单。
    for path in sorted(program_root.rglob("*")):
        if not path.is_file() or path.name in {"AI_Customer.exe", "release-manifest.json"}:
            continue
        files.append(
            {
                "path": path.relative_to(program_root).as_posix(),
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
        "built_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "entrypoint": "AI_Customer_App.exe",
        "files": files,
    }


def _zip_directory(source: Path, destination: Path) -> None:
    with zipfile.ZipFile(destination, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as archive:
        for path in sorted(source.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(source).as_posix())


def assemble(
    *,
    packaged_app: Path,
    stable_launcher: Path,
    staging_root: Path,
    delivery_root: Path,
    version: str,
    environment_version: str,
    schema_version: int,
    crawler_python_root: Path | None,
    crawler_root: Path | None,
    cloakbrowser_root: Path | None,
    vox_component_root: Path | None,
    voice_models_root: Path | None,
    readme: Path,
    program_only: bool = False,
) -> tuple[Path, Path | None]:
    """Assemble the one frequently updated ZIP and the one reusable environment ZIP."""
    if not VERSION_PATTERN.fullmatch(version) or not VERSION_PATTERN.fullmatch(environment_version) or schema_version < 0:
        raise RuntimeError("程序版本、环境版本或数据库版本无效")
    if delivery_root.exists():
        raise RuntimeError(f"交付目录已存在，请先提升版本号：{delivery_root}")
    # Short staging names keep deeply nested PyTorch headers below Windows MAX_PATH.
    program_root = staging_root / "p"
    environment_root = staging_root / "e"
    artifact_root = staging_root / "a"
    program_runtime = program_root / "runtime"
    environment_runtime = environment_root / "runtime"
    for path in (program_root, environment_root, artifact_root):
        path.mkdir(parents=True, exist_ok=False)

    shutil.copy2(stable_launcher, program_root / "AI_Customer.exe")
    shutil.copy2(packaged_app / "AI_Customer_App.exe", program_root / "AI_Customer_App.exe")
    shutil.copy2(readme, program_root / "README.txt")
    packaged_runtime = packaged_app / "runtime"
    for name in PROGRAM_RUNTIME_DIRS:
        source = packaged_runtime / name
        if not source.is_dir():
            raise RuntimeError(f"程序资源缺失：{source}")
        _copy_tree(source, program_runtime / name)

    # Program-only deliveries reuse an existing matching environment ZIP.
    if program_only:
        _write_json(program_root / "release-manifest.json", _program_manifest(program_root, version, environment_version, schema_version))
        program_zip = artifact_root / f"AI_Customer_Program_{version}.zip"
        _zip_directory(program_root, program_zip)
        (artifact_root / "SHA256.txt").write_text(f"{_hash(program_zip)}  {program_zip.name}\n", encoding="ascii")
        (artifact_root / "README.txt").write_text(
            f"本目录仅包含程序 ZIP，运行时需要已安装 Environment {environment_version}。\n",
            encoding="utf-8-sig",
        )
        delivery_root.parent.mkdir(parents=True, exist_ok=True)
        artifact_root.replace(delivery_root)
        return delivery_root / program_zip.name, None

    if any(path is None for path in (crawler_python_root, crawler_root, cloakbrowser_root, vox_component_root, voice_models_root)):
        raise RuntimeError("生成环境 ZIP 时必须提供全部环境来源")

    for source in sorted(packaged_runtime.iterdir()):
        if source.name in PROGRAM_RUNTIME_DIRS:
            continue
        target = environment_runtime / source.name
        if source.is_dir():
            _copy_tree(source, target)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)

    _copy_tree(crawler_python_root, environment_runtime / "python", ignored_dirs={"site-packages", "__pycache__"})
    crawler_destination = environment_runtime / "MyCrawler"
    _copy_tree(crawler_root, crawler_destination, ignored_dirs=CRAWLER_IGNORED_DIRS, ignored_files=CRAWLER_IGNORED_FILES)
    _copy_tree(crawler_root / ".venv" / "Lib" / "site-packages", crawler_destination / ".venv" / "Lib" / "site-packages")
    _copy_tree(cloakbrowser_root, environment_runtime / "cloakbrowser_browser")

    component_info = json.loads((vox_component_root / "component-info.json").read_text(encoding="utf-8-sig"))
    component_version = str(component_info.get("version") or "")
    if (
        component_info.get("format") != 1
        or component_info.get("product") != "AI Customer Component"
        or component_info.get("component") != "voxcpm2"
        or component_info.get("entrypoint") != "VoxCPM_Runtime.exe"
        or not VERSION_PATTERN.fullmatch(component_version)
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
        "python311.dll",
        "playwright/driver/node.exe",
        "cloakbrowser_browser/chrome.exe",
        "python/python.exe",
        "MyCrawler/main.py",
        "MyCrawler/.venv/Lib/site-packages/playwright/driver/node.exe",
        "components/voxcpm2/current.json",
        "components/voxcpm2/versions/" + component_version + "/VoxCPM_Runtime.exe",
        model_files[0].relative_to(environment_runtime).as_posix(),
    ]
    missing = [relative for relative in required_paths if not (environment_runtime / relative).is_file()]
    if missing:
        raise RuntimeError("环境包缺少必需文件：" + ", ".join(missing))
    _write_json(
        environment_runtime / "environment-manifest.json",
        {
            "format": 1,
            "product": "AI Customer Environment",
            "version": environment_version,
            "built_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
            "required_paths": required_paths,
        },
    )
    (environment_root / "README_环境包.txt").write_text(
        "把本 ZIP 直接解压到程序 ZIP 解压后的目录中，确认 runtime 文件夹合并即可。\n",
        encoding="utf-8-sig",
    )
    _write_json(program_root / "release-manifest.json", _program_manifest(program_root, version, environment_version, schema_version))

    program_zip = artifact_root / f"AI_Customer_Program_{version}.zip"
    environment_zip = artifact_root / f"AI_Customer_Environment_{environment_version}.zip"
    _zip_directory(program_root, program_zip)
    _zip_directory(environment_root, environment_zip)
    checksums = f"{_hash(program_zip)}  {program_zip.name}\n{_hash(environment_zip)}  {environment_zip.name}\n"
    (artifact_root / "SHA256.txt").write_text(checksums, encoding="ascii")
    (artifact_root / "README.txt").write_text(
        "1. 解压 Program ZIP。\n2. 把 Environment ZIP 解压到同一目录并合并 runtime。\n3. 双击 AI_Customer.exe。\n",
        encoding="utf-8-sig",
    )
    delivery_root.parent.mkdir(parents=True, exist_ok=True)
    artifact_root.replace(delivery_root)
    return delivery_root / program_zip.name, delivery_root / environment_zip.name


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
        "crawler-python-root",
        "crawler-root",
        "cloakbrowser-root",
        "vox-component-root",
        "voice-models-root",
    ):
        parser.add_argument("--" + name, type=Path)
    parser.add_argument("--version", required=True)
    parser.add_argument("--environment-version", required=True)
    parser.add_argument("--schema-version", type=int, required=True)
    parser.add_argument("--program-only", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = vars(_arguments())
    program_zip, environment_zip = assemble(**{key.replace("-", "_"): value for key, value in args.items()})
    print(json.dumps({"program_zip": str(program_zip), "environment_zip": str(environment_zip) if environment_zip else None}, ensure_ascii=False))


if __name__ == "__main__":
    main()
