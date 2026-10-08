param([ValidateSet("all", "backend", "frontend")][string]$Service = "all")
$ErrorActionPreference = "Stop"

# Load shared paths and process helpers.
. "$PSScriptRoot\_common.ps1"

# Stop the frontend before the backend so no page keeps sending API calls.
if ($Service -in @("all", "frontend")) { Stop-ProjectProcessesOnPort -Port $FrontendPort -ServiceName "frontend" }

# Stop the backend API service.
if ($Service -in @("all", "backend")) { Stop-ProjectProcessesOnPort -Port $BackendPort -ServiceName "backend" }

Write-Host "Requested project services are stopped."
