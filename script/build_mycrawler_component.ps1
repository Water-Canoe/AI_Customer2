param(
    [string]$Version = "1.0.0"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$CrawlerRoot = Join-Path $ProjectRoot "MyCrawler"
$BackendDir = Join-Path $ProjectRoot "backend"
$Python = Join-Path $CrawlerRoot ".venv\Scripts\python.exe"
$VerifierPython = Join-Path $BackendDir ".venv\Scripts\python.exe"
$Entrypoint = Join-Path $ProjectRoot "packaging\mycrawler_launcher.py"

if ($Version -notmatch '^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$') {
    throw "Version must use semantic version format, for example 1.0.0"
}
foreach ($RequiredFile in @($Python, $VerifierPython, $Entrypoint, (Join-Path $CrawlerRoot "LICENSE"))) {
    if (-not (Test-Path -LiteralPath $RequiredFile -PathType Leaf)) {
        throw "Required MyCrawler build file is missing: $RequiredFile"
    }
}
& $Python -c "import nuitka"
if ($LASTEXITCODE -ne 0) {
    throw "Nuitka is missing from MyCrawler/.venv"
}

$Stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$BuildRoot = Join-Path $ProjectRoot "output\mycrawler_${Version}_$Stamp"
$BuildDist = Join-Path $BuildRoot "dist"
$ComponentDir = Join-Path $ProjectRoot "dist\components\AI_Customer_MyCrawler_${Version}_$Stamp"
if ((Test-Path -LiteralPath $BuildRoot) -or (Test-Path -LiteralPath $ComponentDir)) {
    throw "Unique MyCrawler output already exists; wait one second and retry"
}
New-Item -ItemType Directory -Path $BuildDist, $ComponentDir | Out-Null

$PreviousPythonPath = $env:PYTHONPATH
$env:PYTHONPATH = "$CrawlerRoot;$BackendDir\app\mediacrawler_shims"
try {
    & $Python -m nuitka `
        --mode=standalone `
        --output-dir=$BuildDist `
        --output-filename=MyCrawler.exe `
        --windows-console-mode=force `
        --assume-yes-for-downloads `
        --python-flag=no_docstrings `
        --enable-plugin=no-qt `
        --include-module=sitecustomize `
        --include-package-data=wordcloud `
        --include-data-dir="$CrawlerRoot\libs=libs" `
        --include-data-files="$CrawlerRoot\LICENSE=LICENSE" `
        --nofollow-import-to=pytest `
        --nofollow-import-to=pre_commit `
        $Entrypoint
}
finally {
    $env:PYTHONPATH = $PreviousPythonPath
}
if ($LASTEXITCODE -ne 0) { throw "MyCrawler Nuitka compilation failed" }

$RuntimeSource = Join-Path $BuildDist "mycrawler_launcher.dist"
& robocopy $RuntimeSource $ComponentDir "/E" "/R:2" "/W:1" "/NFL" "/NDL" "/NJH" "/NJS" "/NC" "/NS" | Out-Null
if ($LASTEXITCODE -gt 7) { throw "MyCrawler component copy failed with robocopy exit code $LASTEXITCODE" }
foreach ($RequiredRelativePath in @("MyCrawler.exe", "LICENSE", "playwright\driver\node.exe", "wordcloud\stopwords")) {
    if (-not (Test-Path -LiteralPath (Join-Path $ComponentDir $RequiredRelativePath) -PathType Leaf)) {
        throw "MyCrawler component is missing: $RequiredRelativePath"
    }
}

# A real schema initialization catches missing dynamic modules and package data.
Push-Location $ComponentDir
try {
    & (Join-Path $ComponentDir "MyCrawler.exe") --init_db sqlite
    if ($LASTEXITCODE -ne 0) { throw "Compiled MyCrawler database initialization failed" }
}
finally {
    Pop-Location
}
$DatabasePath = Join-Path $ComponentDir "database\sqlite_tables.db"
if (-not (Test-Path -LiteralPath $DatabasePath -PathType Leaf)) {
    throw "Compiled MyCrawler did not create database/sqlite_tables.db"
}
& $VerifierPython -c "import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); n=c.execute('select count(*) from sqlite_master where type=?',('table',)).fetchone()[0]; c.close(); assert n >= 10, n" $DatabasePath
if ($LASTEXITCODE -ne 0) { throw "Compiled MyCrawler database schema is incomplete" }

# Runtime databases must never enter reusable environment components.
Remove-Item -LiteralPath $DatabasePath
foreach ($Suffix in @("-wal", "-shm")) {
    $Sidecar = "$DatabasePath$Suffix"
    if (Test-Path -LiteralPath $Sidecar -PathType Leaf) { Remove-Item -LiteralPath $Sidecar }
}

$Info = [ordered]@{
    format = 1
    product = "AI Customer Component"
    component = "mycrawler"
    version = $Version
    entrypoint = "MyCrawler.exe"
    compiler = "nuitka"
    built_at = (Get-Date).ToString("yyyy-MM-ddTHH:mm:ssK")
}
$Info | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $ComponentDir "component-info.json") -Encoding utf8
Write-Host "MyCrawler component created: $ComponentDir"
