<#
  强制重启 AIToolkit 后端。

  用途：后端卡死时救回。卡死的典型表现是进程还在、端口还在 LISTENING，
  但 /api/health 不响应，且 graph.db-wal 长时间不更新。根因是向量编码
  （core/recall.py 里 emb.encode_texts）是同步 CPU 推理，直接跑在 uvicorn
  的事件循环线程上，一旦有大量过期向量要重算就会把循环占死，此时连
  健康检查都排不上队，进程内的重启接口同样无效，只能用外部手段强杀。

  需要管理员权限：后端由管理员启动，普通权限进程杀不掉它。本脚本会
  自行检测并请求提权。

  用法（在管理员 PowerShell 里）：
    .\restart_backend.ps1
    .\restart_backend.ps1 -Port 18000
    .\restart_backend.ps1 -SkipHealthCheck     # 不检测，直接杀+起
    .\restart_backend.ps1 -OnlyKill            # 只杀不起
#>
[CmdletBinding()]
param(
    [int]$Port = 18000,
    [int]$HealthWaitSeconds = 60,
    [switch]$SkipHealthCheck,
    [switch]$OnlyKill
)

$ErrorActionPreference = 'Stop'
$root    = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $root 'backend'
$venv    = Join-Path $backend '.venv'
$uvicorn = Join-Path $venv 'Scripts\uvicorn.exe'
$logDir  = Join-Path $root 'logs'
$outLog  = Join-Path $logDir 'backend.out.log'
$errLog  = Join-Path $logDir 'backend.err.log'

function Test-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
}

if (-not (Test-Admin)) {
    Write-Host "需要管理员权限，正在请求提权……" -ForegroundColor Yellow
    $argList = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $PSCommandPath,
                 '-Port', $Port, '-HealthWaitSeconds', $HealthWaitSeconds)
    if ($SkipHealthCheck) { $argList += '-SkipHealthCheck' }
    if ($OnlyKill)        { $argList += '-OnlyKill' }
    Start-Process powershell -Verb RunAs -ArgumentList $argList
    exit 0
}

Write-Host "=== 强制重启后端 (端口 $Port) ===" -ForegroundColor Cyan

# 1) 找出占用端口的进程
$conns = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if (-not $conns) {
    Write-Host "端口 $Port 无监听进程。" -ForegroundColor Yellow
} else {
    $pids = $conns | Select-Object -ExpandProperty OwningProcess -Unique
    foreach ($procId in $pids) {
        $p = Get-Process -Id $procId -ErrorAction SilentlyContinue
        if ($p) {
            Write-Host ("  发现 pid={0} ({1})，开始内存 {2:N0} MB" -f `
                $p.Id, $p.ProcessName, ($p.WorkingSet64 / 1MB))
            Write-Host "  强制结束……" -ForegroundColor Yellow
            Stop-Process -Id $p.Id -Force
        }
    }
    Start-Sleep -Seconds 3
}

# 兜底：端口句柄可能被没清干净的 uvicorn/python 占着
$leftover = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($leftover) {
    $still = $leftover | Select-Object -ExpandProperty OwningProcess -Unique
    foreach ($procId in $still) {
        Write-Host "  pid=$procId 仍在监听，再次强杀" -ForegroundColor Yellow
        Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Seconds 2
}

if ($OnlyKill) {
    Write-Host "已按要求只杀不起。" -ForegroundColor Green
    exit 0
}

# 2) 重新拉起
if (-not (Test-Path $uvicorn)) {
    Write-Host "找不到 uvicorn：$uvicorn" -ForegroundColor Red
    Write-Host "请确认 backend\.venv 已安装依赖。" -ForegroundColor Red
    exit 1
}
New-Item -ItemType Directory -Path $logDir -Force | Out-Null

Write-Host "启动后端……"
$proc = Start-Process -FilePath $uvicorn `
    -ArgumentList 'app.main:app', '--host', '0.0.0.0', '--port', "$Port" `
    -WorkingDirectory $backend `
    -RedirectStandardOutput $outLog `
    -RedirectStandardError  $errLog `
    -PassThru
Write-Host ("  pid={0}" -f $proc.Id)

# 3) 等健康检查
if ($SkipHealthCheck) {
    Write-Host "已跳过健康检查。" -ForegroundColor Green
    exit 0
}

$deadline = (Get-Date).AddSeconds($HealthWaitSeconds)
$ok = $false
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 2
    if ($proc.HasExited) {
        Write-Host ("后端进程意外退出，exit={0}" -f $proc.ExitCode) -ForegroundColor Red
        if (Test-Path $errLog) {
            Write-Host "--- stderr 尾部 ---" -ForegroundColor DarkGray
            Get-Content $errLog -Tail 25
        }
        exit 1
    }
    try {
        $r = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/api/health" `
             -TimeoutSec 5 -UseBasicParsing
        if ($r.StatusCode -eq 200) { $ok = $true; break }
    } catch { }
}

if ($ok) {
    Write-Host ("后端已就绪：http://127.0.0.1:{0}/api/health" -f $Port) -ForegroundColor Green
} else {
    Write-Host ("等了 {0} 秒仍未通过健康检查。" -f $HealthWaitSeconds) -ForegroundColor Red
    Write-Host "可能仍在加载嵌入模型，稍等片刻再试；若持续不返回，看日志：" -ForegroundColor Yellow
    Write-Host "  $errLog"
    exit 1
}
