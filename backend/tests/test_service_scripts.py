from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
POWERSHELL = shutil.which("powershell.exe")
pytestmark = pytest.mark.skipif(not POWERSHELL, reason="Windows PowerShell is required")


def run_script_check(tmp_path: Path, body: str) -> subprocess.CompletedProcess[str]:
    """只运行隔离检查；网络、进程与停止动作均由替身承担。"""
    runner = tmp_path / "check.ps1"
    runner.write_text(
        '$ErrorActionPreference = "Stop"\n. $env:COMMON_SCRIPT\n' + body,
        encoding="utf-8-sig",
    )
    return subprocess.run(
        [str(POWERSHELL), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(runner)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15,
        env={**os.environ, "COMMON_SCRIPT": str(ROOT / "script" / "internal" / "_common.ps1")},
    )


def test_process_ownership_requires_project_and_service_including_venv_child(tmp_path: Path) -> None:
    result = run_script_check(tmp_path, r'''
$ProjectRoot = 'D:\isolated\Project Space'
function Get-CimInstance {
    param($ClassName, $Filter, $ErrorAction)
    if ($Filter -eq 'ProcessId = 123') {
        return [pscustomobject]@{ CommandLine = '"D:\isolated\Project Space\backend\.venv\Scripts\python.exe" -m app.main' }
    }
}
$checks = @(
    (Test-ProjectOwnedProcess -ServiceName backend -Process @{ CommandLine = '"D:\other\python.exe" -m uvicorn app.main:app'; ParentProcessId = 0 }),
    (Test-ProjectOwnedProcess -ServiceName backend -Process @{ CommandLine = '"D:\isolated\Project Space-other\python.exe" -m uvicorn app.main:app'; ParentProcessId = 0 }),
    (Test-ProjectOwnedProcess -ServiceName frontend -Process @{ CommandLine = '"node" "D:\other\vite\bin\vite.js" --host 127.0.0.1' }),
    (Test-ProjectOwnedProcess -ServiceName backend -Process @{ CommandLine = '"D:\base\python.exe" -m app.main'; ParentProcessId = 123 }),
    (Test-ProjectOwnedProcess -ServiceName frontend -Process @{ CommandLine = '"node" "D:\isolated\Project Space\frontend\node_modules\vite\bin\vite.js"' })
)
ConvertTo-Json -InputObject $checks -Compress
''')
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == [False, False, False, True, True]


def test_foreign_port_is_rejected_before_any_stop(tmp_path: Path) -> None:
    result = run_script_check(tmp_path, r'''
$global:StopCalls = 0
function Get-ListenerProcesses { return @{ ProcessId = 42; ParentProcessId = 0; CommandLine = 'node D:\other\vite\bin\vite.js' } }
function Stop-ProcessTree { $global:StopCalls++; throw 'UNEXPECTED_STOP' }
try { Stop-ProjectProcessesOnPort -Port 5173 -ServiceName frontend; throw 'UNEXPECTED_SUCCESS' }
catch { if ($_.Exception.Message -notmatch 'non-project') { throw } }
if ($global:StopCalls) { throw 'A foreign process was stopped.' }
''')
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(
    ("status", "product", "process_id", "ready"),
    [(404, "ai-customer", 42, False), (200, "other-product", 42, False),
     (200, "ai-customer", 99, False), (200, "ai-customer", 42, True)],
)
def test_health_requires_200_product_and_listener_pid(
    tmp_path: Path, status: int, product: str, process_id: int, ready: bool,
) -> None:
    response = json.dumps({"status": "ok", "product": product, "process_id": process_id})
    result = run_script_check(tmp_path, r'''
function Get-ProjectListener { return @{ ProcessId = 42; ParentProcessId = 1 } }
function Invoke-WebRequest {
    param($Uri, [switch]$UseBasicParsing, $TimeoutSec)
    if ($TimeoutSec -ne 2) { throw 'Missing request timeout.' }
    return @{ StatusCode = STATUS; Content = 'RESPONSE' }
}
Wait-HttpOk -Url 'http://offline.invalid/api/health' -ServiceName backend -Port 8000 -TimeoutSeconds 1
'''.replace("STATUS", str(status)).replace("RESPONSE", response))
    assert (result.returncode == 0) is ready, result.stdout + result.stderr
    if not ready:
        assert "did not become ready" in result.stderr


def test_backend_stop_uses_graceful_exit_without_force_kill(tmp_path: Path) -> None:
    result = run_script_check(tmp_path, r'''
$global:Exited = $false
function Get-ProjectListener { return @{ ProcessId = 42; ParentProcessId = 1 } }
function Get-ListenerProcesses { if (-not $global:Exited) { return @{ ProcessId = 42 } } }
function Invoke-WebRequest {
    param($Uri, [switch]$UseBasicParsing, $TimeoutSec)
    return @{ StatusCode = 200; Content = '{"status":"ok","product":"ai-customer","process_id":42}' }
}
function Invoke-RestMethod {
    param($Uri, $Method, $TimeoutSec)
    if (-not $Uri.EndsWith('/api/system/exit') -or $Method -ne 'Post' -or $TimeoutSec -ne 5) { throw 'Wrong exit request.' }
    $global:Exited = $true
    return @{ ok = $true }
}
function Stop-ProcessTree { throw 'UNEXPECTED_FORCE_KILL' }
Stop-ProjectProcessesOnPort -Port 8000 -ServiceName backend
if (-not $global:Exited) { throw 'Graceful exit was not requested.' }
''')
    assert result.returncode == 0, result.stdout + result.stderr


def test_failed_launch_is_detected_before_network_poll(tmp_path: Path) -> None:
    result = run_script_check(tmp_path, r'''
function Get-Process { param($Id, $ErrorAction); return $null }
function Invoke-WebRequest { throw 'UNEXPECTED_NETWORK_CALL' }
Wait-HttpOk -Url 'http://offline.invalid' -ServiceName frontend -Port 5173 -LaunchProcessId 123
''')
    assert result.returncode != 0
    assert "exited before becoming ready" in result.stderr


def test_cleanup_skips_process_that_has_already_exited(tmp_path: Path) -> None:
    result = run_script_check(tmp_path, r'''
function Get-Process { param($Id, $ErrorAction); return $null }
function taskkill.exe { throw 'UNEXPECTED_KILL' }
Stop-ProcessTree -ProcessId 123
''')
    assert result.returncode == 0, result.stdout + result.stderr


def test_repeated_start_reuses_services_without_launch_or_stop(tmp_path: Path) -> None:
    project = tmp_path / "project space's"
    script = project / "script"
    internal = script / "internal"
    internal.mkdir(parents=True)
    vite = project / "frontend" / "node_modules" / "vite" / "bin" / "vite.js"
    vite.parent.mkdir(parents=True)
    vite.write_text("fixture")
    # 仅替换解释器路径；运行真正的启动入口和内部工具。
    for name in ("start_all.ps1", "internal/_common.ps1", "internal/start_backend.ps1", "internal/start_frontend.ps1"):
        content = (ROOT / "script" / name).read_text(encoding="utf-8-sig")
        content = content.replace(
            'Join-Path $BackendDir ".venv\\Scripts\\python.exe"',
            "'" + os.sys.executable.replace("'", "''") + "'",
        )
        (script / name).write_text(content, encoding="utf-8-sig")
    body = r'''
function Get-NetTCPConnection {
    param($LocalPort, $State, $ErrorAction)
    return [pscustomobject]@{ OwningProcess = $(if ($LocalPort -eq 8000) { 111 } else { 222 }) }
}
function Get-CimInstance {
    param($ClassName, $Filter, $ErrorAction)
    if ($Filter -eq 'ProcessId = 111') { return @{ ProcessId = 111; CommandLine = '"PROJECT\backend\python.exe" -m app.main' } }
    return @{ ProcessId = 222; CommandLine = 'node "PROJECT\frontend\node_modules\vite\bin\vite.js"' }
}
function Invoke-WebRequest {
    param($Uri, [switch]$UseBasicParsing, $TimeoutSec)
    return @{ StatusCode = 200; Content = '{"status":"ok","product":"ai-customer","process_id":111}' }
}
function Start-Process { throw 'UNEXPECTED_LAUNCH' }
function Stop-Process { throw 'UNEXPECTED_STOP' }
& 'START_SCRIPT'
'''.replace("PROJECT", str(project).replace("'", "''")).replace("START_SCRIPT", str(script / "start_all.ps1").replace("'", "''"))
    result = run_script_check(tmp_path, body)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "backend is ready" in result.stdout and "frontend is ready" in result.stdout


def test_frontend_launch_quotes_vite_path_and_keeps_fixed_port(tmp_path: Path) -> None:
    project = tmp_path / "project space's"
    internal = project / "script" / "internal"
    internal.mkdir(parents=True)
    vite = project / "frontend" / "node_modules" / "vite" / "bin" / "vite.js"
    vite.parent.mkdir(parents=True)
    vite.write_text("fixture")
    for name in ("_common.ps1", "start_frontend.ps1"):
        shutil.copyfile(ROOT / "script" / "internal" / name, internal / name)
    body = r'''
$global:Launched = $false
function Get-Command { param($Name, $ErrorAction); return @{ Source = 'node.exe' } }
function Get-NetTCPConnection {
    param($LocalPort, $State, $ErrorAction)
    if ($global:Launched) { return [pscustomobject]@{ OwningProcess = 333 } }
}
function Get-CimInstance {
    param($ClassName, $Filter, $ErrorAction)
    return @{ ProcessId = 333; ParentProcessId = 1; CommandLine = 'node "VITE_PATH"' }
}
function Get-Process { param($Id, $ErrorAction); return @{ Id = 333 } }
function Start-Process {
    param($FilePath, $ArgumentList, $WorkingDirectory, $RedirectStandardOutput, $RedirectStandardError, $WindowStyle, [switch]$PassThru)
    if ($ArgumentList -ne '"VITE_PATH" --host 127.0.0.1 --port 5173 --strictPort' -or $WindowStyle -ne 'Hidden') { throw 'Wrong Vite launch arguments.' }
    $global:Launched = $true
    return @{ Id = 333 }
}
function Invoke-WebRequest { param($Uri, [switch]$UseBasicParsing, $TimeoutSec); return @{ StatusCode = 200; Content = '<html>Vite</html>' } }
function taskkill.exe { throw 'UNEXPECTED_STOP' }
& 'START_SCRIPT'
if (-not $global:Launched) { throw 'Frontend was not launched.' }
'''.replace("VITE_PATH", str(vite).replace("'", "''")).replace("START_SCRIPT", str(internal / "start_frontend.ps1").replace("'", "''"))
    result = run_script_check(tmp_path, body)
    assert result.returncode == 0, result.stdout + result.stderr


def prepare_builder(tmp_path: Path) -> tuple[Path, Path]:
    project = tmp_path / "project space's"
    (project / "frontend").mkdir(parents=True)
    (project / "script").mkdir()
    (project / "script" / "internal").mkdir()
    shutil.copyfile(ROOT / "script" / "internal" / "_version.ps1", project / "script" / "internal" / "_version.ps1")
    builder = project / "script" / "build_package.ps1"
    content = (ROOT / "script" / "build_package.ps1").read_text(encoding="utf-8-sig")
    content = content.replace('Join-Path $BackendDir ".venv\\Scripts\\python.exe"', "'Mock-Python'")
    builder.write_text(content, encoding="utf-8-sig")
    return project, builder


def test_build_failure_restores_pythonpath_and_working_directory(tmp_path: Path) -> None:
    _, builder = prepare_builder(tmp_path)
    body = r'''
function Test-Path { param($Path, $LiteralPath, $PathType); return $PathType -eq 'Leaf' }
function Get-Command { param($Name, $ErrorAction); return @{ Source = 'Mock-Npm' } }
function Mock-Npm { $global:LASTEXITCODE = 0 }
function Mock-Python {
    $global:LASTEXITCODE = 0
    if ($args[0] -eq '-c' -and $args[1].Contains('print(APP_VERSION)')) { return '9.9.9' }
    if ($args[0] -eq '-m' -and $args[1] -eq 'pytest') { $global:LASTEXITCODE = 1 }
}
$env:PYTHONPATH = 'original-python-path'
$originalLocation = (Get-Location).Path
try { & 'BUILD_SCRIPT' -ProgramOnly; throw 'UNEXPECTED_SUCCESS' }
catch { if ($_.Exception.Message -ne 'Backend tests failed') { throw } }
if ($env:PYTHONPATH -ne 'original-python-path' -or (Get-Location).Path -ne $originalLocation) { throw 'Build leaked environment or working directory.' }
'''.replace("BUILD_SCRIPT", str(builder).replace("'", "''"))
    result = run_script_check(tmp_path, body)
    assert result.returncode == 0, result.stdout + result.stderr


def test_existing_delivery_fails_before_compiler_or_component_checks(tmp_path: Path) -> None:
    project, builder = prepare_builder(tmp_path)
    delivery = project / "deliverables" / "9.9.9"
    delivery.mkdir(parents=True)
    body = r'''
function Test-Path {
    param($LiteralPath, $PathType)
    if ($LiteralPath -eq 'Mock-Python') { return $true }
    Microsoft.PowerShell.Management\Test-Path @PSBoundParameters
}
function Mock-Python {
    if ($args[0] -ne '-c' -or -not $args[1].Contains('print(APP_VERSION)')) { throw 'UNEXPECTED_COMPILER_CHECK' }
    $global:LASTEXITCODE = 0
    return '9.9.9'
}
try { & 'BUILD_SCRIPT'; throw 'UNEXPECTED_SUCCESS' }
catch { if ($_.Exception.Message -notmatch 'Delivery output already exists') { throw }; Write-Output $_.Exception.Message }
'''.replace("BUILD_SCRIPT", str(builder).replace("'", "''"))
    result = run_script_check(tmp_path, body)
    assert result.returncode == 0, result.stdout + result.stderr
    assert str(delivery) in result.stdout
    assert not (project / "output").exists()


@pytest.mark.parametrize("component", ["mycrawler", "voxcpm2"])
def test_ambiguous_components_list_candidates_before_compilation(tmp_path: Path, component: str) -> None:
    project, builder = prepare_builder(tmp_path)
    candidates = []
    for kind in ("mycrawler", "voxcpm2"):
        for index in range(2 if kind == component else 1):
            directory = project / "dist" / "components" / f"{kind}-{index}"
            directory.mkdir(parents=True)
            entrypoint = "MyCrawler.exe" if kind == "mycrawler" else "VoxCPM_Runtime.exe"
            (directory / entrypoint).write_bytes(b"fixture")
            info = {"component": kind, "compiler": "nuitka", "entrypoint": entrypoint,
                    "source_revision": "a" * 40, "source_dirty": False, "entrypoint_sha256": "b" * 64}
            if kind == "voxcpm2":
                (directory / "r").mkdir()
                (directory / "r" / "ai_customer_voxcpm_native.pyd").write_bytes(b"fixture")
                info.update(runtime_packager="pyinstaller", native_module="r/ai_customer_voxcpm_native.pyd",
                            native_module_sha256="c" * 64)
            (directory / "component-info.json").write_text(json.dumps(info))
            if kind == component:
                candidates.append(directory)
    browser = project / "browser.exe"
    browser.write_bytes(b"fixture")
    (project / "data" / "voice_models" / "VoxCPM2").mkdir(parents=True)
    body = r'''
function Test-Path {
    param($LiteralPath, $PathType)
    if ($LiteralPath -eq 'Mock-Python') { return $true }
    Microsoft.PowerShell.Management\Test-Path @PSBoundParameters
}
function Mock-Python {
    $global:LASTEXITCODE = 0
    if ($args[0] -eq '-c' -and $args[1].Contains('print(APP_VERSION)')) { return '9.9.9' }
    if ($args[0] -eq '-c' -and $args[1].Contains('binary_info')) { return 'BROWSER_PATH' }
    throw 'UNEXPECTED_COMPILER_CHECK'
}
try { & 'BUILD_SCRIPT' BUILD_FLAGS; throw 'UNEXPECTED_SUCCESS' }
catch { if ($_.Exception.Message -notmatch 'Multiple valid') { throw }; Write-Output $_.Exception.Message }
'''.replace("BUILD_SCRIPT", str(builder).replace("'", "''")).replace(
        "BROWSER_PATH", str(browser).replace("'", "''")
    ).replace("BUILD_FLAGS", "-IncludeVoice" if component == "voxcpm2" else "")
    result = run_script_check(tmp_path, body)
    assert result.returncode == 0, result.stdout + result.stderr
    assert all(str(directory) in result.stdout for directory in candidates)
    flag = "-MyCrawlerComponentPath" if component == "mycrawler" else "-VoxComponentPath"
    assert flag in result.stdout
    assert not (project / "output").exists()
