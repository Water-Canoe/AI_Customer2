$ErrorActionPreference = "Stop"

# Load shared paths and process helpers.
. "$PSScriptRoot\_common.ps1"

# Stop the frontend before the backend so no page keeps sending API calls.
Stop-ProjectProcessesOnPort -Port $FrontendPort -ServiceName "frontend"

# Stop the backend API service.
Stop-ProjectProcessesOnPort -Port $BackendPort -ServiceName "backend"

Write-Host "All project services are stopped."
