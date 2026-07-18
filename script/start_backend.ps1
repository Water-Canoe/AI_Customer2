$ErrorActionPreference = "Stop"

# Load shared paths and process helpers.
. "$PSScriptRoot\_common.ps1"

# Prepare the log folder used by Start-Process redirection.
Initialize-LogDir

# Close an old backend service on port 8000 before starting a new one.
Stop-ProjectProcessesOnPort -Port $BackendPort -ServiceName "backend"

# Use the project virtual environment so dependencies stay local to this repo.
$pythonPath = Join-Path $BackendDir ".venv\Scripts\python.exe"
if (-not (Test-Path $pythonPath)) {
    throw "Backend virtual environment not found: $pythonPath"
}

# Start FastAPI through the project runner so the page can request a graceful shutdown.
$stdoutLog = Join-Path $LogDir "backend.out.log"
$stderrLog = Join-Path $LogDir "backend.err.log"
$backendProcess = Start-Process `
    -FilePath $pythonPath `
    -ArgumentList @("-m", "app.main") `
    -WorkingDirectory $BackendDir `
    -RedirectStandardOutput $stdoutLog `
    -RedirectStandardError $stderrLog `
    -WindowStyle Hidden `
    -PassThru

Write-Host "Backend started. PID=$($backendProcess.Id), URL=http://127.0.0.1:$BackendPort"

# Confirm the backend health endpoint is reachable.
Wait-HttpOk -Url "http://127.0.0.1:$BackendPort/api/health" -ServiceName "backend" -TimeoutSeconds 30
