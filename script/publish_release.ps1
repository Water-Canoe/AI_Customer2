[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Version,
    [Parameter(Mandatory = $true)]
    [string]$ReleasePath,
    [string]$PrivateKeyPath = $env:AI_CUSTOMER_UPDATE_PRIVATE_KEY,
    [string]$PublicKeyPath = "",
    [string]$ServerBaseUrl = "https://tfwqsfaegbdj.sealosbja.site/ai-customer",
    [ValidateSet("stable", "beta")]
    [string]$Channel = "stable",
    [ValidateSet("windows")]
    [string]$Platform = "windows",
    [ValidateSet("x64")]
    [string]$Arch = "x64",
    [string]$MinUpdaterVersion = "1.0.0",
    [string]$Notes = "",
    [ValidateRange(0, 100)]
    [int]$RolloutPercent = 0,
    [switch]$Mandatory,
    [switch]$Enable,
    [switch]$PrepareOnly
)

$ErrorActionPreference = "Stop"
$SemVerPattern = '^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$'

# Resolve trusted local tools and reject missing dependencies.
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Python = Join-Path $ProjectRoot "backend\.venv\Scripts\python.exe"
$SigningTool = Join-Path $ProjectRoot "script\release_signing.py"
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw "Backend virtual environment not found: $Python"
}
if (-not (Test-Path -LiteralPath $SigningTool -PathType Leaf)) {
    throw "Release signing helper not found: $SigningTool"
}
if (-not $PublicKeyPath) {
    $PublicKeyPath = Join-Path $ProjectRoot "packaging\update_signing_public.pem"
}
if (-not (Test-Path -LiteralPath $PublicKeyPath -PathType Leaf)) {
    throw "Trusted release signing public key not found: $PublicKeyPath"
}
& $Python -c "import cryptography"
if ($LASTEXITCODE -ne 0) {
    throw "cryptography is missing from backend/.venv; publishing cannot continue"
}

function Assert-SemVer([string]$Value, [string]$Label) {
    if ($Value -notmatch $SemVerPattern) {
        throw "$Label must use semantic version format, for example 1.2.0 or 1.2.0-beta.1"
    }
}

function Write-Utf8NoBom([string]$Path, [string]$Content) {
    $Encoding = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($Path, $Content, $Encoding)
}

function Invoke-UpdateApi(
    [string]$Method,
    [string]$Path,
    [object]$Body,
    [string]$AdminToken
) {
    $Headers = @{ "x-ai-customer-admin-token" = $AdminToken }
    $Arguments = @{
        Uri = "$($ServerBaseUrl.TrimEnd('/'))$Path"
        Method = $Method
        Headers = $Headers
        ContentType = "application/json; charset=utf-8"
    }
    if ($null -ne $Body) {
        $Arguments.Body = $Body | ConvertTo-Json -Depth 12 -Compress
    }
    try {
        return Invoke-RestMethod @Arguments
    }
    catch {
        $Details = $_.ErrorDetails.Message
        if (-not $Details) { $Details = $_.Exception.Message }
        throw "Update API request failed: $Method $Path`n$Details"
    }
}

function Read-Release([string]$Path, [string]$ExpectedVersion) {
    $Release = Resolve-Path -LiteralPath $Path
    $ManifestPath = Join-Path $Release.Path "release-manifest.json"
    if (-not (Test-Path -LiteralPath $ManifestPath -PathType Leaf)) {
        throw "release-manifest.json is missing"
    }
    $Manifest = Get-Content -LiteralPath $ManifestPath -Raw -Encoding utf8 | ConvertFrom-Json
    if ([int]$Manifest.format -ne 1 -or [string]$Manifest.product -ne "AI Customer Desktop") {
        throw "Release manifest format or product is invalid"
    }
    if ([string]$Manifest.version -ne $ExpectedVersion) {
        throw "Release manifest version does not match -Version"
    }
    if (-not ([string]$Manifest.entrypoint -eq "app/AI_Customer.exe")) {
        throw "Release manifest entrypoint is invalid"
    }
    $SchemaVersion = 0
    if (-not [int]::TryParse([string]$Manifest.schema_version, [ref]$SchemaVersion) -or $SchemaVersion -lt 0) {
        throw "Release schema version is invalid"
    }

    # Reject undeclared files before checking every declared hash.
    $Declared = @{}
    foreach ($Item in @($Manifest.files)) {
        $Relative = ([string]$Item.path).Replace('\', '/')
        $Segments = @($Relative.Split('/'))
        if (-not $Relative -or [System.IO.Path]::IsPathRooted($Relative) -or $Segments -contains ".." -or $Segments -contains "." -or $Segments -contains "") {
            throw "Unsafe release path: $Relative"
        }
        if ($Declared.ContainsKey($Relative)) {
            throw "Duplicate release path: $Relative"
        }
        $Declared[$Relative] = $Item
    }
    if ($Declared.Count -eq 0) {
        throw "Release manifest does not contain files"
    }
    $ReparseEntries = @(Get-ChildItem -LiteralPath $Release.Path -Recurse -Force | Where-Object {
        ($_.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0
    })
    if ($ReparseEntries.Count -gt 0) {
        throw "Release contains a reparse point: $($ReparseEntries[0].FullName)"
    }
    $ReleasePrefix = $Release.Path.TrimEnd('\') + '\'
    $ActualFiles = @(Get-ChildItem -LiteralPath $Release.Path -Recurse -File -Force | Where-Object { $_.FullName -ne $ManifestPath })
    if ($ActualFiles.Count -ne $Declared.Count) {
        throw "Release contains missing or undeclared files"
    }
    foreach ($File in $ActualFiles) {
        if (($File.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Release contains a reparse-point file: $($File.FullName)"
        }
        $Relative = $File.FullName.Substring($ReleasePrefix.Length).Replace('\', '/')
        if (-not $Declared.ContainsKey($Relative)) {
            throw "Release contains undeclared file: $Relative"
        }
    }
    foreach ($Relative in $Declared.Keys) {
        $Item = $Declared[$Relative]
        $Source = Join-Path $Release.Path ($Relative.Replace('/', '\'))
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
    return [pscustomobject]@{
        Path = $Release.Path
        Manifest = $Manifest
        ManifestPath = $ManifestPath
        SchemaVersion = $SchemaVersion
    }
}

function New-ReleaseArchive([string]$SourcePath, [string]$DestinationPath) {
    Add-Type -AssemblyName System.IO.Compression
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $SourcePrefix = $SourcePath.TrimEnd('\') + '\'
    $Files = @(Get-ChildItem -LiteralPath $SourcePath -Recurse -File -Force | Sort-Object FullName)
    $Stream = [System.IO.File]::Open(
        $DestinationPath,
        [System.IO.FileMode]::CreateNew,
        [System.IO.FileAccess]::ReadWrite,
        [System.IO.FileShare]::None
    )
    try {
        $Archive = [System.IO.Compression.ZipArchive]::new(
            $Stream,
            [System.IO.Compression.ZipArchiveMode]::Create,
            $false
        )
        try {
            foreach ($File in $Files) {
                if (($File.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                    throw "Release contains a reparse-point file: $($File.FullName)"
                }
                $EntryName = $File.FullName.Substring($SourcePrefix.Length).Replace('\', '/')
                [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile(
                    $Archive,
                    $File.FullName,
                    $EntryName,
                    [System.IO.Compression.CompressionLevel]::Optimal
                ) | Out-Null
            }
        }
        finally {
            $Archive.Dispose()
        }
    }
    finally {
        $Stream.Dispose()
    }
}

Assert-SemVer $Version "Version"
Assert-SemVer $MinUpdaterVersion "MinUpdaterVersion"
if ($Channel -eq "stable" -and $Version.Contains('-')) {
    throw "stable channel cannot publish a prerelease version"
}
if ($Enable -and -not $Mandatory -and $RolloutPercent -eq 0) {
    throw "-Enable requires a positive -RolloutPercent unless -Mandatory is set"
}
if ($Mandatory -and -not $Enable) {
    throw "-Mandatory requires -Enable"
}
if ($Notes.Length -gt 4000) {
    throw "Notes length exceeds 4000 characters"
}

$Release = Read-Release $ReleasePath $Version
if (-not $PrivateKeyPath) {
    throw "Private key path is required through -PrivateKeyPath or AI_CUSTOMER_UPDATE_PRIVATE_KEY"
}
$PrivateKey = Resolve-Path -LiteralPath $PrivateKeyPath
if (-not (Test-Path -LiteralPath $PrivateKey.Path -PathType Leaf)) {
    throw "Release signing private key is missing"
}
$ProjectPrefix = $ProjectRoot.TrimEnd('\') + '\'
if ($PrivateKey.Path.StartsWith($ProjectPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Release signing private key must be stored outside the project repository"
}

if (-not $PrepareOnly) {
    $ServerUri = [Uri]$ServerBaseUrl
    if ($ServerUri.Scheme -ne "https") {
        throw "ServerBaseUrl must use HTTPS"
    }
    $AdminToken = [string]$env:AI_CUSTOMER_UPDATE_ADMIN_TOKEN
    if (-not $AdminToken -or $AdminToken.Length -lt 32) {
        throw "AI_CUSTOMER_UPDATE_ADMIN_TOKEN is missing or too short"
    }
    if (-not (Get-Command curl.exe -ErrorAction SilentlyContinue)) {
        throw "curl.exe is required for streaming the release upload"
    }
}

# Every publish attempt gets a new workspace and never overwrites old artifacts.
$Stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$PublishDir = Join-Path $ProjectRoot "output\release_publish_${Version}_$Stamp"
if (Test-Path -LiteralPath $PublishDir) {
    throw "Unique publish workspace already exists; wait one second and retry"
}
New-Item -ItemType Directory -Path $PublishDir | Out-Null
$ArchivePath = Join-Path $PublishDir "AI_Customer_${Version}_${Platform}_${Arch}.zip"
$ManifestOutput = Join-Path $PublishDir "update-manifest.json"
$SignatureOutput = Join-Path $PublishDir "update-manifest.sig"
$ResultOutput = Join-Path $PublishDir "publish-result.json"

Write-Host "Valid release verified. Creating archive..."
New-ReleaseArchive $Release.Path $ArchivePath
$Archive = Get-Item -LiteralPath $ArchivePath
$ArchiveHash = (Get-FileHash -LiteralPath $ArchivePath -Algorithm SHA256).Hash.ToLowerInvariant()

if ($PrepareOnly) {
    $ObjectKey = "releases/$Version/AI_Customer_${Version}_${Platform}_${Arch}.zip"
}
else {
    Write-Host "Requesting a short-lived upload URL..."
    $UploadResponse = Invoke-UpdateApi "POST" "/update/admin/upload-url" ([ordered]@{
        version = $Version
        platform = $Platform
        arch = $Arch
    }) $AdminToken
    if ([int]$UploadResponse.code -ne 200 -or -not $UploadResponse.data.objectKey -or -not $UploadResponse.data.uploadUrl) {
        throw "Upload URL response is incomplete"
    }
    $ObjectKey = [string]$UploadResponse.data.objectKey
    $UploadUrl = [string]$UploadResponse.data.uploadUrl
    if (([Uri]$UploadUrl).Scheme -ne "https") {
        throw "Object storage upload URL must use HTTPS"
    }
}

# Sign the exact compact UTF-8 manifest that clients will verify.
$UpdateManifest = [ordered]@{
    format = 1
    product = "AI Customer Desktop"
    version = $Version
    platform = $Platform
    arch = $Arch
    schema_version = $Release.SchemaVersion
    min_updater_version = $MinUpdaterVersion
    published_at = (Get-Date).ToUniversalTime().ToString("o")
    package = [ordered]@{
        object_key = $ObjectKey
        size = $Archive.Length
        sha256 = $ArchiveHash
    }
}
$ManifestText = $UpdateManifest | ConvertTo-Json -Depth 8 -Compress
Write-Utf8NoBom $ManifestOutput $ManifestText
& $Python $SigningTool sign --private-key $PrivateKey.Path --input $ManifestOutput --output $SignatureOutput
if ($LASTEXITCODE -ne 0) {
    throw "Release manifest signing failed"
}
& $Python $SigningTool verify --public-key $PublicKeyPath --input $ManifestOutput --signature $SignatureOutput
if ($LASTEXITCODE -ne 0) {
    throw "Release private key does not match the trusted client public key"
}
$Signature = (Get-Content -LiteralPath $SignatureOutput -Raw -Encoding ascii).Trim()
if ([Convert]::FromBase64String($Signature).Length -ne 64) {
    throw "Generated Ed25519 signature has an invalid length"
}

if ($PrepareOnly) {
    $PreparedResult = [ordered]@{
        version = $Version
        channel = $Channel
        object_key = $ObjectKey
        package_size = $Archive.Length
        sha256 = $ArchiveHash
        prepared_only = $true
        enabled = $false
    }
    Write-Utf8NoBom $ResultOutput ($PreparedResult | ConvertTo-Json -Depth 5)
    Write-Host "Publish artifacts prepared without remote changes:"
    Write-Host "  $PublishDir"
    return
}

Write-Host "Uploading immutable release package..."
& curl.exe --fail-with-body --silent --show-error --connect-timeout 30 --request PUT --upload-file $ArchivePath $UploadUrl
if ($LASTEXITCODE -ne 0) {
    throw "Object storage upload failed; no release metadata was registered"
}

Write-Host "Registering signed release metadata..."
$RegisterResponse = Invoke-UpdateApi "POST" "/update/admin/releases" ([ordered]@{
    channel = $Channel
    manifestText = $ManifestText
    signature = $Signature
    notes = $Notes
}) $AdminToken
if ([int]$RegisterResponse.code -notin @(200, 201)) {
    throw "Release registration failed after upload; preserve $PublishDir for recovery"
}

$Enabled = $false
if ($Enable) {
    Write-Host "Enabling release distribution..."
    $StatusResponse = Invoke-UpdateApi "PUT" "/update/admin/release-status" ([ordered]@{
        version = $Version
        channel = $Channel
        platform = $Platform
        arch = $Arch
        enabled = $true
        mandatory = [bool]$Mandatory
        rolloutPercent = $RolloutPercent
        notes = $Notes
    }) $AdminToken
    if ([int]$StatusResponse.code -ne 200) {
        throw "Release was registered but could not be enabled"
    }
    $Enabled = $true
}

$PublishResult = [ordered]@{
    version = $Version
    channel = $Channel
    object_key = $ObjectKey
    package_size = $Archive.Length
    sha256 = $ArchiveHash
    release_id = [string]$RegisterResponse.data.releaseId
    prepared_only = $false
    enabled = $Enabled
    mandatory = [bool]$Mandatory
    rollout_percent = $RolloutPercent
}
Write-Utf8NoBom $ResultOutput ($PublishResult | ConvertTo-Json -Depth 5)
Write-Host "Release published successfully:"
Write-Host "  Version: $Version"
Write-Host "  Enabled: $Enabled"
Write-Host "  Artifacts: $PublishDir"
