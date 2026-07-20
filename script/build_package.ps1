param(
    [string]$Version = "",
    [string]$EnvironmentVersion = "1.0.2",
    [string]$MyCrawlerComponentPath = "",
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
$AppLauncher = Join-Path $ProjectRoot "packaging\ai_customer_launcher.py"
$StableLauncher = Join-Path $ProjectRoot "packaging\ai_customer_bootstrap.py"
$AppIcon = Join-Path $ProjectRoot "packaging\ai-customer-icon.ico"
$DeliveryAssembler = Join-Path $ProjectRoot "script\assemble_delivery.py"
$Readme = Join-Path $ProjectRoot "packaging\PACKAGE_README.txt"
$Python = Join-Path $BackendDir ".venv\Scripts\python.exe"

# Validate local dependencies only; this script never installs missing packages implicitly.
foreach ($RequiredFile in @($Python, $AppLauncher, $StableLauncher, $AppIcon, $DeliveryAssembler, $Readme)) {
    if (-not (Test-Path -LiteralPath $RequiredFile -PathType Leaf)) {
        throw "Required build file is missing: $RequiredFile"
    }
}
& $Python -c "import nuitka"
if ($LASTEXITCODE -ne 0) {
    throw "Nuitka is missing from backend/.venv. Install backend/requirements-dev.txt first."
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
$CloakBrowserDir = ""
if (-not $ProgramOnly) {
    if (-not $MyCrawlerComponentPath) {
        $ComponentRoot = Join-Path $ProjectRoot "dist\components"
        $MyCrawlerComponentPath = @(Get-ChildItem -LiteralPath $ComponentRoot -Directory -ErrorAction SilentlyContinue | Where-Object {
            $InfoPath = Join-Path $_.FullName "component-info.json"
            if (-not (Test-Path -LiteralPath $InfoPath -PathType Leaf)) { return $false }
            $Info = Get-Content -LiteralPath $InfoPath -Raw -Encoding utf8 | ConvertFrom-Json
            [string]$Info.component -eq "mycrawler" -and
            [string]$Info.compiler -eq "nuitka" -and
            [string]$Info.entrypoint -eq "MyCrawler.exe" -and
            (Test-Path -LiteralPath (Join-Path $_.FullName "MyCrawler.exe") -PathType Leaf)
        } | Sort-Object LastWriteTime -Descending | Select-Object -First 1)[0].FullName
    }
    if (-not $MyCrawlerComponentPath -or -not (Test-Path -LiteralPath (Join-Path $MyCrawlerComponentPath "MyCrawler.exe") -PathType Leaf)) {
        throw "A completed Nuitka MyCrawler component is required. Run script/build_mycrawler_component.ps1 first."
    }
    $CloakBrowserPath = (& $Python -c "import cloakbrowser; print(cloakbrowser.ensure_binary())").Trim()
    if (-not (Test-Path -LiteralPath $CloakBrowserPath -PathType Leaf)) {
        throw "CloakBrowser is missing. Run backend/.venv/Scripts/python.exe -m cloakbrowser install first."
    }
    $CloakBrowserDir = Split-Path -Parent $CloakBrowserPath
    if (-not $VoiceModelsPath) { $VoiceModelsPath = Join-Path $ProjectRoot "data\voice_models" }
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
}

# Keep timestamped intermediate files; customers only receive deliverables/<version>.
$Stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$BuildRoot = Join-Path $ProjectRoot "output\build_${Version}_$Stamp"
$BuildDist = Join-Path $BuildRoot "dist"
$StagingRoot = Join-Path $ProjectRoot "output\stage_$Stamp"
$DeliveryRoot = Join-Path $ProjectRoot "deliverables\$Version"
if ((Test-Path -LiteralPath $BuildRoot) -or (Test-Path -LiteralPath $DeliveryRoot)) {
    throw "Build or delivery output already exists. Increase the product version before rebuilding."
}
New-Item -ItemType Directory -Path $BuildDist | Out-Null

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

# Compile the complete Python application to native code; data files stay beside the standalone binary.
$PreviousPythonPath = $env:PYTHONPATH
$env:PYTHONPATH = "$BackendDir;$ProjectRoot"
try {
    & $Python -m nuitka `
        --mode=standalone `
        --output-dir=$BuildDist `
        --output-filename=AI_Customer_App.exe `
        --windows-console-mode=force `
        --windows-icon-from-ico=$AppIcon `
        --assume-yes-for-downloads `
        --python-flag=no_docstrings `
        --include-package=app `
        --include-package=tools `
        --nofollow-import-to=patchright `
        --nofollow-import-to=torch `
        --nofollow-import-to=torchaudio `
        --nofollow-import-to=torchcodec `
        --nofollow-import-to=transformers `
        --nofollow-import-to=safetensors `
        --nofollow-import-to=voxcpm `
        --include-data-dir="$FrontendDir\dist=frontend_dist" `
        --include-data-dir="$BackendDir\app\video_engine\resource=app/video_engine/resource" `
        --include-data-dir="$BackendDir\app\video_engine\services\data=app/video_engine/services/data" `
        --include-data-files="$BackendDir\app\video_engine\config.default.toml=app/video_engine/config.default.toml" `
        --include-data-files="$BackendDir\app\video_engine\LICENSE=app/video_engine/LICENSE" `
        --include-data-files="$BackendDir\app\publish_engine\utils\stealth.min.js=app/publish_engine/utils/stealth.min.js" `
        --include-data-files="$BackendDir\app\publish_engine\LICENSE=app/publish_engine/LICENSE" `
        --include-package-data=cloakbrowser `
        --include-package-data=moviepy `
        --include-package-data=imageio_ffmpeg `
        --include-package-data=faster_whisper `
        --include-package-data=ctranslate2 `
        $AppLauncher
}
finally {
    $env:PYTHONPATH = $PreviousPythonPath
}
if ($LASTEXITCODE -ne 0) { throw "Application Nuitka compilation failed" }
$PackagedApp = Join-Path $BuildDist "ai_customer_launcher.dist"
if (-not (Test-Path -LiteralPath (Join-Path $PackagedApp "AI_Customer_App.exe") -PathType Leaf)) {
    throw "Nuitka application executable was not created"
}
if (-not (Test-Path -LiteralPath (Join-Path $PackagedApp "playwright\driver\node.exe") -PathType Leaf)) {
    throw "Playwright driver is missing from the Nuitka application runtime"
}

# Compile the stable updater as a single native Windows executable.
$PreviousPythonPath = $env:PYTHONPATH
$env:PYTHONPATH = $BackendDir
try {
    & $Python -m nuitka `
        --mode=onefile `
        --output-dir=$BuildDist `
        --output-filename=AI_Customer.exe `
        --windows-console-mode=disable `
        --windows-icon-from-ico=$AppIcon `
        --assume-yes-for-downloads `
        --python-flag=no_docstrings `
        --enable-plugin=tk-inter `
        --include-data-files="$AppIcon=ai-customer-icon.ico" `
        $StableLauncher
}
finally {
    $env:PYTHONPATH = $PreviousPythonPath
}
if ($LASTEXITCODE -ne 0) { throw "Stable launcher Nuitka compilation failed" }

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
        "--crawler-component-root", $MyCrawlerComponentPath,
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
