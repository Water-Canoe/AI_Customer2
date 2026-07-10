param(
    [Parameter(Mandatory = $true)]
    [string]$ReleasePath,
    [string]$InstallRoot = (Join-Path $env:LOCALAPPDATA "AI_Customer")
)

$ErrorActionPreference = "Stop"

# Verify the declared files; extra runtime files in a copied release are intentionally ignored.
$Release = Resolve-Path -LiteralPath $ReleasePath
$ManifestPath = Join-Path $Release "release-manifest.json"
if (-not (Test-Path -LiteralPath $ManifestPath -PathType Leaf)) {
    throw "release-manifest.json is missing"
}
$Manifest = Get-Content -LiteralPath $ManifestPath -Raw -Encoding utf8 | ConvertFrom-Json
$Version = [string]$Manifest.version
if ($Manifest.format -ne 1 -or $Manifest.product -ne "AI Customer Desktop" -or $Version -notmatch '^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$') {
    throw "Release manifest is invalid"
}

$DeclaredPaths = @{}
foreach ($Item in $Manifest.files) {
    $Relative = ([string]$Item.path).Replace('\', '/')
    if (-not $Relative -or [System.IO.Path]::IsPathRooted($Relative) -or $Relative.Split('/') -contains '..' -or $DeclaredPaths.ContainsKey($Relative)) {
        throw "Unsafe release path: $Relative"
    }
    $Source = Join-Path $Release ($Relative.Replace('/', '\'))
    if (-not (Test-Path -LiteralPath $Source -PathType Leaf)) {
        throw "Release file is missing: $Relative"
    }
    $File = Get-Item -LiteralPath $Source
    if ($File.Length -ne [long]$Item.size) {
        throw "Release file size mismatch: $Relative"
    }
    $Hash = (Get-FileHash -LiteralPath $Source -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($Hash -ne ([string]$Item.sha256).ToLowerInvariant()) {
        throw "Release file hash mismatch: $Relative"
    }
    $DeclaredPaths[$Relative] = $Source
}
if (-not $DeclaredPaths.ContainsKey("AI_Customer.exe") -or -not $DeclaredPaths.ContainsKey("app/AI_Customer.exe")) {
    throw "Release is missing the launcher or application"
}

# Copy only the immutable app payload into a new version folder.
$VersionsRoot = Join-Path $InstallRoot "versions"
$TargetVersion = Join-Path $VersionsRoot $Version
if (Test-Path -LiteralPath $TargetVersion) {
    throw "Version $Version is already installed; switch to it or build a new version"
}
New-Item -ItemType Directory -Path $InstallRoot, $VersionsRoot, (Join-Path $InstallRoot "data") -Force | Out-Null
$StagingVersion = Join-Path $VersionsRoot "$Version.staging-$PID"
if (Test-Path -LiteralPath $StagingVersion) {
    throw "An unfinished installation folder already exists; close the app and try again"
}
foreach ($Relative in $DeclaredPaths.Keys) {
    if (-not $Relative.StartsWith("app/")) { continue }
    $Destination = Join-Path $StagingVersion ($Relative.Substring(4).Replace('/', '\'))
    New-Item -ItemType Directory -Path (Split-Path -Parent $Destination) -Force | Out-Null
    Copy-Item -LiteralPath $DeclaredPaths[$Relative] -Destination $Destination
}
if (-not (Test-Path -LiteralPath (Join-Path $StagingVersion "AI_Customer.exe") -PathType Leaf)) {
    throw "Release is missing the application executable"
}
Move-Item -LiteralPath $StagingVersion -Destination $TargetVersion

# Replace one stable launcher and then atomically switch the version pointer.
$TemporaryLauncher = Join-Path $InstallRoot "AI_Customer.next.exe"
Copy-Item -LiteralPath $DeclaredPaths["AI_Customer.exe"] -Destination $TemporaryLauncher -Force
Move-Item -LiteralPath $TemporaryLauncher -Destination (Join-Path $InstallRoot "AI_Customer.exe") -Force
$Current = [ordered]@{
    version = $Version
    switched_at = (Get-Date).ToString("yyyy-MM-ddTHH:mm:ssK")
}
$CurrentPath = Join-Path $InstallRoot "current-version.json"
$TemporaryCurrentPath = Join-Path $InstallRoot "current-version.next.json"
$Current | ConvertTo-Json | Set-Content -LiteralPath $TemporaryCurrentPath -Encoding utf8
Move-Item -LiteralPath $TemporaryCurrentPath -Destination $CurrentPath -Force

Write-Host "Installed version $Version"
Write-Host "Persistent data remains in: $InstallRoot\data"
Write-Host "Start with: $InstallRoot\AI_Customer.exe"
