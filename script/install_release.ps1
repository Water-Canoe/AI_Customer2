param(
    [Parameter(Mandatory = $true)]
    [string]$ReleasePath,
    [string]$InstallRoot = (Join-Path $env:LOCALAPPDATA "AI_Customer")
)

$ErrorActionPreference = "Stop"

# Resolve and validate the release manifest before changing the installation.
$Release = Resolve-Path -LiteralPath $ReleasePath
$ManifestPath = Join-Path $Release "release-manifest.json"
if (-not (Test-Path -LiteralPath $ManifestPath -PathType Leaf)) {
    throw "release-manifest.json is missing"
}
$Manifest = Get-Content -LiteralPath $ManifestPath -Raw -Encoding utf8 | ConvertFrom-Json
$Version = [string]$Manifest.version
if ($Version -notmatch '^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$') {
    throw "Release version is invalid"
}

# Reject undeclared files before verifying size and SHA-256 for every declared file.
$DeclaredPaths = @{}
foreach ($Item in $Manifest.files) {
    $DeclaredPaths[([string]$Item.path).Replace('\', '/')] = $true
}
$ReleasePrefix = $Release.Path.TrimEnd('\') + '\'
$ActualFiles = Get-ChildItem -LiteralPath $Release -Recurse -File | Where-Object { $_.FullName -ne $ManifestPath }
if ($ActualFiles.Count -ne $Manifest.files.Count) {
    throw "Release contains missing or undeclared files"
}
foreach ($File in $ActualFiles) {
    $Relative = $File.FullName.Substring($ReleasePrefix.Length).Replace('\', '/')
    if (-not $DeclaredPaths.ContainsKey($Relative)) {
        throw "Release contains undeclared file: $Relative"
    }
}

# Verify size and SHA-256 for every declared file.
foreach ($Item in $Manifest.files) {
    $Relative = [string]$Item.path
    if ([System.IO.Path]::IsPathRooted($Relative) -or $Relative.Split('/') -contains '..') {
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
}

# Create stable data and immutable version folders without deleting older releases.
$VersionsRoot = Join-Path $InstallRoot "versions"
$TargetVersion = Join-Path $VersionsRoot $Version
if (Test-Path -LiteralPath $TargetVersion) {
    throw "Version $Version is already installed; switch to it or build a new version"
}
New-Item -ItemType Directory -Path $InstallRoot, $VersionsRoot, (Join-Path $InstallRoot "data") -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $Release "app") -Destination $TargetVersion -Recurse

# Replace only the single stable launcher, then atomically switch the current version file.
Copy-Item -LiteralPath (Join-Path $Release "AI_Customer.exe") -Destination (Join-Path $InstallRoot "AI_Customer.exe") -Force
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
