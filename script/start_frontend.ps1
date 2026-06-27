$ErrorActionPreference = "Stop"

# Load shared paths and process helpers.
. "$PSScriptRoot\_common.ps1"

# Prepare the log folder used by Start-Process redirection.
Initialize-LogDir

# Close an old frontend service on port 5173 before starting a new one.
Stop-ProjectProcessesOnPort -Port $FrontendPort -ServiceName "frontend"

# Resolve npm explicitly so the script fails clearly if Node.js is not installed.
$npmPath = Resolve-RequiredCommand "npm.cmd"

# Start the Vite dev server in a hidden background window.
$stdoutLog = Join-Path $LogDir "frontend.out.log"
$stderrLog = Join-Path $LogDir "frontend.err.log"
$frontendProcess = Start-Process `
    -FilePath $npmPath `
    -ArgumentList @("run", "dev") `
    -WorkingDirectory $FrontendDir `
    -RedirectStandardOutput $stdoutLog `
    -RedirectStandardError $stderrLog `
    -WindowStyle Hidden `
    -PassThru

Write-Host "Frontend start command launched. PID=$($frontendProcess.Id), URL=http://127.0.0.1:$FrontendPort"

# Confirm the Vite page is reachable.
Wait-HttpOk -Url "http://127.0.0.1:$FrontendPort" -ServiceName "frontend" -TimeoutSeconds 30
