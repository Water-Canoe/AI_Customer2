[CmdletBinding()]
param(
    [ValidateSet("publish", "status", "set-state")]
    [string]$Action = "publish",
    [string]$Version = "",
    [Alias("ProgramZip")]
    [string]$ZipPath = "",
    [string]$ResumeManifest = "",
    [string]$PrivateKeyPath = $env:AI_CUSTOMER_UPDATE_PRIVATE_KEY,
    [string]$PublicKeyPath = "",
    [string]$SshKeyPath = "",
    [string]$ServerBaseUrl = "https://tfwqsfaegbdj.sealosbja.site/ai-customer",
    [ValidateSet("stable", "beta")]
    [string]$Channel = "stable",
    [ValidateSet("windows")]
    [string]$Platform = "windows",
    [ValidateSet("x64")]
    [string]$Arch = "x64",
    [string]$MinUpdaterVersion = "1.1.0",
    [string]$Notes = "",
    [ValidateRange(0, 100)]
    [int]$RolloutPercent = 100,
    [bool]$Enabled = $false,
    [switch]$Mandatory,
    [switch]$Upload,
    [switch]$Enable,
    [switch]$PrepareOnly
)

$ErrorActionPreference = "Stop"
$SemVerPattern = '^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$'
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$RequestedState = @{}
foreach ($Field in @("Enabled", "Mandatory", "RolloutPercent", "Notes")) {
    if ($PSBoundParameters.ContainsKey($Field)) { $RequestedState[$Field] = $PSBoundParameters[$Field] }
}

function Assert-SemVer([string]$Value, [string]$Label) {
    if ($Value -notmatch $SemVerPattern) { throw "$Label must use semantic version format, for example 1.2.0" }
}

function Write-Utf8NoBom([string]$Path, [string]$Content) {
    [System.IO.File]::WriteAllText($Path, $Content, (New-Object System.Text.UTF8Encoding($false)))
}

function Get-SealosAdminToken([string]$KeyPath) {
    $Existing = [string]$env:AI_CUSTOMER_ADMIN_TOKEN
    if ($Existing.Length -ge 32) { return $Existing }
    if (-not (Test-Path -LiteralPath $KeyPath -PathType Leaf)) { throw "Sealos SSH key not found: $KeyPath" }
    if (-not (Get-Command ssh.exe -ErrorAction SilentlyContinue)) { throw "ssh.exe is required to read the Sealos management token" }
    $TokenLines = @(& ssh.exe -i $KeyPath -p 2233 -o BatchMode=yes devbox@bja.sealos.run "sed -n 's/^AI_CUSTOMER_ADMIN_TOKEN=//p' /home/devbox/project/.env")
    if ($LASTEXITCODE -ne 0) { throw "Failed to read the management token through Sealos SSH" }
    $Token = ($TokenLines -join "").Trim()
    if ($Token.Length -lt 32) { throw "The management token is missing or too short" }
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
        $Details = $_.ErrorDetails.Message
        if (-not $Details) { $Details = $_.Exception.Message }
        throw "Update API request failed: $Method $Path`n$Details"
    }
}

function Get-Releases {
    $Response = Invoke-UpdateApi "GET" "/update/admin/releases" $null $AdminToken
    if ([int]$Response.code -ne 200) { throw "Release list could not be read" }
    return @($Response.data)
}

function Find-Release([object[]]$Releases) {
    $Matches = @($Releases | Where-Object {
        $_.version -eq $Version -and $_.channel -eq $Channel -and $_.platform -eq $Platform -and $_.arch -eq $Arch
    })
    if ($Matches.Count -gt 1) { throw "Release identity is not unique" }
    if ($Matches.Count -eq 1) { return $Matches[0] }
    return $null
}

function Assert-PackageMatch([object]$Registered) {
    if ([long]$Registered.packageSize -ne [long]$Release.size -or ([string]$Registered.sha256).ToLowerInvariant() -ne $Release.sha256) {
        throw "The immutable package for $Version differs from the local archive. Publish a new version instead."
    }
}

function Set-ReleaseState([object]$Registered) {
    # Preserve fields the operator did not explicitly request to change.
    $State = [ordered]@{
        version = $Version; channel = $Channel; platform = $Platform; arch = $Arch
        enabled = [bool]$Registered.enabled
        mandatory = [bool]$Registered.mandatory
        rolloutPercent = [int]$Registered.rolloutPercent
        notes = [string]$Registered.notes
    }
    foreach ($Field in $RequestedState.Keys) {
        $Value = $RequestedState[$Field]
        if ($Field -in @("Enabled", "Mandatory")) { $Value = [bool]$Value }
        $State[$Field] = $Value
    }
    if ($State.enabled -and -not $State.mandatory -and $State.rolloutPercent -eq 0) {
        throw "Enabled distribution requires a positive rollout unless mandatory is set"
    }
    $Response = Invoke-UpdateApi "PUT" "/update/admin/release-status" $State $AdminToken
    if ([int]$Response.code -ne 200) { throw "Release state could not be changed" }
    $Verified = Find-Release @(Get-Releases)
    if (-not $Verified) { throw "Release disappeared after its state changed" }
    foreach ($Field in @("enabled", "mandatory", "rolloutPercent", "notes")) {
        if ($Verified.$Field -cne $State[$Field]) { throw "Release state read-back differs for $Field" }
    }
    return $Verified
}

function Write-PublishResult([object]$Registered, [bool]$AlreadyPublished) {
    $Result = [ordered]@{
        version = $Version; channel = $Channel; object_key = [string]$Registered.objectKey
        package_size = [long]$Release.size; sha256 = [string]$Release.sha256
        source_revision = [string]$Release.source_revision; source_dirty = [bool]$Release.source_dirty
        built_at = [string]$Release.built_at; updater_version = [string]$Release.updater_version
        release_id = [string]$Registered.releaseId; already_published = $AlreadyPublished
        prepared_only = $IsPrepareOnly; enabled = [bool]$Registered.enabled
        mandatory = [bool]$Registered.mandatory; rollout_percent = [int]$Registered.rolloutPercent
        notes = [string]$Registered.notes
    }
    Write-Utf8NoBom $ResultOutput ($Result | ConvertTo-Json -Depth 5)
    Write-Host "Version: $Version; enabled: $($Result.enabled); rollout: $($Result.rollout_percent)%"
    Write-Host "Publish records: $PublishDir"
}

if ($Notes.Length -gt 4000) { throw "Notes length exceeds 4000 characters" }
if ($PrepareOnly -and ($Upload -or $Enable -or $ResumeManifest)) { throw "PrepareOnly cannot perform remote changes" }
if ($Enable -and $PSBoundParameters.ContainsKey("Enabled")) { throw "Use Enable for publish, or Enabled for set-state" }
if ($Action -ne "publish" -and ($ZipPath -or $ResumeManifest -or $Upload -or $Enable -or $PrepareOnly)) {
    throw "Status actions cannot upload or sign packages"
}
if ($Action -eq "publish" -and $PSBoundParameters.ContainsKey("Enabled")) { throw "Enabled is only supported with set-state" }
if ($Action -eq "status" -and $RequestedState.Count) { throw "Use set-state to change distribution fields" }
if ($Enable) {
    $RequestedState.Enabled = $true
    $RequestedState.RolloutPercent = $RolloutPercent
}
$IsPrepareOnly = $Action -eq "publish" -and ([bool]$PrepareOnly -or (-not $Upload -and -not $Enable -and -not $ResumeManifest))

if (-not $IsPrepareOnly) {
    if (([Uri]$ServerBaseUrl).Scheme -ne "https") { throw "ServerBaseUrl must use HTTPS" }
    if (-not $SshKeyPath) { $SshKeyPath = Join-Path $HOME ".ssh\sealos\bja.sealos.run_ns-0lgzvp7r_medician-ai-back" }
    $AdminToken = Get-SealosAdminToken $SshKeyPath
}
if ($Action -ne "publish") {
    Assert-SemVer $Version "Version"
    $Registered = Find-Release @(Get-Releases)
    if (-not $Registered) { throw "No registered release matches the requested identity" }
    if ($Action -eq "set-state") {
        if ($RequestedState.Count -eq 0) { throw "Set-state requires Enabled, Mandatory, RolloutPercent or Notes" }
        $Registered = Set-ReleaseState $Registered
    }
    $Registered | Select-Object version,channel,platform,arch,enabled,mandatory,rolloutPercent,notes,sha256 | ConvertTo-Json
    return
}

$Python = Join-Path $ProjectRoot "backend\.venv\Scripts\python.exe"
$SigningTool = Join-Path $ProjectRoot "script\release_signing.py"
$ProgramZipVerifier = Join-Path $ProjectRoot "script\verify_program_zip.py"
foreach ($Tool in @($Python, $SigningTool, $ProgramZipVerifier)) {
    if (-not (Test-Path -LiteralPath $Tool -PathType Leaf)) { throw "Required local tool not found: $Tool" }
}
if (-not $PublicKeyPath) { $PublicKeyPath = Join-Path $ProjectRoot "packaging\update_signing_public.pem" }
if (-not (Test-Path -LiteralPath $PublicKeyPath -PathType Leaf)) { throw "Trusted signing public key is missing" }
& $Python -c "import cryptography"
if ($LASTEXITCODE -ne 0) { throw "cryptography is missing from backend/.venv" }
if (-not $ZipPath) {
    Assert-SemVer $Version "Version"
    $VersionDir = Join-Path $ProjectRoot "deliverables\$Version"
    $Candidates = @(Get-ChildItem -LiteralPath $VersionDir -File -Filter "AI_Customer_Program_*.zip")
    if ($Candidates.Count -ne 1) { throw "Specify ZipPath, or Version with exactly one completed program ZIP" }
    $ZipPath = $Candidates[0].FullName
}
$ArchivePath = (Resolve-Path -LiteralPath $ZipPath).Path
$VerifyArguments = @($ProgramZipVerifier, $ArchivePath)
if ($Version) { $VerifyArguments += @("--version", $Version) }
$VerifyOutput = @(& $Python @VerifyArguments)
if ($LASTEXITCODE -ne 0) { throw "Program ZIP verification failed" }
$Release = ($VerifyOutput -join "") | ConvertFrom-Json
$Version = [string]$Release.version
Assert-SemVer $Version "Version"
Assert-SemVer $MinUpdaterVersion "MinUpdaterVersion"
if ($MinUpdaterVersion -ne [string]$Release.updater_version) { throw "MinUpdaterVersion must match the verified package updater_version" }
if ($Channel -eq "stable" -and $Version.Contains('-')) { throw "Stable channel cannot publish a prerelease version" }
if (-not $Notes) { $Notes = "Release $Version" }
Write-Host "Selected ZIP: $ArchivePath"
Write-Host "Source: $($Release.source_revision); dirty: $($Release.source_dirty); built: $($Release.built_at)"
Write-Host "Version: $Version; updater: $MinUpdaterVersion; sha256: $($Release.sha256)"

$SkipUpload = $false
$ExistingRelease = $null
if ($ResumeManifest) {
    # Reuse exact signed bytes after failed registration, never a regenerated timestamp or package.
    $ManifestOutput = (Resolve-Path -LiteralPath $ResumeManifest).Path
    $PublishDir = Split-Path -Parent $ManifestOutput
    $SignatureOutput = [System.IO.Path]::ChangeExtension($ManifestOutput, ".sig")
    & $Python $SigningTool verify --public-key $PublicKeyPath --input $ManifestOutput --signature $SignatureOutput
    if ($LASTEXITCODE -ne 0) { throw "Saved release manifest signature verification failed" }
    # Read plain text; PowerShell 5 adds provider properties to Get-Content strings during JSON serialization.
    $ManifestText = [System.IO.File]::ReadAllText($ManifestOutput, [System.Text.Encoding]::UTF8)
    $UpdateManifest = $ManifestText | ConvertFrom-Json
    if ($UpdateManifest.format -ne 1 -or $UpdateManifest.product -ne "AI Customer Desktop" -or
        $UpdateManifest.version -ne $Version -or $UpdateManifest.platform -ne $Platform -or $UpdateManifest.arch -ne $Arch -or
        $UpdateManifest.schema_version -ne $Release.schema_version -or $UpdateManifest.environment_version -ne $Release.environment_version -or
        $UpdateManifest.min_updater_version -ne $MinUpdaterVersion -or $UpdateManifest.package.size -ne $Release.size -or
        $UpdateManifest.package.sha256 -ne $Release.sha256 -or -not $UpdateManifest.package.object_key) {
        throw "Saved signed manifest does not match the selected program ZIP"
    }
    $ObjectKey = [string]$UpdateManifest.package.object_key
    $SkipUpload = $true
}
else {
    $Stamp = Get-Date -Format "yyyyMMdd_HHmmss_fff"
    $PublishDir = Join-Path $ProjectRoot "output\release_publish_${Version}_$Stamp"
    New-Item -ItemType Directory -Path $PublishDir | Out-Null
    $ManifestOutput = Join-Path $PublishDir "update-manifest.json"
    $SignatureOutput = Join-Path $PublishDir "update-manifest.sig"
}
$ResultOutput = Join-Path $PublishDir "publish-result.json"

if (-not $IsPrepareOnly) {
    $Releases = @(Get-Releases)
    $ExistingRelease = Find-Release $Releases
    if ($ExistingRelease) {
        Assert-PackageMatch $ExistingRelease
        if ($ResumeManifest -and $ExistingRelease.objectKey -ne $ObjectKey) { throw "Registered object differs from the saved signed manifest" }
        if ($RequestedState.Count) { $ExistingRelease = Set-ReleaseState $ExistingRelease }
        Write-PublishResult $ExistingRelease $true
        return
    }
    if (-not $ResumeManifest) {
        $OtherChannel = @($Releases | Where-Object { $_.version -eq $Version -and $_.platform -eq $Platform -and $_.arch -eq $Arch })
        if ($OtherChannel.Count) {
            foreach ($Registered in $OtherChannel) { Assert-PackageMatch $Registered }
            $ObjectKey = [string]$OtherChannel[0].objectKey
            $SkipUpload = $true
        }
        else {
            try { $UploadResponse = Invoke-UpdateApi "POST" "/update/admin/upload-url" ([ordered]@{ version = $Version; platform = $Platform; arch = $Arch }) $AdminToken }
            catch {
                if ($_.Exception.Message -match 'PACKAGE_ALREADY_EXISTS') {
                    throw "Package already exists without matching registration. Use ResumeManifest with the original saved signed manifest; no object was overwritten."
                }
                throw
            }
            if ([int]$UploadResponse.code -ne 200 -or -not $UploadResponse.data.objectKey -or -not $UploadResponse.data.uploadUrl) { throw "Upload URL response is incomplete" }
            $ObjectKey = [string]$UploadResponse.data.objectKey
            $UploadUrl = [string]$UploadResponse.data.uploadUrl
            if (([Uri]$UploadUrl).Scheme -ne "https") { throw "Object storage upload URL must use HTTPS" }
        }
    }
}
else { $ObjectKey = "releases/$Version/AI_Customer_${Version}_${Platform}_${Arch}.zip" }

if (-not $ResumeManifest) {
    if (-not $PrivateKeyPath) { $PrivateKeyPath = Join-Path $HOME ".ssh\sealos\ai_customer_update_signing_private.pem" }
    $PrivateKey = (Resolve-Path -LiteralPath $PrivateKeyPath).Path
    if (-not (Test-Path -LiteralPath $PrivateKey -PathType Leaf)) { throw "Release signing private key is missing" }
    if ($PrivateKey.StartsWith($ProjectRoot.TrimEnd('\') + '\', [System.StringComparison]::OrdinalIgnoreCase)) { throw "Release signing private key must be stored outside the project repository" }
    $UpdateManifest = [ordered]@{
        format = 1; product = "AI Customer Desktop"; version = $Version; platform = $Platform; arch = $Arch
        schema_version = [int]$Release.schema_version; environment_version = [string]$Release.environment_version
        min_updater_version = $MinUpdaterVersion; published_at = (Get-Date).ToUniversalTime().ToString("o")
        package = [ordered]@{ object_key = $ObjectKey; size = [long]$Release.size; sha256 = [string]$Release.sha256 }
    }
    $ManifestText = $UpdateManifest | ConvertTo-Json -Depth 8 -Compress
    Write-Utf8NoBom $ManifestOutput $ManifestText
    & $Python $SigningTool sign --private-key $PrivateKey --input $ManifestOutput --output $SignatureOutput
    if ($LASTEXITCODE -ne 0) { throw "Release manifest signing failed" }
    & $Python $SigningTool verify --public-key $PublicKeyPath --input $ManifestOutput --signature $SignatureOutput
    if ($LASTEXITCODE -ne 0) { throw "Signing key does not match the trusted client public key" }
}
$Signature = (Get-Content -LiteralPath $SignatureOutput -Raw -Encoding ascii).Trim()
if ([Convert]::FromBase64String($Signature).Length -ne 64) { throw "Generated Ed25519 signature has an invalid length" }
if ($IsPrepareOnly) {
    Write-PublishResult ([pscustomobject]@{ objectKey = $ObjectKey; enabled = $false; mandatory = $false; rolloutPercent = 0; notes = $Notes }) $false
    return
}
if (-not $SkipUpload) {
    if (-not (Get-Command curl.exe -ErrorAction SilentlyContinue)) { throw "curl.exe is required for streaming the release upload" }
    & curl.exe --fail-with-body --silent --show-error --connect-timeout 30 --request PUT --upload-file $ArchivePath $UploadUrl
    if ($LASTEXITCODE -ne 0) { throw "Object upload failed; signed manifest preserved at $ManifestOutput" }
}
try {
    $RegisterResponse = Invoke-UpdateApi "POST" "/update/admin/releases" ([ordered]@{ channel = $Channel; manifestText = $ManifestText; signature = $Signature; notes = $Notes }) $AdminToken
}
catch { throw "Release registration failed; resume with signed manifest: $ManifestOutput`n$($_.Exception.Message)" }
if ([int]$RegisterResponse.code -notin @(200, 201)) { throw "Release registration failed; resume with signed manifest: $ManifestOutput" }
$Registered = Find-Release @(Get-Releases)
if (-not $Registered) { throw "Release registration could not be read back; signed manifest: $ManifestOutput" }
Assert-PackageMatch $Registered
if ($Registered.objectKey -ne $ObjectKey) { throw "Registered object key differs from the signed manifest" }
if ($RequestedState.Count) { $Registered = Set-ReleaseState $Registered }
Write-PublishResult $Registered $false
