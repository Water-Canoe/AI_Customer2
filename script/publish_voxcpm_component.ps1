[CmdletBinding()]
param(
    [string]$Version = "",
    [string]$ComponentPath = "",
    [string]$PrivateKeyPath = $env:AI_CUSTOMER_UPDATE_PRIVATE_KEY,
    [string]$PublicKeyPath = "",
    [string]$SshKeyPath = "",
    [string]$ServerBaseUrl = "https://tfwqsfaegbdj.sealosbja.site/ai-customer",
    [string]$MinAppVersion = "1.2.1",
    [string]$Notes = "",
    [switch]$Upload,
    [switch]$Enable,
    [switch]$PrepareOnly
)

$ErrorActionPreference = "Stop"
$SemVerPattern = '^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$'
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Python = Join-Path $ProjectRoot "backend\.venv\Scripts\python.exe"
$SigningTool = Join-Path $ProjectRoot "script\release_signing.py"
if (-not $PublicKeyPath) { $PublicKeyPath = Join-Path $ProjectRoot "packaging\update_signing_public.pem" }

# Publishing uses existing local tools and never installs dependencies.
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) { throw "Backend virtual environment not found: $Python" }
if (-not (Test-Path -LiteralPath $SigningTool -PathType Leaf)) { throw "Release signing helper not found: $SigningTool" }
if (-not (Test-Path -LiteralPath $PublicKeyPath -PathType Leaf)) { throw "Trusted signing public key not found: $PublicKeyPath" }

function Assert-SemVer([string]$Value, [string]$Label) {
    if ($Value -notmatch $SemVerPattern) { throw "$Label must use semantic version format, for example 1.0.0" }
}

function Write-Utf8NoBom([string]$Path, [string]$Content) {
    $Encoding = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($Path, $Content, $Encoding)
}

function Find-LatestComponent([string]$RootPath) {
    $Candidates = @(Get-ChildItem -LiteralPath $RootPath -Directory -ErrorAction SilentlyContinue | Where-Object {
        Test-Path -LiteralPath (Join-Path $_.FullName "component-info.json") -PathType Leaf
    } | Sort-Object LastWriteTime -Descending)
    if ($Candidates.Count -eq 0) { throw "No completed VoxCPM2 component was found under $RootPath" }
    return $Candidates[0].FullName
}

function Get-SealosAdminToken([string]$KeyPath) {
    if ([string]$env:AI_CUSTOMER_ADMIN_TOKEN -and $env:AI_CUSTOMER_ADMIN_TOKEN.Length -ge 32) {
        return [string]$env:AI_CUSTOMER_ADMIN_TOKEN
    }
    if (-not (Test-Path -LiteralPath $KeyPath -PathType Leaf)) { throw "Sealos SSH key not found: $KeyPath" }
    $Lines = @(& ssh.exe -i $KeyPath -p 2233 -o BatchMode=yes devbox@bja.sealos.run "sed -n 's/^AI_CUSTOMER_ADMIN_TOKEN=//p' /home/devbox/project/.env")
    if ($LASTEXITCODE -ne 0) { throw "Failed to read the Sealos update management token" }
    $Token = ($Lines -join "").Trim()
    if ($Token.Length -lt 32) { throw "The Sealos update management token is missing or too short" }
    return $Token
}

function Invoke-UpdateApi([string]$Method, [string]$Path, [object]$Body, [string]$AdminToken) {
    $Arguments = @{
        Uri = "$($ServerBaseUrl.TrimEnd('/'))$Path"
        Method = $Method
        Headers = @{ Authorization = "Bearer $AdminToken" }
        ContentType = "application/json; charset=utf-8"
    }
    if ($null -ne $Body) { $Arguments.Body = $Body | ConvertTo-Json -Depth 12 -Compress }
    try { return Invoke-RestMethod @Arguments }
    catch {
        $Details = if ($_.ErrorDetails.Message) { $_.ErrorDetails.Message } else { $_.Exception.Message }
        throw "Update API request failed: $Method $Path`n$Details"
    }
}

function Read-Component([string]$Path, [string]$ExpectedVersion) {
    $Resolved = Resolve-Path -LiteralPath $Path
    $AccessPath = if ($env:OS -eq "Windows_NT") { "\\?\$($Resolved.Path)" } else { $Resolved.Path }
    $InfoPath = [System.IO.Path]::Combine($AccessPath, "component-info.json")
    if (-not (Test-Path -LiteralPath $InfoPath -PathType Leaf)) { throw "component-info.json is missing" }
    $Info = Get-Content -LiteralPath $InfoPath -Raw -Encoding utf8 | ConvertFrom-Json
    if ([int]$Info.format -ne 1 -or [string]$Info.product -ne "AI Customer Component" -or [string]$Info.component -ne "voxcpm2") {
        throw "Component metadata is invalid"
    }
    if ([string]$Info.entrypoint -ne "VoxCPM_Runtime.exe" -or -not (Test-Path -LiteralPath ([System.IO.Path]::Combine($AccessPath, "VoxCPM_Runtime.exe")) -PathType Leaf)) {
        throw "Component executable is missing"
    }
    if ($ExpectedVersion -and [string]$Info.version -ne $ExpectedVersion) { throw "Component version does not match -Version" }
    $Files = @(Get-ChildItem -LiteralPath $AccessPath -Recurse -File -Force)
    foreach ($File in $Files) {
        if (($File.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Component contains a reparse-point file: $($File.FullName)"
        }
    }
    return [pscustomobject]@{ Path = $Resolved.Path; AccessPath = $AccessPath; Info = $Info; Files = $Files }
}

function New-ComponentArchive([string]$SourcePath, [string]$DestinationPath, [object[]]$Files) {
    Add-Type -AssemblyName System.IO.Compression
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $Prefix = $SourcePath.TrimEnd('\') + '\'
    $Stream = [System.IO.File]::Open($DestinationPath, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::ReadWrite, [System.IO.FileShare]::None)
    try {
        $Archive = [System.IO.Compression.ZipArchive]::new($Stream, [System.IO.Compression.ZipArchiveMode]::Create, $false)
        try {
            foreach ($File in @($Files | Sort-Object FullName)) {
                $EntryName = $File.FullName.Substring($Prefix.Length).Replace('\', '/')
                [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile($Archive, $File.FullName, $EntryName, [System.IO.Compression.CompressionLevel]::Optimal) | Out-Null
            }
        }
        finally { $Archive.Dispose() }
    }
    finally { $Stream.Dispose() }
}

$IsPrepareOnly = [bool]$PrepareOnly -or (-not $Upload -and -not $Enable)
if ($PrepareOnly -and ($Upload -or $Enable)) { throw "-PrepareOnly cannot be combined with -Upload or -Enable" }
if (-not $ComponentPath) { $ComponentPath = Find-LatestComponent (Join-Path $ProjectRoot "dist\components") }
$Component = Read-Component $ComponentPath $Version
if (-not $Version) { $Version = [string]$Component.Info.version }
Assert-SemVer $Version "Version"
Assert-SemVer $MinAppVersion "MinAppVersion"
if (-not $Notes) { $Notes = "VoxCPM2 $Version 音色克隆组件" }

if (-not $PrivateKeyPath) { $PrivateKeyPath = Join-Path $HOME ".ssh\sealos\ai_customer_update_signing_private.pem" }
$PrivateKey = Resolve-Path -LiteralPath $PrivateKeyPath
if ($PrivateKey.Path.StartsWith($ProjectRoot.TrimEnd('\') + '\', [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Component signing private key must be stored outside the project repository"
}
if (-not $SshKeyPath) { $SshKeyPath = Join-Path $HOME ".ssh\sealos\bja.sealos.run_ns-0lgzvp7r_medician-ai-back" }
if (-not $IsPrepareOnly) {
    if (([Uri]$ServerBaseUrl).Scheme -ne "https") { throw "ServerBaseUrl must use HTTPS" }
    $AdminToken = Get-SealosAdminToken $SshKeyPath
    if (-not (Get-Command curl.exe -ErrorAction SilentlyContinue)) { throw "curl.exe is required for component upload" }
}

# Preserve every signed publish workspace for inspection and reuse.
$Stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$PublishDir = Join-Path $ProjectRoot "output\voxcpm_publish_${Version}_$Stamp"
New-Item -ItemType Directory -Path $PublishDir | Out-Null
$ArchivePath = Join-Path $PublishDir "AI_Customer_VoxCPM2_${Version}_windows_x64.zip"
$ManifestOutput = Join-Path $PublishDir "component-manifest.json"
$SignatureOutput = Join-Path $PublishDir "component-manifest.sig"
$ResultOutput = Join-Path $PublishDir "publish-result.json"
Write-Host "Valid component verified. Creating archive..."
New-ComponentArchive $Component.AccessPath $ArchivePath @($Component.Files)
$Archive = Get-Item -LiteralPath $ArchivePath
$ArchiveHash = (Get-FileHash -LiteralPath $ArchivePath -Algorithm SHA256).Hash.ToLowerInvariant()

$ObjectKey = "components/voxcpm2/$Version/AI_Customer_VoxCPM2_${Version}_windows_x64.zip"
$SkipUpload = $IsPrepareOnly
$ExistingComponent = $null
if (-not $IsPrepareOnly) {
    try {
        $UploadResponse = Invoke-UpdateApi "POST" "/update/admin/component-upload-url" ([ordered]@{ component = "voxcpm2"; version = $Version; platform = "windows"; arch = "x64" }) $AdminToken
        if ([int]$UploadResponse.code -ne 200 -or -not $UploadResponse.data.uploadUrl) { throw "Upload URL response is incomplete" }
        $ObjectKey = [string]$UploadResponse.data.objectKey
        $UploadUrl = [string]$UploadResponse.data.uploadUrl
    }
    catch {
        if ($_.Exception.Message -notmatch 'COMPONENT_PACKAGE_ALREADY_EXISTS') { throw }
        $Existing = Invoke-UpdateApi "GET" "/update/admin/components" $null $AdminToken
        $Match = @($Existing.data | Where-Object { $_.component -eq "voxcpm2" -and $_.version -eq $Version -and [long]$_.packageSize -eq [long]$Archive.Length -and ([string]$_.sha256).ToLowerInvariant() -eq $ArchiveHash } | Select-Object -First 1)[0]
        if (-not $Match) { throw "An immutable component package already exists but differs from the local archive. Use a new component version." }
        $ExistingComponent = $Match
        $ObjectKey = [string]$Match.objectKey
        $SkipUpload = $true
    }
}

$Manifest = [ordered]@{
    format = 1
    product = "AI Customer Component"
    component = "voxcpm2"
    version = $Version
    platform = "windows"
    arch = "x64"
    min_app_version = $MinAppVersion
    published_at = (Get-Date).ToUniversalTime().ToString("o")
    package = [ordered]@{ object_key = $ObjectKey; size = $Archive.Length; sha256 = $ArchiveHash }
}
$ManifestText = $Manifest | ConvertTo-Json -Depth 8 -Compress
Write-Utf8NoBom $ManifestOutput $ManifestText
& $Python $SigningTool sign --private-key $PrivateKey.Path --input $ManifestOutput --output $SignatureOutput
if ($LASTEXITCODE -ne 0) { throw "Component manifest signing failed" }
& $Python $SigningTool verify --public-key $PublicKeyPath --input $ManifestOutput --signature $SignatureOutput
if ($LASTEXITCODE -ne 0) { throw "Signing key does not match the trusted client public key" }
$Signature = (Get-Content -LiteralPath $SignatureOutput -Raw -Encoding ascii).Trim()

if ($IsPrepareOnly) {
    Write-Utf8NoBom $ResultOutput ([ordered]@{ component = "voxcpm2"; version = $Version; package_size = $Archive.Length; sha256 = $ArchiveHash; prepared_only = $true; enabled = $false } | ConvertTo-Json)
    Write-Host "Component artifacts prepared without remote changes:"
    Write-Host "  $PublishDir"
    return
}
if (-not $SkipUpload) {
    Write-Host "Uploading immutable component package..."
    & curl.exe --fail-with-body --silent --show-error --connect-timeout 30 --request PUT --upload-file $ArchivePath $UploadUrl
    if ($LASTEXITCODE -ne 0) { throw "Object storage upload failed; component metadata was not registered" }
}
if ($ExistingComponent) {
    $Register = [pscustomobject]@{ code = 200; data = $ExistingComponent }
}
else {
    $Register = Invoke-UpdateApi "POST" "/update/admin/components" ([ordered]@{ manifestText = $ManifestText; signature = $Signature; notes = $Notes }) $AdminToken
    if ([int]$Register.code -notin @(200, 201)) { throw "Component registration failed" }
}
$Enabled = $false
if ($Enable) {
    $Status = Invoke-UpdateApi "PUT" "/update/admin/component-status" ([ordered]@{ component = "voxcpm2"; version = $Version; platform = "windows"; arch = "x64"; enabled = $true; notes = $Notes }) $AdminToken
    if ([int]$Status.code -ne 200) { throw "Component was registered but could not be enabled" }
    $Enabled = $true
}
Write-Utf8NoBom $ResultOutput ([ordered]@{ component = "voxcpm2"; version = $Version; package_size = $Archive.Length; sha256 = $ArchiveHash; prepared_only = $false; enabled = $Enabled } | ConvertTo-Json)
Write-Host "Component published successfully:"
Write-Host "  Version: $Version"
Write-Host "  Enabled: $Enabled"
Write-Host "  Artifacts: $PublishDir"
