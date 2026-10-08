[CmdletBinding()]
param(
    [ValidateSet("start", "restart", "stop")]
    [string]$Action = "start",
    [ValidateSet("all", "backend", "frontend")]
    [string]$Service = "all"
)

$ErrorActionPreference = "Stop"
. "$PSScriptRoot\internal\_common.ps1"

# 启动或重启前先检查所有目标服务，缺少依赖或端口被占用时不停止现有服务。
if ($Action -ne "stop") {
    if ($Service -in @("all", "backend")) {
        Get-ProjectListener -Port $BackendPort -ServiceName "backend" | Out-Null
        $PythonPath = Join-Path $BackendDir ".venv\Scripts\python.exe"
        if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) { throw "Backend virtual environment not found: $PythonPath" }
        & $PythonPath -c "import fastapi, uvicorn"
        if ($LASTEXITCODE -ne 0) { throw "Backend dependencies are incomplete. Install backend/requirements.txt first." }
    }
    if ($Service -in @("all", "frontend")) {
        Get-ProjectListener -Port $FrontendPort -ServiceName "frontend" | Out-Null
        Resolve-RequiredCommand "node.exe" | Out-Null
        $VitePath = Join-Path $FrontendDir "node_modules\vite\bin\vite.js"
        if (-not (Test-Path -LiteralPath $VitePath -PathType Leaf)) { throw "Frontend dependencies are missing. Run npm.cmd ci in frontend first." }
    }
}

if ($Action -in @("stop", "restart")) { & "$PSScriptRoot\internal\stop_all.ps1" -Service $Service }
if ($Action -eq "stop") { return }

# 重复启动会检查并复用现有服务；只有明确指定 restart 才重启。
if ($Service -in @("all", "backend")) { & "$PSScriptRoot\internal\start_backend.ps1" }
if ($Service -in @("all", "frontend")) { & "$PSScriptRoot\internal\start_frontend.ps1" }
Write-Host "Services are ready. Logs: $LogDir"
