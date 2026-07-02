$ErrorActionPreference = "Stop"

# Resolve project paths from this script location.
$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$BackendDir = Join-Path $ProjectRoot "backend"
$FrontendDir = Join-Path $ProjectRoot "frontend"
$Launcher = Join-Path $ProjectRoot "packaging\ai_customer_launcher.py"
$Readme = Join-Path $ProjectRoot "packaging\PACKAGE_README.txt"
$Python = Join-Path $BackendDir ".venv\Scripts\python.exe"

# Build a unique package name so old packages do not need to be deleted.
$Stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$PackageName = "AI_Customer_Test_$Stamp"

# Ensure the backend virtual environment exists before packaging.
if (-not (Test-Path $Python)) {
    throw "Backend virtual environment not found: $Python"
}

# Build the production frontend static files.
Push-Location $FrontendDir
try {
    npm run build
}
finally {
    Pop-Location
}

# Install PyInstaller into the project virtual environment when missing.
$oldErrorActionPreference = $ErrorActionPreference
$ErrorActionPreference = "Continue"
& $Python -c "import importlib.util, sys; sys.exit(0 if importlib.util.find_spec('PyInstaller') else 1)"
$hasPyInstaller = $LASTEXITCODE -eq 0
$ErrorActionPreference = $oldErrorActionPreference
if (-not $hasPyInstaller) {
    & $Python -m pip install pyinstaller
}

# Create a one-folder Windows package that includes the frontend dist files.
& $Python -m PyInstaller `
    --name $PackageName `
    --onedir `
    --paths $BackendDir `
    --add-data "$FrontendDir\dist;frontend_dist" `
    $Launcher

# Copy the package usage note next to the generated exe.
$PackageDir = Join-Path $ProjectRoot "dist\$PackageName"
Copy-Item -Path $Readme -Destination (Join-Path $PackageDir "使用说明.txt")
Copy-Item -Path $Readme -Destination (Join-Path $PackageDir "README.txt")

Write-Host "Package created:"
Write-Host "  $PackageDir"
Write-Host "Start with:"
Write-Host "  $PackageDir\$PackageName.exe"
