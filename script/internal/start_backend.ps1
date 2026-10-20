$ErrorActionPreference = "Stop"

# Load shared paths and process helpers.
. "$PSScriptRoot\_common.ps1"

# 已有健康服务直接复用，不因重复点击启动而中断私信。
$existing = Get-ProjectListener -Port $BackendPort -ServiceName "backend"
if ($existing) {
    Wait-HttpOk -Url "http://127.0.0.1:$BackendPort/api/health" -ServiceName "backend" -Port $BackendPort
    return
}
Initialize-LogDir

# Use the project virtual environment so dependencies stay local to this repo.
$pythonPath = Join-Path $BackendDir ".venv\Scripts\python.exe"
if (-not (Test-Path $pythonPath)) {
    throw "Backend virtual environment not found: $pythonPath"
}

# Start FastAPI through the project runner so the page can request a graceful shutdown.
$stdoutLog = Join-Path $LogDir "backend.out.log"
$stderrLog = Join-Path $LogDir "backend.err.log"
$originalServiceUrl = $env:MINI_BACKEND_URL
try {
    # 仅开发子进程继承旧地址；保留操作者显式指定的服务地址。
    if ([string]::IsNullOrWhiteSpace($originalServiceUrl)) {
        $developmentServiceUrl = (& $pythonPath -c "import sys; sys.path.insert(0, sys.argv[1]); from app.product_config import DEVELOPMENT_SERVICE_URL; print(DEVELOPMENT_SERVICE_URL)" $BackendDir)
        if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($developmentServiceUrl)) {
            throw "Failed to resolve the development backend address"
        }
        $env:MINI_BACKEND_URL = $developmentServiceUrl
    }
    $backendProcess = Start-Process `
        -FilePath $pythonPath `
        -ArgumentList @("-m", "app.main") `
        -WorkingDirectory $BackendDir `
        -RedirectStandardOutput $stdoutLog `
        -RedirectStandardError $stderrLog `
        -WindowStyle Hidden `
        -PassThru
}
finally {
    # 启动失败也恢复父进程环境，避免后续发布更新误用开发地址。
    $env:MINI_BACKEND_URL = $originalServiceUrl
}

Write-Host "Backend started. PID=$($backendProcess.Id), URL=http://127.0.0.1:$BackendPort"

# Confirm the backend health endpoint is reachable.
try {
    Wait-HttpOk -Url "http://127.0.0.1:$BackendPort/api/health" -ServiceName "backend" -Port $BackendPort -LaunchProcessId $backendProcess.Id
}
catch {
    # 清理本次启动失败的新进程，避免留下无人管理的后台。
    Stop-ProcessTree -ProcessId $backendProcess.Id
    throw
}
