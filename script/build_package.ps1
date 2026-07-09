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

# Read the product version from source unless the caller supplied one.
if (-not $Version) {
    $Version = (& $Python -c "import sys; sys.path.insert(0, r'$BackendDir'); from app.version import APP_VERSION; print(APP_VERSION)").Trim()
}
if ($Version -notmatch '^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$') {
    throw "Version must use semantic version format, for example 1.1.0"
}

# Use unique build and release folders so the script never deletes an older package.
$Stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$BuildRoot = Join-Path $ProjectRoot "output\package_build_${Version}_$Stamp"
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
    --contents-directory "runtime" `
    --distpath $BuildDist `
    --workpath (Join-Path $BuildWork "app") `
    --specpath $BuildSpec `
    --paths $BackendDir `
    --add-data "$FrontendDir\dist;frontend_dist" `
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
Copy-Item -LiteralPath (Join-Path $BuildDist "AI_Customer") -Destination (Join-Path $ReleaseDir "app") -Recurse
Copy-Item -LiteralPath (Join-Path $BuildDist "AI_Customer_Launcher.exe") -Destination (Join-Path $ReleaseDir "AI_Customer.exe")
Copy-Item -LiteralPath $Readme -Destination (Join-Path $ReleaseDir "README.txt")
Copy-Item -LiteralPath $Installer -Destination (Join-Path $ReleaseDir "install-release.ps1")
Copy-Item -LiteralPath $Switcher -Destination (Join-Path $ReleaseDir "switch-version.ps1")

# Record every release file hash before writing the manifest itself.
$ReleasePrefix = $ReleaseDir.TrimEnd('\') + '\'
$Files = Get-ChildItem -LiteralPath $ReleaseDir -Recurse -File | ForEach-Object {
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
Write-Host "Install or update with:"
Write-Host "  powershell -ExecutionPolicy Bypass -File `"$ReleaseDir\install-release.ps1`" -ReleasePath `"$ReleaseDir`""
Write-Host "Prepare or publish remotely with:"
Write-Host "  powershell -ExecutionPolicy Bypass -File `"$ProjectRoot\script\publish_release.ps1`" -Version `"$Version`" -ReleasePath `"$ReleaseDir`" -PrivateKeyPath `"<outside-repository-private-key>`" -PrepareOnly"
