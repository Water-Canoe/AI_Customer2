param(
    [string]$Version = "1.0.0"
)

$ErrorActionPreference = "Stop"

# Resolve every path from the script directory for Windows PowerShell 5.1.
$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$BackendDir = Join-Path $ProjectRoot "backend"
$Python = Join-Path $BackendDir ".venv\Scripts\python.exe"
$NativeEntrypoint = Join-Path $ProjectRoot "packaging\voxcpm_runtime.py"
$RuntimeLoader = Join-Path $ProjectRoot "packaging\voxcpm_loader.py"

if ($Version -notmatch '^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$') {
    throw "Version must use semantic version format, for example 1.0.0"
}
foreach ($RequiredFile in @($Python, $NativeEntrypoint, $RuntimeLoader)) {
    if (-not (Test-Path -LiteralPath $RequiredFile -PathType Leaf)) {
        throw "Required VoxCPM2 build file is missing: $RequiredFile"
    }
}
& $Python -c "import nuitka, PyInstaller, torch, voxcpm; assert torch.cuda.is_available()"
if ($LASTEXITCODE -ne 0) {
    throw "Nuitka or the VoxCPM2 CUDA runtime is incomplete. Install backend/requirements-dev.txt and run script/install_voxcpm.ps1."
}

# Keep every component build in a unique directory for comparison and rollback.
$BuildTimestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$BuildRoot = Join-Path $ProjectRoot "output\voxcpm_${Version}_$BuildTimestamp"
$BuildDist = Join-Path $BuildRoot "dist"
$BuildWork = Join-Path $BuildRoot "work"
$BuildSpec = Join-Path $BuildRoot "spec"
$NativeDist = Join-Path $BuildRoot "native"
$NativeSourceDir = Join-Path $BuildRoot "native-source"
$ComponentDir = Join-Path $ProjectRoot "dist\components\AI_Customer_VoxCPM2_${Version}_$BuildTimestamp"
if ((Test-Path -LiteralPath $BuildRoot) -or (Test-Path -LiteralPath $ComponentDir)) {
    throw "Unique component output already exists; wait one second and retry."
}
New-Item -ItemType Directory -Path $BuildDist, $BuildWork, $BuildSpec, $NativeDist, $NativeSourceDir, $ComponentDir | Out-Null

# Only compile the proprietary request handler; model frameworks stay as external runtime dependencies.
$RenamedNativeSource = Join-Path $NativeSourceDir "ai_customer_voxcpm_native.py"
Copy-Item -LiteralPath $NativeEntrypoint -Destination $RenamedNativeSource
& $Python -m nuitka `
    --mode=module `
    --output-dir=$NativeDist `
    --no-pyi-file `
    --python-flag=no_docstrings `
    $RenamedNativeSource
if ($LASTEXITCODE -ne 0) { throw "VoxCPM2 native entry module compilation failed" }
$CompiledEntrypoint = @(Get-ChildItem -LiteralPath $NativeDist -Filter "ai_customer_voxcpm_native*.pyd" -File | Select-Object -First 1)[0]
if (-not $CompiledEntrypoint) {
    throw "Nuitka did not create the VoxCPM2 native entry module"
}
& $Python -c "import sys; sys.path.insert(0, r'$NativeDist'); import ai_customer_voxcpm_native; assert callable(ai_customer_voxcpm_native.serve)"
if ($LASTEXITCODE -ne 0) { throw "VoxCPM2 native entry module cannot be imported" }

& $Python -m PyInstaller `
    --name "VoxCPM_Runtime" `
    --onedir `
    --optimize 2 `
    --contents-directory "r" `
    --distpath $BuildDist `
    --workpath $BuildWork `
    --specpath $BuildSpec `
    --paths $NativeDist `
    --paths $BackendDir `
    --exclude-module patchright `
    --exclude-module playwright `
    --collect-all voxcpm `
    --collect-all torch `
    --collect-all torchaudio `
    --collect-all torchcodec `
    --collect-all transformers `
    --collect-all safetensors `
    --collect-all soundfile `
    --copy-metadata voxcpm `
    $RuntimeLoader
if ($LASTEXITCODE -ne 0) { throw "VoxCPM2 component packaging failed" }

$RuntimeSource = Join-Path $BuildDist "VoxCPM_Runtime"
& robocopy $RuntimeSource $ComponentDir "/E" "/R:2" "/W:1" "/NFL" "/NDL" "/NJH" "/NJS" "/NC" "/NS" | Out-Null
if ($LASTEXITCODE -gt 7) { throw "Component payload copy failed with robocopy exit code $LASTEXITCODE" }
if (-not (Test-Path -LiteralPath (Join-Path $ComponentDir "VoxCPM_Runtime.exe") -PathType Leaf)) {
    throw "Component executable was not created"
}
$PackagedNativeModule = @(Get-ChildItem -LiteralPath $ComponentDir -Filter "ai_customer_voxcpm_native*.pyd" -File -Recurse | Select-Object -First 1)[0]
if (-not $PackagedNativeModule) {
    throw "Component does not contain the Nuitka native entry module"
}
$SmokeResult = '{}' | & (Join-Path $ComponentDir "VoxCPM_Runtime.exe") --serve
if ($LASTEXITCODE -ne 0) { throw "VoxCPM2 component protocol smoke test failed" }
$SmokePayload = $SmokeResult | ConvertFrom-Json
if ($SmokePayload.ok -ne $false -or -not [string]$SmokePayload.error) {
    throw "VoxCPM2 component returned an invalid smoke-test response"
}

$Info = [ordered]@{
    format = 1
    product = "AI Customer Component"
    component = "voxcpm2"
    version = $Version
    entrypoint = "VoxCPM_Runtime.exe"
    compiler = "nuitka"
    runtime_packager = "pyinstaller"
    native_module = $PackagedNativeModule.FullName.Substring($ComponentDir.Length + 1).Replace("\", "/")
    built_at = (Get-Date).ToString("yyyy-MM-ddTHH:mm:ssK")
}
$Info | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $ComponentDir "component-info.json") -Encoding utf8

Write-Host "VoxCPM2 component created:"
Write-Host "  $ComponentDir"
Write-Host "Prepare or upload it with:"
Write-Host "  powershell -ExecutionPolicy Bypass -File `"$ProjectRoot\script\publish_voxcpm_component.ps1`" -ComponentPath `"$ComponentDir`""
