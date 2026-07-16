param(
    [string]$Version = "1.0.0"
)

$ErrorActionPreference = "Stop"

# Resolve every path from the script directory for Windows PowerShell 5.1.
$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$BackendDir = Join-Path $ProjectRoot "backend"
$Python = Join-Path $BackendDir ".venv\Scripts\python.exe"
$Entrypoint = Join-Path $ProjectRoot "packaging\voxcpm_runtime.py"

if ($Version -notmatch '^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$') {
    throw "Version must use semantic version format, for example 1.0.0"
}
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw "Backend virtual environment not found: $Python"
}
& $Python -c "import PyInstaller, torch, voxcpm; assert torch.cuda.is_available()"
if ($LASTEXITCODE -ne 0) {
    throw "VoxCPM2 CUDA runtime is incomplete. Run script/install_voxcpm.ps1 first."
}

# Keep every component build in a unique directory for comparison and rollback.
$BuildTimestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$BuildRoot = Join-Path $ProjectRoot "output\voxcpm_${Version}_$BuildTimestamp"
$BuildDist = Join-Path $BuildRoot "dist"
$BuildWork = Join-Path $BuildRoot "work"
$BuildSpec = Join-Path $BuildRoot "spec"
$ComponentDir = Join-Path $ProjectRoot "dist\components\AI_Customer_VoxCPM2_${Version}_$BuildTimestamp"
if ((Test-Path -LiteralPath $BuildRoot) -or (Test-Path -LiteralPath $ComponentDir)) {
    throw "Unique component output already exists; wait one second and retry."
}
New-Item -ItemType Directory -Path $BuildDist, $BuildWork, $BuildSpec, $ComponentDir | Out-Null

& $Python -m PyInstaller `
    --name "VoxCPM_Runtime" `
    --onedir `
    --optimize 2 `
    --contents-directory "r" `
    --distpath $BuildDist `
    --workpath $BuildWork `
    --specpath $BuildSpec `
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
    $Entrypoint
if ($LASTEXITCODE -ne 0) { throw "VoxCPM2 component packaging failed" }

$RuntimeSource = Join-Path $BuildDist "VoxCPM_Runtime"
& robocopy $RuntimeSource $ComponentDir "/E" "/R:2" "/W:1" "/NFL" "/NDL" "/NJH" "/NJS" "/NC" "/NS" | Out-Null
if ($LASTEXITCODE -gt 7) { throw "Component payload copy failed with robocopy exit code $LASTEXITCODE" }
if (-not (Test-Path -LiteralPath (Join-Path $ComponentDir "VoxCPM_Runtime.exe") -PathType Leaf)) {
    throw "Component executable was not created"
}

$Info = [ordered]@{
    format = 1
    product = "AI Customer Component"
    component = "voxcpm2"
    version = $Version
    entrypoint = "VoxCPM_Runtime.exe"
    built_at = (Get-Date).ToString("yyyy-MM-ddTHH:mm:ssK")
}
$Info | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $ComponentDir "component-info.json") -Encoding utf8

Write-Host "VoxCPM2 component created:"
Write-Host "  $ComponentDir"
Write-Host "Prepare or upload it with:"
Write-Host "  powershell -ExecutionPolicy Bypass -File `"$ProjectRoot\script\publish_voxcpm_component.ps1`" -ComponentPath `"$ComponentDir`""
