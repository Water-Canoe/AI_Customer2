$ErrorActionPreference = "Stop"

# 内部脚本统一从工程根目录解析路径，不依赖调用者当前目录。
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$BackendDir = Join-Path $ProjectRoot "backend"
$FrontendDir = Join-Path $ProjectRoot "frontend"
$LogDir = Join-Path $ProjectRoot "output\logs"
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
        $Process,
        [ValidateSet("backend", "frontend")]
        [string]$ServiceName
    )

    # 同时核对工程路径和服务入口；不能仅凭 uvicorn/vite 名称认领其它工程。
    $commandLine = [string]$Process.CommandLine
    $rootPattern = [regex]::Escape($ProjectRoot.TrimEnd('\')) + '[\\/]'
    if ($ServiceName -eq "frontend") {
        return $commandLine -match $rootPattern -and $commandLine -match 'frontend[\\/].*vite[\\/]bin[\\/]vite\.js'
    }
    $backendPattern = '(?:^|\s)(?:-m\s+app\.main|uvicorn\s+app\.main:app)(?:\s|$)'
    if ($commandLine -notmatch $backendPattern) { return $false }
    if ($commandLine -match $rootPattern) { return $true }
    # Windows venv 会启动基础 Python 子进程；其父进程仍带有本工程解释器路径。
    $parent = Get-CimInstance Win32_Process -Filter "ProcessId = $($Process.ParentProcessId)" -ErrorAction SilentlyContinue
    return $parent -and [string]$parent.CommandLine -match $rootPattern -and [string]$parent.CommandLine -match $backendPattern
}

function Get-ProjectListener {
    param([int]$Port, [string]$ServiceName)

    $processes = @(Get-ListenerProcesses -Port $Port)
    foreach ($process in $processes) {
        if (-not (Test-ProjectOwnedProcess -Process $process -ServiceName $ServiceName)) {
            throw "Port $Port is occupied by a non-project $ServiceName process. PID=$($process.ProcessId)"
        }
    }
    if ($processes.Count -gt 1) { throw "Multiple $ServiceName processes listen on port $Port." }
    if ($processes.Count) { return $processes[0] }
}

function Stop-ProcessTree {
    param(
        [Parameter(Mandatory = $true)]
        [int]$ProcessId
    )

    # Stop the explicit process tree so child Python/browser processes do not survive a restart.
    if (-not (Get-Process -Id $ProcessId -ErrorAction SilentlyContinue)) { return }
    & taskkill.exe /PID $ProcessId /T /F | Out-Null
    if ($LASTEXITCODE -ne 0 -and (Get-Process -Id $ProcessId -ErrorAction SilentlyContinue)) {
        throw "Cannot stop process tree: PID=$ProcessId"
    }
}

function Stop-ProjectProcessesOnPort {
    param(
        [Parameter(Mandatory = $true)]
        [int]$Port,
        [Parameter(Mandatory = $true)]
        [string]$ServiceName
    )

    $process = Get-ProjectListener -Port $Port -ServiceName $ServiceName
    if (-not $process) { return }
    if ($ServiceName -eq "backend") {
        # 让后端通过 lifespan 清理私信、任务和浏览器；退出失败时不强杀。
        Wait-HttpOk -Url "http://127.0.0.1:$Port/api/health" -ServiceName $ServiceName -Port $Port -TimeoutSeconds 3
        $result = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/system/exit" -Method Post -TimeoutSec 5
        if (-not $result.ok) { throw "Backend refused graceful shutdown." }
    }
    else { Stop-ProcessTree -ProcessId ([int]$process.ProcessId) }
    for ($index = 0; $index -lt 120; $index++) {
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
        [int]$Port,
        [int]$LaunchProcessId = 0,
        [int]$TimeoutSeconds = 30
    )

    # 请求有独立超时；只接受 HTTP 200，后端还要匹配产品与监听进程。
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if ($LaunchProcessId -and -not (Get-Process -Id $LaunchProcessId -ErrorAction SilentlyContinue)) {
            throw "$ServiceName exited before becoming ready. Check logs in $LogDir."
        }
        $listener = Get-ProjectListener -Port $Port -ServiceName $ServiceName
        if ($listener) {
            if ($LaunchProcessId -and $listener.ProcessId -ne $LaunchProcessId -and $listener.ParentProcessId -ne $LaunchProcessId) {
                throw "The $ServiceName listener does not belong to the process just launched."
            }
            try {
                $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 2
                $ready = [int]$response.StatusCode -eq 200
                if ($ready -and $ServiceName -eq "backend") {
                    $health = $response.Content | ConvertFrom-Json
                    $ready = $health.status -eq "ok" -and $health.product -eq "ai-customer" -and [int]$health.process_id -eq [int]$listener.ProcessId
                }
                if ($ready) { Write-Host "$ServiceName is ready: $Url"; return }
            }
            catch {
                # 启动期间暂时不可访问，继续在总时限内等待。
            }
        }
        Start-Sleep -Milliseconds 500
    }

    throw "$ServiceName did not become ready in $TimeoutSeconds seconds. Check logs in $LogDir."
}
