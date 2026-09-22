"""
手机负载监控（合并自 G:\\MCPLAY\\1.21.1jxdl\\monitor\\monitor_dash.py）
—— 纯被动监控：通过 SSH 定期读取手机 /proc 数据，**绝不执行任何进程操作**
（不 pkill / 不 kill / 不启动 / 不停止任何程序）。连不上就返回 ok=false + 最近数据。

手机: Termux (SSH)，地址 / 端口 / 凭据见 backend/.env 的 PHONE_* 配置
注意: 手机锁屏/后台时 Android 限制读取全局 /proc（stat/loadavg/net 等），
      此时那些指标为空（无数据），内存/磁盘/进程表仍可用。
"""
import logging
import os
import re
import threading
import time
from collections import deque
from pathlib import Path
from fastapi import APIRouter

# 屏蔽 /api/phone/status 轮询请求在 uvicorn access log 的逐条刷屏
class _PhoneAccessFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return "/api/phone/status" not in record.getMessage()


logging.getLogger("uvicorn.access").addFilter(_PhoneAccessFilter())

try:
    import paramiko
except ImportError:
    paramiko = None

# 静默 paramiko 的 INFO 连接/认证日志，避免每 3 秒轮询刷屏后端控制台
if paramiko is not None:
    for _name in ("paramiko", "paramiko.transport", "paramiko.transport.sftp", "paramiko.sftp"):
        logging.getLogger(_name).setLevel(logging.WARNING)
        logging.getLogger(_name).propagate = False
    logging.getLogger("paramiko").setLevel(logging.WARNING)

# 手机 SSH 凭据：从 backend/.env 或环境变量读取，不硬编码在源码中，
# 避免把内网 IP / 用户名 / 密码随代码提交到公开仓库。
_ENV_FILE = Path(__file__).resolve().parent.parent.parent / ".env"  # backend/.env


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


_env_phone = _load_env_file()


def _env_or(name: str, default: str = "") -> str:
    return os.environ.get(name) or _env_phone.get(name) or default


def _env_or_int(name: str, default: int) -> int:
    try:
        return int(_env_or(name, str(default)))
    except ValueError:
        return default


def _env_or_float(name: str, default: float) -> float:
    try:
        return float(_env_or(name, str(default)))
    except ValueError:
        return default


PHONE_HOST = _env_or("PHONE_HOST")
PHONE_PORT = _env_or_int("PHONE_PORT", 8022)
PHONE_USER = _env_or("PHONE_USER")
PHONE_PASS = _env_or("PHONE_PASS")
POLL_INTERVAL = _env_or_float("PHONE_POLL_INTERVAL", 3.0)
HIST_MAX = 200
SERIES_KEEP = 29000  # 24h 全量时间戳采样环（按条数封顶，3s 采样 ≈ 24h）

router = APIRouter(prefix="/api/phone", tags=["phone-monitor"])

_state = {
    "last": None,          # 最近一次成功样本
    "ok": False,
    "err": "",
    "cpu_hist": deque(maxlen=HIST_MAX),
    "mem_hist": deque(maxlen=HIST_MAX),
    "series": [],          # 24h 全量时间戳采样 [(ts,cpu,mem_avail,swap_total,swap_free,disk_used,disk_total,rx,tx)]
    "last_stat": None,     # 上一次 /proc/stat 每核 (idle, total)，用于差分
    "started": False,
}

_SAMPLE_SCRIPT = r"""
set +e
echo "===MEM==="; cat /proc/meminfo 2>/dev/null | grep -E "MemTotal|MemFree|MemAvailable|Cached|Buffers|SwapTotal|SwapFree"
echo "===LOAD==="; cat /proc/loadavg 2>/dev/null || echo NA
echo "===PROC==="; ps -eo pid,pcpu,pmem,rss,comm 2>/dev/null | sort -k2 -nr | head -12
echo "===DF==="; df -P / /data 2>/dev/null
echo "===UPTIME==="; cat /proc/uptime 2>/dev/null | cut -d' ' -f1 || echo NA
echo "===NPROC==="; grep -c ^processor /proc/cpuinfo 2>/dev/null || echo NA
echo "===STAT==="; cat /proc/stat 2>/dev/null
echo "===UTC==="; date +%s
"""


def _parse_text(t: str) -> dict:
    d: dict = {}
    m = re.search(r"===MEM===\n(.*?)(?====)", t, re.S)
    mem = {}
    if m:
        for kv in m.group(1).splitlines():
            if ":" in kv:
                k, v = kv.split(":", 1)
                try:
                    mem[k.strip()] = int(v.strip().split()[0])
                except Exception:
                    pass
    d["mem"] = mem
    lm = re.search(r"===LOAD===\n([0-9. ]+)", t)
    d["load"] = lm.group(1).split() if lm else []
    procs = []
    pm = re.search(r"===PROC===\n(.*?)(?====)", t, re.S)
    if pm:
        # 新版命令 `ps | sort -k2 -nr` 后无表头行，不能固定跳过第 1 行
        # （否则排序第一的进程会被误当表头丢弃）；
        # 统一按「PID 为数字」过滤，兼容有无表头两种情况。
        for ln in pm.group(1).splitlines()[:12]:
            f = ln.split(None, 4)
            if len(f) == 5 and f[0].isdigit():
                procs.append({
                    "pid": f[0], "cpu": f[1], "mem": f[2],
                    "rss": int(f[3]) // 1024 if f[3].isdigit() else 0,
                    "comm": f[4][:42],
                })
    d["procs"] = procs
    d["cpu_sum"] = 0.0
    for p in procs:
        try:
            d["cpu_sum"] += float(p["cpu"])
        except Exception:
            pass
    # /proc/stat 每核累计时间片（idle, total），供两次差分算单核使用率
    st = re.search(r"===STAT===\n(.*?)(?====)", t, re.S)
    stat_cores = None
    if st:
        stat_cores = []
        for ln in st.group(1).splitlines():
            f = ln.split()
            if len(f) < 5 or not f[0].startswith("cpu") or f[0] == "cpu":
                continue
            fields = []
            for x in f[1:9]:
                try:
                    fields.append(int(x))
                except Exception:
                    break
            if len(fields) >= 4:
                idle = fields[3] + (fields[4] if len(fields) > 4 else 0)  # idle + iowait
                stat_cores.append((idle, sum(fields)))
    d["stat_cores"] = stat_cores
    disk = []
    dm = re.search(r"===DF===\n(.*?)(?====)", t, re.S)
    if dm:
        for ln in dm.group(1).splitlines()[1:]:
            f = ln.split()
            if len(f) >= 6 and f[1].isdigit():
                total = int(f[1]) * 512 // 1024  # 512字节块 -> KiB
                if total >= 1024 ** 2 and not f[5].startswith("/data/app"):
                    disk.append({"mnt": f[5], "total": total,
                                 "used": int(f[2]) * 512 // 1024,
                                 "avail": int(f[3]) * 512 // 1024})
    d["disk"] = disk
    u = re.search(r"===UPTIME===\n(\d+)", t)
    d["uptime_s"] = int(u.group(1)) if u else None
    n = re.search(r"===NPROC===\n(\d+)", t)
    d["nproc"] = int(n.group(1)) if n else None
    ut = re.search(r"===UTC===\n(\d+)", t)
    d["ts"] = int(ut.group(1)) if ut else int(time.time())
    return d


_ssh_lock = threading.Lock()
_ssh = None          # 复用的长连接（失败后下次自动重连）


def _get_ssh():
    """获取 SSH 长连接：首次创建，断线重连。只读使用，不执行任何进程操作。"""
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
    client.connect(PHONE_HOST, port=PHONE_PORT, username=PHONE_USER, password=PHONE_PASS,
                   timeout=15, look_for_keys=False, allow_agent=False, banner_timeout=15)
    _ssh = client
    return _ssh


def _ssh_sample():
    """SSH 采样一次（只读命令）。失败抛异常，由调用方记录并重连。"""
    with _ssh_lock:
        ssh = _get_ssh()
        try:
            _, out, _ = ssh.exec_command(_SAMPLE_SCRIPT, timeout=25)
            return _parse_text(out.read().decode("utf-8", errors="replace"))
        except Exception:
            # 连接级故障：标记为失效，下次轮询自动重连
            global _ssh
            try:
                ssh.close()
            except Exception:
                pass
            _ssh = None
            raise


def _poll_once():
    d = _ssh_sample()
    _state["last"] = d
    _state["ok"] = True
    _state["err"] = ""
    m = d.get("mem") or {}
    if m.get("MemAvailable"):
        _state["mem_hist"].append(m["MemAvailable"])
    # CPU 使用率：优先 /proc/stat 两次差分（精确，需能读全局 /proc）；
    # Android 无 root 时读不到 /proc/stat（Permission denied），
    # 回退到 ps 可见进程占用和（近似，标注来源）。
    cores_now = d.get("stat_cores")
    prev = _state["last_stat"]
    _state["last_stat"] = cores_now
    cpu_pct = None
    core_pcts = None
    src = None
    raw_sum = None
    if cores_now and prev and len(cores_now) == len(prev):
        core_pcts = []
        t_sum = b_sum = 0
        for p, n in zip(prev, cores_now):
            p_idle, p_tot = p
            n_idle, n_tot = n
            dt = n_tot - p_tot
            if dt <= 0:
                core_pcts.append(0.0)
                continue
            busy = dt - (n_idle - p_idle)
            core_pcts.append(round(max(0.0, min(100.0, 100.0 * busy / dt)), 1))
            t_sum += dt
            b_sum += max(0, n_idle - p_idle)
        if t_sum > 0:
            cpu_pct = round(max(0.0, min(100.0, 100.0 * (t_sum - b_sum) / t_sum)), 1)
        elif core_pcts:
            cpu_pct = round(sum(core_pcts) / len(core_pcts), 1)
        src = "stat"
    else:
        s = d.get("cpu_sum")
        if s is not None and s > 0:
            raw_sum = round(s, 1)
            cpu_pct = min(100.0, raw_sum)  # 统一 0-100 显示口径
            src = "ps"
    d["cpu"] = cpu_pct
    d["cores"] = core_pcts
    d["cpu_src"] = src
    d["cpu_sum_raw"] = raw_sum
    if cpu_pct is not None:
        _state["cpu_hist"].append(cpu_pct)
    # 24h 全量时间戳采样（前端折线图 / 悬停时间段记录）；手机无网络计数器，rx/tx 为 None
    m = d.get("mem") or {}
    disk_used = sum((x.get("used") or 0) for x in d.get("disk", []))
    disk_total = sum((x.get("total") or 0) for x in d.get("disk", []))
    _state["series"].append((
        d.get("ts", int(time.time())), cpu_pct, m.get("MemAvailable"),
        m.get("SwapTotal") or 0, m.get("SwapFree") or 0,
        disk_used, disk_total, None, None,
    ))
    if len(_state["series"]) > SERIES_KEEP:
        del _state["series"][: len(_state["series"]) - SERIES_KEEP]


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


def poll_loop():
    """后台轮询线程：只读采样，失败仅记录，绝不杀进程。"""
    while True:
        try:
            _poll_once()
        except Exception as e:
            _state["ok"] = False
            _state["err"] = repr(e)[:200]
        time.sleep(POLL_INTERVAL)


def start_poller():
    """启动轮询线程（幂等）。在应用 lifespan 中调用。"""
    if _state["started"]:
        return
    _state["started"] = True
    if paramiko is None:
        _state["err"] = "backend 缺少 paramiko 依赖（pip install paramiko）"
        return
    threading.Thread(target=poll_loop, daemon=True).start()


@router.get("/status")
async def phone_status():
    """手机最新负载快照 + 历史曲线。

    纯被动数据：连不上时 ok=false，页面显示“无数据”，不影响任何进程。
    """
    d = _state["last"]
    return {
        "ok": _state["ok"],
        "err": _state["err"],
        "ts": (d or {}).get("ts", 0),
        "cpu": (d or {}).get("cpu"),
        "cores": (d or {}).get("cores"),
        "cpu_src": (d or {}).get("cpu_src"),
        "cpu_sum_raw": (d or {}).get("cpu_sum_raw"),
        "load": (d or {}).get("load", []),
        "mem": (d or {}).get("mem", {}),
        "disk": (d or {}).get("disk", []),
        "procs": (d or {}).get("procs", []),
        "uptime_s": (d or {}).get("uptime_s"),
        "nproc": (d or {}).get("nproc"),
        "hist": {
            "cpu": list(_state["cpu_hist"]),
            "mem": list(_state["mem_hist"]),
        },
    }


@router.get("/series")
async def phone_series(start: int = 0, end: int = 0, limit: int = 900):
    """时间窗历史采样（自动降采样），供前端折线图缩放/平移按需拉取。"""
    now = int(time.time())
    s0 = start or now - 86400
    s1 = end or now
    return {"series": _series_dicts(_state["series"], s0, s1, max(50, min(limit, 1200)))}