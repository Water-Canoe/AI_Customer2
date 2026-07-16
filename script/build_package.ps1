param(
    [string]$Version = "",
    [string]$EnvironmentVersion = "1.0.0",
    [string]$VoxComponentPath = "",
    [string]$VoiceModelsPath = ""
)

$ErrorActionPreference = "Stop"
$SemVerPattern = '^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$'

# 所有路径都从仓库根目录计算，避免在不同终端目录执行时打包错文件。
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$BackendDir = Join-Path $ProjectRoot "backend"
$FrontendDir = Join-Path $ProjectRoot "frontend"
$CrawlerRoot = Join-Path $ProjectRoot "MyCrawler"
$AppLauncher = Join-Path $ProjectRoot "packaging\ai_customer_launcher.py"
$StableLauncher = Join-Path $ProjectRoot "packaging\ai_customer_bootstrap.py"
$DeliveryAssembler = Join-Path $ProjectRoot "script\assemble_delivery.py"
$Readme = Join-Path $ProjectRoot "packaging\PACKAGE_README.txt"
$Python = Join-Path $BackendDir ".venv\Scripts\python.exe"
$CrawlerPython = Join-Path $CrawlerRoot ".venv\Scripts\python.exe"
$CrawlerSitePackages = Join-Path $CrawlerRoot ".venv\Lib\site-packages"

# 构建脚本只校验依赖，不会在用户不知情的情况下联网安装。
foreach ($RequiredFile in @($Python, $CrawlerPython, $AppLauncher, $StableLauncher, $DeliveryAssembler, $Readme)) {
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

# 程序版本只有一个来源，避免 EXE 内版本与发布清单不一致。
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

# 中间目录带时间戳且从不删除；客户只看 deliverables/<版本>。
$Stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$BuildRoot = Join-Path $ProjectRoot "output\build_${Version}_$Stamp"
$BuildDist = Join-Path $BuildRoot "dist"
$BuildWork = Join-Path $BuildRoot "work"
$BuildSpec = Join-Path $BuildRoot "spec"
$StagingRoot = Join-Path $BuildRoot "delivery-staging"
$DeliveryRoot = Join-Path $ProjectRoot "deliverables\$Version"
if ((Test-Path -LiteralPath $BuildRoot) -or (Test-Path -LiteralPath $DeliveryRoot)) {
    throw "Build or delivery output already exists. Increase the product version before rebuilding."
}
New-Item -ItemType Directory -Path $BuildDist, $BuildWork, $BuildSpec | Out-Null

# 先通过现有前后端测试，确保交付物来自已验证源码。
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

# 主程序只保留自身代码和前端资源；依赖文件统一落入 runtime。
& $Python -m PyInstaller `
    --name "AI_Customer_App" `
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

# 稳定入口只负责更新、环境校验和启动主程序。
& $Python -m PyInstaller `
    --name "AI_Customer" `
    --onefile `
    --windowed `
    --optimize 2 `
    --distpath $BuildDist `
    --workpath (Join-Path $BuildWork "bootstrap") `
    --specpath $BuildSpec `
    $StableLauncher
if ($LASTEXITCODE -ne 0) { throw "Stable launcher packaging failed" }

# 组装唯一的两个客户 ZIP，程序 ZIP 可远程更新，环境 ZIP 只需首次交付。
$SchemaVersion = [int](& $Python -c "import sys; sys.path.insert(0, r'$BackendDir'); from app.migrations import latest_version; print(latest_version())")
$AssemblyOutput = @(& $Python $DeliveryAssembler `
    --packaged-app $PackagedApp `
    --stable-launcher (Join-Path $BuildDist "AI_Customer.exe") `
    --staging-root $StagingRoot `
    --delivery-root $DeliveryRoot `
    --version $Version `
    --environment-version $EnvironmentVersion `
    --schema-version $SchemaVersion `
    --crawler-python-root $CrawlerPythonRoot `
    --crawler-root $CrawlerRoot `
    --cloakbrowser-root $CloakBrowserDir `
    --vox-component-root $VoxComponentPath `
    --voice-models-root $VoiceModelsPath `
    --readme $Readme)
if ($LASTEXITCODE -ne 0) { throw "Portable delivery assembly failed" }
$Assembly = ($AssemblyOutput -join "") | ConvertFrom-Json

Write-Host "Two-ZIP delivery created:"
Write-Host "  Program: $($Assembly.program_zip)"
Write-Host "  Environment: $($Assembly.environment_zip)"
Write-Host "Customer steps: extract Program ZIP, merge Environment ZIP into it, then double-click AI_Customer.exe."
Write-Host "Remote publishing uses the Program ZIP only."
