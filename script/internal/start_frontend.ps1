$ErrorActionPreference = "Stop"

# Load shared paths and process helpers.
. "$PSScriptRoot\_common.ps1"

# 已有健康服务直接复用；固定端口避免 Vite 悄悄切换到另一个端口。
$existing = Get-ProjectListener -Port $FrontendPort -ServiceName "frontend"
if ($existing) {
    Wait-HttpOk -Url "http://127.0.0.1:$FrontendPort" -ServiceName "frontend" -Port $FrontendPort
    return
}
$nodePath = Resolve-RequiredCommand "node.exe"
$vitePath = Join-Path $FrontendDir "node_modules\vite\bin\vite.js"
if (-not (Test-Path -LiteralPath $vitePath -PathType Leaf)) { throw "Frontend Vite is missing: $vitePath" }
Initialize-LogDir

# Start the Vite dev server in a hidden background window.
$stdoutLog = Join-Path $LogDir "frontend.out.log"
$stderrLog = Join-Path $LogDir "frontend.err.log"
$frontendProcess = Start-Process `
    -FilePath $nodePath `
    -ArgumentList "`"$vitePath`" --host 127.0.0.1 --port $FrontendPort --strictPort" `
    -WorkingDirectory $FrontendDir `
    -RedirectStandardOutput $stdoutLog `
    -RedirectStandardError $stderrLog `
    -WindowStyle Hidden `
    -PassThru

Write-Host "Frontend start command launched. PID=$($frontendProcess.Id), URL=http://127.0.0.1:$FrontendPort"

# Confirm the Vite page is reachable.
try {
    Wait-HttpOk -Url "http://127.0.0.1:$FrontendPort" -ServiceName "frontend" -Port $FrontendPort -LaunchProcessId $frontendProcess.Id
}
catch {
    Stop-ProcessTree -ProcessId $frontendProcess.Id
    throw
}
