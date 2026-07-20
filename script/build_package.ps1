param(
    [string]$Version = "",
    [string]$EnvironmentVersion = "1.0.0",
    [string]$VoxComponentPath = "",
    [string]$VoiceModelsPath = "",
    [switch]$ProgramOnly
)

$ErrorActionPreference = "Stop"
$SemVerPattern = '^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$'

# Resolve all paths from the repository root so the caller's location cannot change the build.
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$BackendDir = Join-Path $ProjectRoot "backend"
$FrontendDir = Join-Path $ProjectRoot "frontend"
$CrawlerRoot = Join-Path $ProjectRoot "MyCrawler"
$AppLauncher = Join-Path $ProjectRoot "packaging\ai_customer_launcher.py"
$StableLauncher = Join-Path $ProjectRoot "packaging\ai_customer_bootstrap.py"
$AppIcon = Join-Path $ProjectRoot "packaging\ai-customer-icon.ico"
$DeliveryAssembler = Join-Path $ProjectRoot "script\assemble_delivery.py"
$Readme = Join-Path $ProjectRoot "packaging\PACKAGE_README.txt"
$Python = Join-Path $BackendDir ".venv\Scripts\python.exe"
$CrawlerPython = Join-Path $CrawlerRoot ".venv\Scripts\python.exe"
$CrawlerSitePackages = Join-Path $CrawlerRoot ".venv\Lib\site-packages"

# Validate local dependencies only; this script never installs missing packages implicitly.
foreach ($RequiredFile in @($Python, $CrawlerPython, $AppLauncher, $StableLauncher, $AppIcon, $DeliveryAssembler, $Readme)) {
    if (-not (Test-Path -LiteralPath $RequiredFile -PathType Leaf)) {
        throw "Required build file is missing: $RequiredFile"
    }
}
if (-not (Test-Path -LiteralPath $CrawlerSitePackages -PathType Container)) {
    throw "MyCrawler virtual environment is incomplete: $CrawlerSitePackages"
}
& $Python -c "import PyInstaller"
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller is missing from backend/.venv. Install it explicitly before packaging."
}
$CloakBrowserPath = (& $Python -c "import cloakbrowser; print(cloakbrowser.ensure_binary())").Trim()
if (-not (Test-Path -LiteralPath $CloakBrowserPath -PathType Leaf)) {
    throw "CloakBrowser is missing. Run backend/.venv/Scripts/python.exe -m cloakbrowser install first."
}
$CloakBrowserDir = Split-Path -Parent $CloakBrowserPath
$CrawlerPythonRoot = (& $CrawlerPython -c "import sys; print(sys.base_prefix)").Trim()
if (-not (Test-Path -LiteralPath (Join-Path $CrawlerPythonRoot "python.exe") -PathType Leaf)) {
    throw "MyCrawler base Python is missing: $CrawlerPythonRoot"
}

# Keep one version source so the EXE and release manifest cannot disagree.
$SourceVersion = (& $Python -c "import sys; sys.path.insert(0, r'$BackendDir'); from app.version import APP_VERSION; print(APP_VERSION)").Trim()
if (-not $Version) {
    $Version = $SourceVersion
}
elseif ($Version -ne $SourceVersion) {
    throw "Version must match backend/app/version.py ($SourceVersion)"
}
if ($Version -notmatch $SemVerPattern -or $EnvironmentVersion -notmatch $SemVerPattern) {
    throw "Version and EnvironmentVersion must use semantic version format, for example 1.2.3"
}
if (-not $VoiceModelsPath) {
    $VoiceModelsPath = Join-Path $ProjectRoot "data\voice_models"
}
if (-not (Test-Path -LiteralPath (Join-Path $VoiceModelsPath "VoxCPM2") -PathType Container)) {
    throw "VoxCPM2 model files are missing: $VoiceModelsPath\VoxCPM2"
}
if (-not $VoxComponentPath) {
    $ComponentRoot = Join-Path $ProjectRoot "dist\components"
    $VoxComponentPath = @(Get-ChildItem -LiteralPath $ComponentRoot -Directory -ErrorAction SilentlyContinue | Where-Object {
        (Test-Path -LiteralPath (Join-Path $_.FullName "component-info.json") -PathType Leaf) -and
        (Test-Path -LiteralPath (Join-Path $_.FullName "VoxCPM_Runtime.exe") -PathType Leaf)
    } | Sort-Object LastWriteTime -Descending | Select-Object -First 1)[0].FullName
}
if (-not $VoxComponentPath -or -not (Test-Path -LiteralPath (Join-Path $VoxComponentPath "VoxCPM_Runtime.exe") -PathType Leaf)) {
    throw "A completed VoxCPM2 component is required through -VoxComponentPath"
}

# Keep timestamped intermediate files; customers only receive deliverables/<version>.
$Stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$BuildRoot = Join-Path $ProjectRoot "output\build_${Version}_$Stamp"
$BuildDist = Join-Path $BuildRoot "dist"
$BuildWork = Join-Path $BuildRoot "work"
$BuildSpec = Join-Path $BuildRoot "spec"
$StagingRoot = Join-Path $ProjectRoot "output\stage_$Stamp"
$DeliveryRoot = Join-Path $ProjectRoot "deliverables\$Version"
if ((Test-Path -LiteralPath $BuildRoot) -or (Test-Path -LiteralPath $DeliveryRoot)) {
    throw "Build or delivery output already exists. Increase the product version before rebuilding."
}
New-Item -ItemType Directory -Path $BuildDist, $BuildWork, $BuildSpec | Out-Null

# Run the existing frontend and backend checks before packaging verified source.
Push-Location $FrontendDir
try {
    & npm test
    if ($LASTEXITCODE -ne 0) { throw "Frontend tests failed" }
    & npm run build
    if ($LASTEXITCODE -ne 0) { throw "Frontend build failed" }
}
finally {
    Pop-Location
}
Push-Location $ProjectRoot
try {
    $env:PYTHONPATH = $BackendDir
    & $Python -m pytest (Join-Path $BackendDir "tests") -q
    if ($LASTEXITCODE -ne 0) { throw "Backend tests failed" }
}
finally {
    Pop-Location
}

# Keep application code and frontend assets in the program payload; dependencies live in runtime.
& $Python -m PyInstaller `
    --name "AI_Customer_App" `
    --icon $AppIcon `
    --onedir `
    --optimize 2 `
    --contents-directory "runtime" `
    --distpath $BuildDist `
    --workpath (Join-Path $BuildWork "app") `
    --specpath $BuildSpec `
    --paths $BackendDir `
    --paths $ProjectRoot `
    --additional-hooks-dir (Join-Path $ProjectRoot "packaging\hooks") `
    --exclude-module patchright `
    --exclude-module torch `
    --exclude-module torchaudio `
    --exclude-module torchcodec `
    --exclude-module transformers `
    --exclude-module safetensors `
    --exclude-module voxcpm `
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
    $AppLauncher
if ($LASTEXITCODE -ne 0) { throw "Application packaging failed" }
$PackagedApp = Join-Path $BuildDist "AI_Customer_App"
if (Test-Path -LiteralPath (Join-Path $PackagedApp "runtime\patchright")) {
    throw "Patchright leaked into the main package; inspect PyInstaller hooks."
}
foreach ($OptionalRuntime in @("torch", "torchaudio", "torchcodec", "transformers", "safetensors", "voxcpm")) {
    if (Test-Path -LiteralPath (Join-Path $PackagedApp "runtime\$OptionalRuntime")) {
        throw "$OptionalRuntime leaked into the main package; keep it in the environment package."
    }
}
if (-not (Test-Path -LiteralPath (Join-Path $PackagedApp "runtime\playwright\driver\node.exe") -PathType Leaf)) {
    throw "Playwright driver is missing from the packaged runtime."
}

# The stable entrypoint only updates, validates the environment, and starts the application.
& $Python -m PyInstaller `
    --name "AI_Customer" `
    --icon $AppIcon `
    --onefile `
    --windowed `
    --optimize 2 `
    --distpath $BuildDist `
    --workpath (Join-Path $BuildWork "bootstrap") `
    --specpath $BuildSpec `
    --paths $BackendDir `
    --add-data "$AppIcon;." `
    $StableLauncher
if ($LASTEXITCODE -ne 0) { throw "Stable launcher packaging failed" }

# Assemble the two customer ZIPs: remotely updated program and one-time environment.
$SchemaVersion = [int](& $Python -c "import sys; sys.path.insert(0, r'$BackendDir'); from app.migrations import latest_version; print(latest_version())")
$AssemblyArguments = @(
    $DeliveryAssembler,
    "--packaged-app", $PackagedApp,
    "--stable-launcher", (Join-Path $BuildDist "AI_Customer.exe"),
    "--staging-root", $StagingRoot,
    "--delivery-root", $DeliveryRoot,
    "--version", $Version,
    "--environment-version", $EnvironmentVersion,
    "--schema-version", $SchemaVersion,
    "--readme", $Readme
)
if ($ProgramOnly) {
    $AssemblyArguments += "--program-only"
}
else {
    $AssemblyArguments += @(
        "--crawler-python-root", $CrawlerPythonRoot,
        "--crawler-root", $CrawlerRoot,
        "--cloakbrowser-root", $CloakBrowserDir,
        "--vox-component-root", $VoxComponentPath,
        "--voice-models-root", $VoiceModelsPath
    )
}
$AssemblyOutput = @(& $Python @AssemblyArguments)
if ($LASTEXITCODE -ne 0) { throw "Portable delivery assembly failed" }
$Assembly = ($AssemblyOutput -join "") | ConvertFrom-Json

if ($ProgramOnly) {
    Write-Host "Program-only delivery created:"
    Write-Host "  Program: $($Assembly.program_zip)"
    Write-Host "Run it with the existing Environment $EnvironmentVersion files."
}
else {
    Write-Host "Two-ZIP delivery created:"
    Write-Host "  Program: $($Assembly.program_zip)"
    Write-Host "  Environment: $($Assembly.environment_zip)"
    Write-Host "Customer steps: extract Program ZIP, merge Environment ZIP into it, then double-click AI_Customer.exe."
    Write-Host "Remote publishing uses the Program ZIP only."
}
