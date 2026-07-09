param(
    [Parameter(Mandatory = $true)]
    [string]$Version,
    [string]$InstallRoot = (Join-Path $env:LOCALAPPDATA "AI_Customer")
)

$ErrorActionPreference = "Stop"

# Validate the requested immutable version before switching the stable launcher target.
if ($Version -notmatch '^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$') {
    throw "Version format is invalid"
}
$Executable = Join-Path $InstallRoot "versions\$Version\AI_Customer.exe"
if (-not (Test-Path -LiteralPath $Executable -PathType Leaf)) {
    throw "Version $Version is not installed or is incomplete"
}

# Write the next pointer first, then replace the single current-version file.
$Current = [ordered]@{
    version = $Version
    switched_at = (Get-Date).ToString("yyyy-MM-ddTHH:mm:ssK")
}
$CurrentPath = Join-Path $InstallRoot "current-version.json"
$TemporaryCurrentPath = Join-Path $InstallRoot "current-version.next.json"
$Current | ConvertTo-Json | Set-Content -LiteralPath $TemporaryCurrentPath -Encoding utf8
Move-Item -LiteralPath $TemporaryCurrentPath -Destination $CurrentPath -Force

Write-Host "Current version switched to $Version"
Write-Host "Persistent data was not changed: $InstallRoot\data"
