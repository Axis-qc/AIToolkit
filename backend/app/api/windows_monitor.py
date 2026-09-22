"""
Windows 新服务器性能监控（只读 SSH + PowerShell）
—— 通过 SSH 连新服务器（administrator），周期性执行 PowerShell 采集
   CPU / 内存 / 磁盘 / 进程 / 网络流量，解析为与 phone_monitor 相同形状的 JSON，
   供前端 McPanelView 的 KPI 带 / 进程表直接复用（前端只切数据源）。

与 phone_monitor 不同：这里是 Windows（无 /proc），改用 PowerShell 计数器，
CPU 为真实占用（Get-Counter），比手机 Termux 的 ps 近似值更准更全。
只读，绝不执行任何进程操作（不 kill / 不启动 / 不停止）。

配置（backend/.env，不硬编码）：
  WIN_HOST / WIN_PORT / WIN_USER / WIN_PASS —— 新服务器 SSH 凭据
  WIN_POLL_INTERVAL —— 轮询间隔（秒）
"""
import json
import logging
import os
import re
import threading
import time
from collections import deque
from pathlib import Path
from fastapi import APIRouter

# 屏蔽 /api/win/status 轮询在 uvicorn access log 的刷屏
class _WinAccessFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return "/api/win/status" not in record.getMessage()


logging.getLogger("uvicorn.access").addFilter(_WinAccessFilter())

try:
    import paramiko
except ImportError:
    paramiko = None

if paramiko is not None:
    for _name in ("paramiko", "paramiko.transport", "paramiko.transport.sftp", "paramiko.sftp"):
        logging.getLogger(_name).setLevel(logging.WARNING)
        logging.getLogger(_name).propagate = False
    logging.getLogger("paramiko").setLevel(logging.WARNING)

# 读取 .env（同 phone_monitor 读取方式，不硬编码）
_ENV_FILE = Path(__file__).resolve().parent.parent.parent / ".env"


def _load_env_file() -> dict[str, str]:
    env: dict[str, str] = {}
    try:
        for line in _ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            env[key.strip()] = value.strip().strip('"').strip("'")
    except OSError:
        pass
    return env


_env_win = _load_env_file()


def _env_or(name: str, default: str = "") -> str:
    return os.environ.get(name) or _env_win.get(name) or default


def _env_or_float(name: str, default: float) -> float:
    try:
        return float(_env_or(name, str(default)))
    except ValueError:
        return default


WIN_HOST = _env_or("WIN_HOST")
WIN_PORT = int(_env_or("WIN_PORT", "22") or 22)
WIN_USER = _env_or("WIN_USER")
WIN_PASS = _env_or("WIN_PASS")
POLL_INTERVAL = _env_or_float("WIN_POLL_INTERVAL", 3.0)
HIST_MAX = 200
SERIES_KEEP = 29000  # 24h 全量时间戳采样环（按条数封顶，3s 采样 ≈ 24h）

router = APIRouter(prefix="/api/win", tags=["win-monitor"])

_state = {
    "last": None,
    "ok": False,
    "err": "",
    "cpu_hist": deque(maxlen=HIST_MAX),
    "mem_hist": deque(maxlen=HIST_MAX),
    "series": [],  # 24h 全量时间戳采样 [(ts,cpu,mem_avail,swap_total,swap_free,disk_used,disk_total,rx,tx)]
    "started": False,
}

# PowerShell 采样脚本。注意：SSH 通道经服务器端 cmd.exe /c 执行
# `powershell -EncodedCommand <base64>`，受 cmd 单条命令 ~8191 字符上限约束，
# 故脚本必须紧凑（去注释/去缩进），base64 需压在 8000 以下。
# 只依赖在目标 Windows Server 实测可用的命令；所有中间变量在脚本内定义，绝不引用未定义变量。
_SAMPLE_SCRIPT = r"""
$ErrorActionPreference='Stop'
$os=Get-CimInstance Win32_OperatingSystem
$memTotal=[long]$os.TotalVisibleMemorySize
$memFree=[long]$os.FreePhysicalMemory
$swapTotal=0;$swapFree=0
$pf=Get-CimInstance Win32_PageFileUsage
if($pf){$swapTotal=[long]((($pf|Measure-Object AllocatedBaseSize -Sum).Sum)*1024);$swapUsed=[long]((($pf|Measure-Object CurrentUsage -Sum).Sum)*1024);$swapFree=[long]($swapTotal-$swapUsed)}
$cores=@()
try{$coreSamples=(Get-Counter '\Processor(*)\% Processor Time' -SampleInterval 1 -MaxSamples 2).CounterSamples|Where-Object{$_.InstanceName -ne '_total'};foreach($g in($coreSamples|Group-Object InstanceName)){$cores+=[math]::Round([double]$g.Group.CookedValue[-1],1)}}catch{$cores=@()}
$nproc=$cores.Count
$cpuAvg=if($nproc -gt 0){[math]::Round(($cores|Measure-Object -Average).Average,1)}else{0}
$disk=@()
foreach($d in(Get-PSDrive -PSProvider FileSystem)){if($null -eq $d.Root){continue};$used=[long]$d.Used;$free=[long]$d.Free;$total=$used+$free;if($total -le 0){continue};$disk+=@{mnt=[string]$d.Root;total=$total;used=$used;avail=$free}}
$net=@(Get-NetAdapterStatistics|Where-Object{$_.Name -notlike '*Loopback*'})
$rx_kb_s=0;$tx_kb_s=0
if($net.Count -gt 0){$rx1=[double]($net|Measure-Object ReceivedBytes -Sum).Sum;$tx1=[double]($net|Measure-Object SentBytes -Sum).Sum;Start-Sleep -Milliseconds 1000;$net2=@(Get-NetAdapterStatistics|Where-Object{$_.Name -notlike '*Loopback*'});if($net2.Count -gt 0){$rx2=[double]($net2|Measure-Object ReceivedBytes -Sum).Sum;$tx2=[double]($net2|Measure-Object SentBytes -Sum).Sum;$rx_kb_s=if($rx2 -ge $rx1){[math]::Round(($rx2-$rx1)/1024.0,1)}else{0};$tx_kb_s=if($tx2 -ge $tx1){[math]::Round(($tx2-$tx1)/1024.0,1)}else{0}}}else{Start-Sleep -Milliseconds 1000}
$snap1=@{}
foreach($p in(Get-Process|Where-Object{$_.Id -gt 0})){try{$snap1[$p.Id]=$p.TotalProcessorTime.TotalMilliseconds}catch{}}
Start-Sleep -Milliseconds 1000
$procs=@()
foreach($p in(Get-Process|Where-Object{$_.Id -gt 0})){$id=$p.Id;try{$t2=$p.TotalProcessorTime.TotalMilliseconds}catch{$t2=$null};$dt=if($snap1.ContainsKey($id) -and $t2 -ne $null){($t2-$snap1[$id])/1000.0}else{0};$pct=[math]::Min(100.0,[math]::Round($dt*100,1));$r=[long]$p.WorkingSet64;if($r -lt 0){$r=0};$rss=[long]($r/1KB);$memPct=if($memTotal -gt 0){[math]::Round(($rss/$memTotal)*100,1)}else{0};$procs+=@{pid=[string]$id;cpu=[string]$pct;mem=[string]$memPct;rss=$rss;comm=[string]$p.ProcessName}}
$procs=$procs|Sort-Object{[double]$_.rss} -Descending|Select-Object -First 12
$uptime_s=-1
try{$uptime_s=[int]((Get-Date)-$os.LastBootUpTime).TotalSeconds}catch{$uptime_s=-1}
$result=@{cpu=$cpuAvg;cores=$cores;cpu_src='counter';cpu_sum_raw=$null;load=@();mem=@{MemTotal=$memTotal;MemFree=$memFree;MemAvailable=$memFree;SwapTotal=$swapTotal;SwapFree=$swapFree;Cached=0;Buffers=0};disk=$disk;procs=$procs;uptime_s=$uptime_s;nproc=$nproc;rx_kb_s=$rx_kb_s;tx_kb_s=$tx_kb_s}
$result|ConvertTo-Json -Compress -Depth 4
"""

def _ps_encoded(script: str) -> str:
    import base64
    return base64.b64encode(script.encode("utf-16le")).decode("ascii")


_ssh_lock = threading.Lock()
_ssh = None


def _get_ssh():
    global _ssh
    if _ssh is not None:
        t = _ssh.get_transport()
        if t is not None and t.is_active():
            return _ssh
        try:
            _ssh.close()
        except Exception:
            pass
        _ssh = None
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(WIN_HOST, port=WIN_PORT, username=WIN_USER, password=WIN_PASS,
                   timeout=15, look_for_keys=False, allow_agent=False, banner_timeout=15)
    _ssh = client
    return _ssh


def _parse_json(text: str) -> dict:
    # 取最后一行 JSON 对象（避免 PowerShell 偶发的前置警告行）
    for line in reversed(text.splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                return json.loads(line)
            except Exception:
                pass
    raise ValueError("无法从 PowerShell 输出解析 JSON")


def _ssh_sample():
    with _ssh_lock:
        ssh = _get_ssh()
        try:
            cmd = "powershell -NoProfile -EncodedCommand " + _ps_encoded(_SAMPLE_SCRIPT)
            _, out, _ = ssh.exec_command(cmd, timeout=30)
            return _parse_json(out.read().decode("utf-8", errors="replace"))
        except Exception:
            global _ssh
            try:
                ssh.close()
            except Exception:
                pass
            _ssh = None
            raise


def _tuple_to_dict(s):
    """采样元组 → 前端 JSON 字典。"""
    return {
        "ts": s[0], "cpu": s[1], "mem_avail": s[2],
        "swap_total": s[3], "swap_free": s[4],
        "disk_used": s[5], "disk_total": s[6],
        "rx": s[7], "tx": s[8],
    }


def _series_dicts(samples, from_ts, to_ts, max_points):
    """取 [from_ts, to_ts] 区间采样，降采样到最多 max_points 点（分桶均值）。"""
    pts = [s for s in samples if from_ts <= s[0] <= to_ts]
    n = len(pts)
    if n == 0:
        return []
    if n <= max_points:
        return [_tuple_to_dict(s) for s in pts]
    bucket = n / max_points
    out = []
    for i in range(max_points):
        lo = int(i * bucket)
        hi = max(lo + 1, int((i + 1) * bucket))
        chunk = pts[lo:hi]

        def avg(idx):
            vals = [c[idx] for c in chunk if c[idx] is not None]
            return round(sum(vals) / len(vals), 2) if vals else None

        d = _tuple_to_dict(chunk[-1])
        d["cpu"] = avg(1)
        d["mem_avail"] = avg(2)
        d["swap_total"] = avg(3)
        d["swap_free"] = avg(4)
        d["disk_used"] = avg(5)
        d["disk_total"] = avg(6)
        d["rx"] = avg(7)
        d["tx"] = avg(8)
        out.append(d)
    return out


def _poll_once():
    d = _ssh_sample()
    d["ts"] = int(time.time())
    _state["last"] = d
    _state["ok"] = True
    _state["err"] = ""
    m = d.get("mem") or {}
    if m.get("MemAvailable"):
        _state["mem_hist"].append(m["MemAvailable"])
    if d.get("cpu") is not None:
        _state["cpu_hist"].append(d["cpu"])
    # 24h 全量时间戳采样（前端折线图 / 悬停时间段记录）
    disk_used = sum((x.get("used") or 0) for x in d.get("disk", []))
    disk_total = sum((x.get("total") or 0) for x in d.get("disk", []))
    _state["series"].append((
        d["ts"], d.get("cpu"), m.get("MemAvailable"),
        m.get("SwapTotal") or 0, m.get("SwapFree") or 0,
        disk_used, disk_total,
        d.get("rx_kb_s"), d.get("tx_kb_s"),
    ))
    if len(_state["series"]) > SERIES_KEEP:
        del _state["series"][: len(_state["series"]) - SERIES_KEEP]


def poll_loop():
    while True:
        try:
            _poll_once()
        except Exception as e:
            _state["ok"] = False
            _state["err"] = repr(e)[:200]
        time.sleep(POLL_INTERVAL)


def start_poller():
    if _state["started"]:
        return
    _state["started"] = True
    if paramiko is None:
        _state["err"] = "backend 缺少 paramiko 依赖（pip install paramiko）"
        return
    if not (WIN_HOST and WIN_USER and WIN_PASS):
        _state["err"] = "未配置 Windows 监控 SSH 凭据（WIN_HOST/WIN_USER/WIN_PASS）"
        return
    threading.Thread(target=poll_loop, daemon=True).start()


@router.get("/status")
async def win_status():
    d = _state["last"] or {}
    return {
        "ok": _state["ok"],
        "err": _state["err"],
        "ts": d.get("ts", int(time.time())),
        "cpu": d.get("cpu"),
        "cores": d.get("cores"),
        "cpu_src": d.get("cpu_src"),
        "cpu_sum_raw": d.get("cpu_sum_raw"),
        "load": d.get("load", []),
        "mem": d.get("mem", {}),
        "disk": d.get("disk", []),
        "procs": d.get("procs", []),
        "uptime_s": d.get("uptime_s"),
        "nproc": d.get("nproc"),
        "rx_kb_s": d.get("rx_kb_s"),
        "tx_kb_s": d.get("tx_kb_s"),
        "hist": {
            "cpu": list(_state["cpu_hist"]),
            "mem": list(_state["mem_hist"])
        }
    }


@router.get("/series")
async def win_series(start: int = 0, end: int = 0, limit: int = 900):
    """时间窗历史采样（自动降采样），供前端折线图缩放/平移按需拉取。"""
    now = int(time.time())
    s0 = start or now - 86400
    s1 = end or now
    return {"series": _series_dicts(_state["series"], s0, s1, max(50, min(limit, 1200)))}