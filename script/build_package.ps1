param(
    [string]$Version = ""
)

$ErrorActionPreference = "Stop"

# Resolve every build path from the repository root.
$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$BackendDir = Join-Path $ProjectRoot "backend"
$FrontendDir = Join-Path $ProjectRoot "frontend"
$AppLauncher = Join-Path $ProjectRoot "packaging\ai_customer_launcher.py"
$StableLauncher = Join-Path $ProjectRoot "packaging\ai_customer_bootstrap.py"
$Readme = Join-Path $ProjectRoot "packaging\PACKAGE_README.txt"
$Installer = Join-Path $ProjectRoot "script\install_release.ps1"
$Switcher = Join-Path $ProjectRoot "script\switch_installed_version.ps1"
$Python = Join-Path $BackendDir ".venv\Scripts\python.exe"

# Stop when required local dependencies are missing; packaging never installs them implicitly.
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw "Backend virtual environment not found: $Python"
}
& $Python -c "import PyInstaller"
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller is missing from backend/.venv. Install it explicitly before packaging."
}
& $Python -c "import torch, voxcpm; assert torch.cuda.is_available()"
if ($LASTEXITCODE -ne 0) {
    throw "VoxCPM2 CUDA runtime is missing. Run script/install_voxcpm.ps1 before packaging."
}
$CloakBrowserPath = (& $Python -c "import cloakbrowser; print(cloakbrowser.ensure_binary())").Trim()
if (-not (Test-Path -LiteralPath $CloakBrowserPath -PathType Leaf)) {
    throw "CloakBrowser binary is missing. Run backend/.venv/Scripts/python.exe -m cloakbrowser install before packaging."
}
$CloakBrowserDir = Split-Path -Parent $CloakBrowserPath

# Read the product version from source unless the caller supplied one.
if (-not $Version) {
    $Version = (& $Python -c "import sys; sys.path.insert(0, r'$BackendDir'); from app.version import APP_VERSION; print(APP_VERSION)").Trim()
}
if ($Version -notmatch '^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$') {
    throw "Version must use semantic version format, for example 1.1.0"
}

# Use unique build and release folders so the script never deletes an older package.
$Stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$BuildRoot = Join-Path $ProjectRoot "output\b_${Version}_$Stamp"
$BuildDist = Join-Path $BuildRoot "dist"
$BuildWork = Join-Path $BuildRoot "work"
$BuildSpec = Join-Path $BuildRoot "spec"
$ReleaseDir = Join-Path $ProjectRoot "dist\releases\AI_Customer_${Version}_$Stamp"
if ((Test-Path -LiteralPath $BuildRoot) -or (Test-Path -LiteralPath $ReleaseDir)) {
    throw "Unique build output already exists; wait one second and retry."
}
New-Item -ItemType Directory -Path $BuildDist, $BuildWork, $BuildSpec, $ReleaseDir | Out-Null

# Run frontend tests before producing release assets.
Push-Location $FrontendDir
try {
    & npm test
    if ($LASTEXITCODE -ne 0) { throw "Frontend tests failed" }

    # Build the production frontend after type checking.
    & npm run build
    if ($LASTEXITCODE -ne 0) { throw "Frontend build failed" }
}
finally {
    Pop-Location
}

# Run the full backend suite against the same source that will be packaged.
Push-Location $ProjectRoot
try {
    $env:PYTHONPATH = $BackendDir
    & $Python -m pytest (Join-Path $BackendDir "tests") -q
    if ($LASTEXITCODE -ne 0) { throw "Backend tests failed" }
}
finally {
    Pop-Location
}

# Build the versioned application folder with optimized bytecode and no source files.
& $Python -m PyInstaller `
    --name "AI_Customer" `
    --onedir `
    --optimize 2 `
    --contents-directory "r" `
    --distpath $BuildDist `
    --workpath (Join-Path $BuildWork "app") `
    --specpath $BuildSpec `
    --paths $BackendDir `
    --paths $ProjectRoot `
    --additional-hooks-dir (Join-Path $ProjectRoot "packaging\hooks") `
    --add-data "$FrontendDir\dist;frontend_dist" `
    --add-data "$BackendDir\app\video_engine\resource;app/video_engine/resource" `
    --add-data "$BackendDir\app\video_engine\services\data;app/video_engine/services/data" `
    --add-data "$BackendDir\app\video_engine\config.default.toml;app/video_engine" `
    --add-data "$BackendDir\app\video_engine\LICENSE;app/video_engine" `
    --add-data "$BackendDir\app\publish_engine\utils\stealth.min.js;app/publish_engine/utils" `
    --add-data "$BackendDir\app\publish_engine\LICENSE;app/publish_engine" `
    --collect-all cv2 `
    --collect-all qrcode `
    --collect-all segno `
    --collect-all cloakbrowser `
    --collect-all moviepy `
    --copy-metadata imageio `
    --collect-all imageio_ffmpeg `
    --collect-all edge_tts `
    --collect-all faster_whisper `
    --collect-all ctranslate2 `
    --collect-all openai `
    --collect-all google.genai `
    --collect-all dashscope `
    --collect-all azure.cognitiveservices.speech `
    --collect-all twelvelabs `
    --collect-all pydub `
    --collect-all voxcpm `
    --collect-all torch `
    --collect-all torchaudio `
    --collect-all torchcodec `
    --collect-all transformers `
    --collect-all safetensors `
    --collect-all soundfile `
    --copy-metadata voxcpm `
    $AppLauncher
if ($LASTEXITCODE -ne 0) { throw "Application packaging failed" }

# Build a small stable launcher that selects versions through current-version.json.
& $Python -m PyInstaller `
    --name "AI_Customer_Launcher" `
    --onefile `
    --windowed `
    --optimize 2 `
    --distpath $BuildDist `
    --workpath (Join-Path $BuildWork "bootstrap") `
    --specpath $BuildSpec `
    $StableLauncher
if ($LASTEXITCODE -ne 0) { throw "Stable launcher packaging failed" }

# Assemble the immutable release payload.
$AppSource = Join-Path $BuildDist "AI_Customer"
$AppDestination = Join-Path $ReleaseDir "app"
$BundledCloakBrowser = Join-Path $AppSource "r\cloakbrowser_browser"
& robocopy $CloakBrowserDir $BundledCloakBrowser "/E" "/R:2" "/W:1" "/NFL" "/NDL" "/NJH" "/NJS" "/NC" "/NS" | Out-Null
if ($LASTEXITCODE -gt 7) { throw "CloakBrowser payload copy failed with robocopy exit code $LASTEXITCODE" }
$RobocopyArgs = @($AppSource, $AppDestination, "/E", "/R:2", "/W:1", "/NFL", "/NDL", "/NJH", "/NJS", "/NC", "/NS")
& robocopy @RobocopyArgs | Out-Null
if ($LASTEXITCODE -gt 7) { throw "Application payload copy failed with robocopy exit code $LASTEXITCODE" }
Copy-Item -LiteralPath (Join-Path $BuildDist "AI_Customer_Launcher.exe") -Destination (Join-Path $ReleaseDir "AI_Customer.exe")
Copy-Item -LiteralPath $Readme -Destination (Join-Path $ReleaseDir "README.txt")
Copy-Item -LiteralPath $Installer -Destination (Join-Path $ReleaseDir "install-release.ps1")
Copy-Item -LiteralPath $Switcher -Destination (Join-Path $ReleaseDir "switch-version.ps1")

# Record every release file hash before writing the manifest itself.
$ReleaseScanRoot = if ($IsWindows -or $env:OS -eq "Windows_NT") { "\\?\$ReleaseDir" } else { $ReleaseDir }
$ReleasePrefix = $ReleaseScanRoot.TrimEnd('\') + '\'
$Files = Get-ChildItem -LiteralPath $ReleaseScanRoot -Recurse -File | ForEach-Object {
    $RelativePath = $_.FullName.Substring($ReleasePrefix.Length).Replace('\', '/')
    [ordered]@{
        path = $RelativePath
        size = $_.Length
        sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    }
}
$SchemaVersion = [int](& $Python -c "import sys; sys.path.insert(0, r'$BackendDir'); from app.migrations import latest_version; print(latest_version())")
$Manifest = [ordered]@{
    format = 1
    product = "AI Customer Desktop"
    version = $Version
    schema_version = $SchemaVersion
    built_at = (Get-Date).ToString("yyyy-MM-ddTHH:mm:ssK")
    entrypoint = "app/AI_Customer.exe"
    files = $Files
}
$Manifest | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $ReleaseDir "release-manifest.json") -Encoding utf8

Write-Host "Release created:"
Write-Host "  $ReleaseDir"
Write-Host "Customer use: double-click AI_Customer.exe in this release folder."
Write-Host "Prepare or publish remotely with:"
Write-Host "  powershell -ExecutionPolicy Bypass -File `"$ProjectRoot\script\publish_release.ps1`" -ReleasePath `"$ReleaseDir`""
