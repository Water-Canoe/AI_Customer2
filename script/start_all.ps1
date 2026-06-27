$ErrorActionPreference = "Stop"

# Start the backend first because the frontend proxies API requests to it.
& "$PSScriptRoot\start_backend.ps1"

# Start the frontend after the backend health check passes.
& "$PSScriptRoot\start_frontend.ps1"

Write-Host "All services are running:"
Write-Host "  Backend:  http://127.0.0.1:8000"
Write-Host "  Frontend: http://127.0.0.1:5173"
