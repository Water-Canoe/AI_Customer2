$ErrorActionPreference = "Stop"

# Shared paths used by all project service scripts.
$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Resolve-Path (Join-Path $ScriptRoot "..")
$BackendDir = Join-Path $ProjectRoot "backend"
$FrontendDir = Join-Path $ProjectRoot "frontend"
$LogDir = Join-Path $ScriptRoot "logs"
$BackendPort = 8000
$FrontendPort = 5173

function Initialize-LogDir {
    # Create the log folder if it does not exist.
    if (-not (Test-Path $LogDir)) {
        New-Item -Path $LogDir -ItemType Directory -Force | Out-Null
    }
}

function Resolve-RequiredCommand {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name
    )

    # Resolve a command path and fail early when the runtime is missing.
    $command = Get-Command $Name -ErrorAction SilentlyContinue
    if (-not $command) {
        throw "Required command not found: $Name"
    }
    return $command.Source
}

function Get-ListenerProcesses {
    param(
        [Parameter(Mandatory = $true)]
        [int]$Port
    )

    # Find processes currently listening on the requested local port.
    $connections = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    if (-not $connections) {
        return @()
    }

    $processIds = $connections | Select-Object -ExpandProperty OwningProcess -Unique
    $processes = @()
    foreach ($processId in $processIds) {
        $process = Get-CimInstance Win32_Process -Filter "ProcessId = $processId" -ErrorAction SilentlyContinue
        if ($process) {
            $processes += $process
        }
    }
    return $processes
}

function Test-ProjectOwnedProcess {
    param(
        [Parameter(Mandatory = $true)]
        $Process
    )

    # Only stop processes that look like this project's Vite or FastAPI services.
    $commandLine = [string]$Process.CommandLine
    $rootText = [string]$ProjectRoot
    if ($commandLine.IndexOf($rootText, [StringComparison]::OrdinalIgnoreCase) -ge 0) {
        return $true
    }
    if ($commandLine -match "uvicorn\s+app\.main:app") {
        return $true
    }
    if ($commandLine -match "vite(\.js)?\s+--host\s+127\.0\.0\.1") {
        return $true
    }
    return $false
}

function Stop-ProcessTree {
    param(
        [Parameter(Mandatory = $true)]
        [int]$ProcessId
    )

    # Stop the explicit process tree so child Python/browser processes do not survive a restart.
    if ($IsWindows -or $env:OS -eq "Windows_NT") {
        & taskkill.exe /PID $ProcessId /T /F | Out-Null
        return
    }
    Stop-Process -Id $ProcessId -Force
}

function Stop-ProjectProcessesOnPort {
    param(
        [Parameter(Mandatory = $true)]
        [int]$Port,
        [Parameter(Mandatory = $true)]
        [string]$ServiceName
    )

    # Stop existing project service processes before starting a fresh one.
    $processes = Get-ListenerProcesses -Port $Port
    if (-not $processes -or $processes.Count -eq 0) {
        Write-Host "No old $ServiceName process is listening on port $Port."
        return
    }

    foreach ($process in $processes) {
        if (-not (Test-ProjectOwnedProcess -Process $process)) {
            throw "Port $Port is occupied by a non-project process. PID=$($process.ProcessId), CommandLine=$($process.CommandLine)"
        }

        $processId = [int]$process.ProcessId
        Write-Host "Stopping old $ServiceName process. PID=$processId, Port=$Port"
        Stop-ProcessTree -ProcessId $processId
    }

    # Wait briefly until Windows releases the port.
    for ($index = 0; $index -lt 20; $index++) {
        Start-Sleep -Milliseconds 250
        $remaining = Get-ListenerProcesses -Port $Port
        if (-not $remaining -or $remaining.Count -eq 0) {
            return
        }
    }

    throw "Port $Port is still occupied after stopping old $ServiceName processes."
}

function Wait-HttpOk {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Url,
        [Parameter(Mandatory = $true)]
        [string]$ServiceName,
        [int]$TimeoutSeconds = 30
    )

    # Poll the service URL until it returns a successful HTTP status.
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        $status = & curl.exe -s -o NUL -w "%{http_code}" $Url
        if ($LASTEXITCODE -eq 0 -and $status -match "^\d+$" -and [int]$status -ge 200 -and [int]$status -lt 500) {
            Write-Host "$ServiceName is ready: $Url"
            return
        }
        Start-Sleep -Milliseconds 500
    }

    throw "$ServiceName did not become ready in $TimeoutSeconds seconds. Check logs in $LogDir."
}
