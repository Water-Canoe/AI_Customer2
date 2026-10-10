param(
    [string]$Version = "",
    [string]$EnvironmentVersion = "1.1.0",
    [string]$MyCrawlerComponentPath = "",
    [string]$VoxComponentPath = "",
    [string]$VoiceModelsPath = "",
    [switch]$ProgramOnly,
    [switch]$IncludeVoice
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "internal\_version.ps1")

# Resolve all paths from the repository root so the caller's location cannot change the build.
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$BackendDir = Join-Path $ProjectRoot "backend"
$FrontendDir = Join-Path $ProjectRoot "frontend"
$AppLauncher = Join-Path $ProjectRoot "packaging\ai_customer_launcher.py"
$StableLauncher = Join-Path $ProjectRoot "packaging\ai_customer_bootstrap.py"
$AppIcon = Join-Path $ProjectRoot "packaging\ai-customer-icon.ico"
$DeliveryAssembler = Join-Path $ProjectRoot "script\internal\assemble_delivery.py"
$Readme = Join-Path $ProjectRoot "packaging\PACKAGE_README.txt"
$Python = Join-Path $BackendDir ".venv\Scripts\python.exe"
if ($ProgramOnly -and $IncludeVoice) { throw "ProgramOnly and IncludeVoice cannot be combined." }

# 先核对版本与交付目录，避免完整编译后才发现不能覆盖发行文件。
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) { throw "Required build file is missing: $Python" }
# Keep one version source so the EXE and release manifest cannot disagree.
$SourceVersionOutput = & $Python -c "import sys; sys.path.insert(0, sys.argv[1]); from app.version import APP_VERSION; print(APP_VERSION)" $BackendDir
if ($LASTEXITCODE -ne 0) { throw "Cannot read backend/app/version.py." }
$SourceVersion = ([string]$SourceVersionOutput).Trim()
if (-not $Version) {
    $Version = $SourceVersion
}
elseif ($Version -ne $SourceVersion) {
    throw "Version must match backend/app/version.py ($SourceVersion)"
}
Assert-SemVer $Version "Version"
Assert-SemVer $EnvironmentVersion "EnvironmentVersion"
$DeliveryRoot = Join-Path $ProjectRoot "deliverables\$Version"
if (Test-Path -LiteralPath $DeliveryRoot) {
    throw "Delivery output already exists: $DeliveryRoot. Update backend/app/version.py before building a new release; existing releases are never overwritten."
}
$CloakBrowserDir = ""
if (-not $ProgramOnly) {
    if (-not $MyCrawlerComponentPath) {
        $ComponentRoot = Join-Path $ProjectRoot "dist\components"
        $MyCrawlerCandidates = @(Get-ChildItem -LiteralPath $ComponentRoot -Directory -ErrorAction SilentlyContinue | Where-Object {
            $InfoPath = Join-Path $_.FullName "component-info.json"
            if (-not (Test-Path -LiteralPath $InfoPath -PathType Leaf)) { return $false }
            $Info = Get-Content -LiteralPath $InfoPath -Raw -Encoding utf8 | ConvertFrom-Json
            [string]$Info.component -eq "mycrawler" -and
            [string]$Info.compiler -eq "nuitka" -and
            [string]$Info.entrypoint -eq "MyCrawler.exe" -and
            [string]$Info.source_revision -match '^[0-9a-f]{40,64}$' -and
            $Info.source_dirty -is [bool] -and
            [string]$Info.entrypoint_sha256 -match '^[0-9a-f]{64}$' -and
            (Test-Path -LiteralPath (Join-Path $_.FullName "MyCrawler.exe") -PathType Leaf)
        })
        if ($MyCrawlerCandidates.Count -gt 1) {
            $Paths = ($MyCrawlerCandidates | ForEach-Object { "  $($_.FullName)" }) -join "`n"
            throw "Multiple valid MyCrawler components were found:`n$Paths`nChoose one directory and pass -MyCrawlerComponentPath '<chosen directory>' to script/build_package.ps1. No component was selected automatically."
        }
        if ($MyCrawlerCandidates.Count -eq 1) { $MyCrawlerComponentPath = $MyCrawlerCandidates[0].FullName }
    }
    $MyCrawlerInfoPath = if ($MyCrawlerComponentPath) { Join-Path $MyCrawlerComponentPath "component-info.json" } else { "" }
    $MyCrawlerInfo = if ($MyCrawlerInfoPath -and (Test-Path -LiteralPath $MyCrawlerInfoPath -PathType Leaf)) {
        Get-Content -LiteralPath $MyCrawlerInfoPath -Raw -Encoding utf8 | ConvertFrom-Json
    } else { $null }
    if (
        -not $MyCrawlerInfo -or
        [string]$MyCrawlerInfo.component -ne "mycrawler" -or
        [string]$MyCrawlerInfo.compiler -ne "nuitka" -or
        [string]$MyCrawlerInfo.entrypoint -ne "MyCrawler.exe" -or
        [string]$MyCrawlerInfo.source_revision -notmatch '^[0-9a-f]{40,64}$' -or
        $MyCrawlerInfo.source_dirty -isnot [bool] -or
        [string]$MyCrawlerInfo.entrypoint_sha256 -notmatch '^[0-9a-f]{64}$' -or
        -not (Test-Path -LiteralPath (Join-Path $MyCrawlerComponentPath "MyCrawler.exe") -PathType Leaf)
    ) {
        throw "A completed Nuitka MyCrawler component is required. Run script/internal/build_mycrawler_component.ps1 first."
    }
    # 只定位已有浏览器，禁止在打包检查中隐式下载或触发后台更新。
    $CloakBrowserPath = (& $Python -c "import os, cloakbrowser; print(os.getenv('CLOAKBROWSER_BINARY_PATH', '').strip() or cloakbrowser.binary_info()['binary_path'])").Trim()
    if ($LASTEXITCODE -ne 0) { throw "Cannot inspect the installed CloakBrowser binary." }
    if (-not (Test-Path -LiteralPath $CloakBrowserPath -PathType Leaf)) {
        throw "CloakBrowser is missing. Run backend/.venv/Scripts/python.exe -m cloakbrowser install first."
    }
    $CloakBrowserDir = Split-Path -Parent $CloakBrowserPath
}

# 默认首装只准备工作台基础环境；显式选择后才检查语音运行时和模型。
if ($IncludeVoice) {
    if (-not $VoiceModelsPath) { $VoiceModelsPath = Join-Path $ProjectRoot "data\voice_models" }
    if (-not (Test-Path -LiteralPath (Join-Path $VoiceModelsPath "VoxCPM2") -PathType Container)) {
        throw "VoxCPM2 model files are missing: $VoiceModelsPath\VoxCPM2"
    }
    if (-not $VoxComponentPath) {
        $ComponentRoot = Join-Path $ProjectRoot "dist\components"
        $VoxCandidates = @(Get-ChildItem -LiteralPath $ComponentRoot -Directory -ErrorAction SilentlyContinue | Where-Object {
            $InfoPath = Join-Path $_.FullName "component-info.json"
            if (-not (Test-Path -LiteralPath $InfoPath -PathType Leaf)) { return $false }
            $Info = Get-Content -LiteralPath $InfoPath -Raw -Encoding utf8 | ConvertFrom-Json
            [string]$Info.component -eq "voxcpm2" -and
            [string]$Info.compiler -eq "nuitka" -and
            [string]$Info.runtime_packager -eq "pyinstaller" -and
            [string]$Info.entrypoint -eq "VoxCPM_Runtime.exe" -and
            [string]$Info.native_module -match '^r/ai_customer_voxcpm_native[^/]*\.pyd$' -and
            [string]$Info.source_revision -match '^[0-9a-f]{40,64}$' -and
            $Info.source_dirty -is [bool] -and
            [string]$Info.entrypoint_sha256 -match '^[0-9a-f]{64}$' -and
            [string]$Info.native_module_sha256 -match '^[0-9a-f]{64}$' -and
            (Test-Path -LiteralPath (Join-Path $_.FullName ([string]$Info.native_module)) -PathType Leaf) -and
            (Test-Path -LiteralPath (Join-Path $_.FullName "VoxCPM_Runtime.exe") -PathType Leaf)
        })
        if ($VoxCandidates.Count -gt 1) {
            $Paths = ($VoxCandidates | ForEach-Object { "  $($_.FullName)" }) -join "`n"
            throw "Multiple valid VoxCPM2 components were found:`n$Paths`nChoose one directory and pass -VoxComponentPath '<chosen directory>' with -IncludeVoice. No component was selected automatically."
        }
        if ($VoxCandidates.Count -eq 1) { $VoxComponentPath = $VoxCandidates[0].FullName }
    }
    $VoxInfoPath = if ($VoxComponentPath) { Join-Path $VoxComponentPath "component-info.json" } else { "" }
    $VoxInfo = if ($VoxInfoPath -and (Test-Path -LiteralPath $VoxInfoPath -PathType Leaf)) {
        Get-Content -LiteralPath $VoxInfoPath -Raw -Encoding utf8 | ConvertFrom-Json
    } else { $null }
    $VoxNativeModule = if ($VoxInfo) { [string]$VoxInfo.native_module } else { "" }
    if (
        -not $VoxInfo -or
        [string]$VoxInfo.component -ne "voxcpm2" -or
        [string]$VoxInfo.compiler -ne "nuitka" -or
        [string]$VoxInfo.runtime_packager -ne "pyinstaller" -or
        [string]$VoxInfo.entrypoint -ne "VoxCPM_Runtime.exe" -or
        $VoxNativeModule -notmatch '^r/ai_customer_voxcpm_native[^/]*\.pyd$' -or
        [string]$VoxInfo.source_revision -notmatch '^[0-9a-f]{40,64}$' -or
        $VoxInfo.source_dirty -isnot [bool] -or
        [string]$VoxInfo.entrypoint_sha256 -notmatch '^[0-9a-f]{64}$' -or
        [string]$VoxInfo.native_module_sha256 -notmatch '^[0-9a-f]{64}$' -or
        -not (Test-Path -LiteralPath (Join-Path $VoxComponentPath $VoxNativeModule) -PathType Leaf) -or
        -not (Test-Path -LiteralPath (Join-Path $VoxComponentPath "VoxCPM_Runtime.exe") -PathType Leaf)
    ) {
        throw "A completed Nuitka-entry VoxCPM2 component is required. Run script/internal/build_voxcpm_component.ps1 first."
    }
}

# Validate local dependencies only; this script never installs missing packages implicitly.
foreach ($RequiredFile in @($AppLauncher, $StableLauncher, $AppIcon, $DeliveryAssembler, $Readme)) {
    if (-not (Test-Path -LiteralPath $RequiredFile -PathType Leaf)) {
        throw "Required build file is missing: $RequiredFile"
    }
}
& $Python -c "import nuitka"
if ($LASTEXITCODE -ne 0) {
    throw "Nuitka is missing from backend/.venv. Install backend/requirements-dev.txt first."
}
$Npm = (Get-Command npm.cmd -ErrorAction Stop).Source

# Keep timestamped intermediate files; customers only receive deliverables/<version>.
$Stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$BuildRoot = Join-Path $ProjectRoot "output\build_${Version}_$Stamp"
$BuildDist = Join-Path $BuildRoot "dist"
$StagingRoot = Join-Path $ProjectRoot "output\stage_$Stamp"
if (Test-Path -LiteralPath $BuildRoot) {
    throw "Build output already exists: $BuildRoot. Wait until the existing build has finished before retrying."
}
New-Item -ItemType Directory -Path $BuildDist | Out-Null

# Run the existing frontend and backend checks before packaging verified source.
Push-Location $FrontendDir
try {
    & $Npm test
    if ($LASTEXITCODE -ne 0) { throw "Frontend tests failed" }
    & $Npm run build
    if ($LASTEXITCODE -ne 0) { throw "Frontend build failed" }
}
finally {
    Pop-Location
}
$PreviousTestPythonPath = $env:PYTHONPATH
Push-Location $ProjectRoot
try {
    $env:PYTHONPATH = $BackendDir
    & $Python -m pytest (Join-Path $BackendDir "tests") -q
    if ($LASTEXITCODE -ne 0) { throw "Backend tests failed" }
}
finally {
    Pop-Location
    # 打包脚本结束后恢复调用者环境，避免影响后续开发命令。
    $env:PYTHONPATH = $PreviousTestPythonPath
}

# Compile the complete Python application to native code; data files stay beside the standalone binary.
$SourceRevision = ([string](& git -C $ProjectRoot rev-parse HEAD)).Trim()
if ($LASTEXITCODE -ne 0 -or $SourceRevision -notmatch '^[0-9a-f]{40,64}$') {
    throw "Cannot record the Git source revision. Build from the project Git checkout."
}
$SourceChanges = @(& git -C $ProjectRoot status --porcelain --untracked-files=normal)
if ($LASTEXITCODE -ne 0) { throw "Cannot inspect the Git working-tree state." }
$SourceDirty = $SourceChanges.Count -gt 0
$PreviousPythonPath = $env:PYTHONPATH
$env:PYTHONPATH = "$BackendDir;$ProjectRoot"
try {
    & $Python -m nuitka `
        --mode=standalone `
        --output-dir=$BuildDist `
        --output-filename=AI_Customer_App.exe `
        --windows-console-mode=disable `
        --windows-icon-from-ico=$AppIcon `
        --assume-yes-for-downloads `
        --python-flag=no_docstrings `
        --include-package=app `
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
        --include-data-files="$BackendDir\app\vendor\douyin_spider\utils\mstoken_profile.json=app/vendor/douyin_spider/utils/mstoken_profile.json" `
        --include-data-files="$BackendDir\app\vendor\douyin_spider\utils\mstoken_common_profile.json=app/vendor/douyin_spider/utils/mstoken_common_profile.json" `
        --include-data-files="$BackendDir\app\vendor\douyin_spider\SOURCE.md=app/vendor/douyin_spider/SOURCE.md" `
        --include-data-files="$BackendDir\app\vendor\douyin_spider\UPSTREAM_README.md=app/vendor/douyin_spider/UPSTREAM_README.md" `
        --include-data-dir="$BackendDir\app\vendor\spider_xhs\xhs_utils\xhs_core\js=app/vendor/spider_xhs/xhs_utils/xhs_core/js" `
        --include-data-files="$BackendDir\app\vendor\spider_xhs\xhs_utils\xhs_pc\js\reference_profile.json=app/vendor/spider_xhs/xhs_utils/xhs_pc/js/reference_profile.json" `
        --include-data-files="$BackendDir\app\vendor\spider_xhs\SOURCE.md=app/vendor/spider_xhs/SOURCE.md" `
        --include-data-files="$BackendDir\app\vendor\spider_xhs\UPSTREAM_README.md=app/vendor/spider_xhs/UPSTREAM_README.md" `
        --include-package-data=curl_cffi `
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

# 使用独立临时数据目录检查刚编译的 EXE，避免触及业务数据库或真实私信账号。
$PreviousSmokeData = $env:AI_CUSTOMER_DATA_DIR
$PreviousSmokeDb = $env:AI_CUSTOMER_DB
try {
    $env:AI_CUSTOMER_DATA_DIR = Join-Path $BuildRoot "smoke-data"
    $env:AI_CUSTOMER_DB = Join-Path $env:AI_CUSTOMER_DATA_DIR "ai_customer.sqlite3"
    & (Join-Path $PackagedApp "AI_Customer_App.exe") --smoke-test
    if ($LASTEXITCODE -ne 0) { throw "Compiled application startup/dependency smoke test failed." }
}
finally {
    $env:AI_CUSTOMER_DATA_DIR = $PreviousSmokeData
    $env:AI_CUSTOMER_DB = $PreviousSmokeDb
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
        --include-module=app.services.desktop_update `
        --include-data-files="$AppIcon=app/services/ai-customer-icon.ico" `
        $StableLauncher
}
finally {
    $env:PYTHONPATH = $PreviousPythonPath
}
if ($LASTEXITCODE -ne 0) { throw "Stable launcher Nuitka compilation failed" }

# 组装单包首装 Core 与日常更新 Program；语音环境仅在明确选择后单独交付。
$SchemaOutput = & $Python -c "import sys; sys.path.insert(0, sys.argv[1]); from app.migrations import latest_version; print(latest_version())" $BackendDir
if ($LASTEXITCODE -ne 0) { throw "Cannot read the current database schema version." }
$SchemaVersion = [int]$SchemaOutput
$AssemblyArguments = @(
    $DeliveryAssembler,
    "--packaged-app", $PackagedApp,
    "--stable-launcher", (Join-Path $BuildDist "AI_Customer.exe"),
    "--staging-root", $StagingRoot,
    "--delivery-root", $DeliveryRoot,
    "--version", $Version,
    "--environment-version", $EnvironmentVersion,
    "--schema-version", $SchemaVersion,
    "--source-revision", $SourceRevision,
    "--readme", $Readme
)
if ($SourceDirty) { $AssemblyArguments += "--source-dirty" }
if ($ProgramOnly) {
    $AssemblyArguments += "--program-only"
}
else {
    $AssemblyArguments += @(
        "--crawler-component-root", $MyCrawlerComponentPath,
        "--cloakbrowser-root", $CloakBrowserDir
    )
    if ($IncludeVoice) { $AssemblyArguments += @(
        "--include-voice",
        "--vox-component-root", $VoxComponentPath,
        "--voice-models-root", $VoiceModelsPath
    ) }
}
$AssemblyOutput = @(& $Python @AssemblyArguments)
if ($LASTEXITCODE -ne 0) { throw "Portable delivery assembly failed" }
$Assembly = ($AssemblyOutput -join "") | ConvertFrom-Json

if ($ProgramOnly) {
    Write-Host "Program-only delivery created:"
    Write-Host "  Program: $($Assembly.program_zip)"
    Write-Host "Install this update over an existing Core environment $EnvironmentVersion."
}
else {
    Write-Host "Portable delivery created:"
    Write-Host "  Core: $($Assembly.core_zip)"
    Write-Host "  Program: $($Assembly.program_zip)"
    if ($Assembly.voice_zip) { Write-Host "  Optional voice: $($Assembly.voice_zip)" }
    Write-Host "First install: extract the Core ZIP and double-click AI_Customer.exe."
    Write-Host "Remote publishing uses the Program ZIP only."
}
