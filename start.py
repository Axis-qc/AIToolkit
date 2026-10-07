import subprocess
import sys
import os
import signal
import time
import re
import threading
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "web" / "aitoolkit"
LOG_DIR = ROOT / "logs"

BACKEND_PORT = 18000

# 看门狗参数。后端卡死的形态是进程还在、端口还在 LISTENING，但 /api/health
# 不响应：向量编码（core/recall.py 的 emb.encode_texts）是同步 CPU 推理，
# 跑在 uvicorn 的事件循环线程上，循环被占死后连健康检查都排不上队，进程内
# 的重启接口同样无效。所以探活与强杀必须由后端进程之外的地方做，这里用
# start.py 自己的守护线程，它随 start 一起起来、也随 start 一起退出。
WATCHDOG_INTERVAL_SECONDS = 30
WATCHDOG_TIMEOUT_SECONDS = 8
WATCHDOG_FAIL_THRESHOLD = 3

# 「忙 vs 死」判据：探活失败后采样一段时间的 CPU 增量，超过阈值即认定在干活。
# sample 取 3 秒、门槛 0.6s（约合单核 20%），远比编码期的实际占用（约 100%）宽松，
# 目的是只放过明显在烧 CPU 的进程，不误放真正挂起（CPU 不增长）的进程。
WATCHDOG_BUSY_SAMPLE_SECONDS = 3
WATCHDOG_BUSY_CPU_SECONDS = 0.6


def _backend_healthy(timeout):
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{BACKEND_PORT}/api/health", timeout=timeout)
        return True
    except Exception:
        return False


def _spawn_backend(uvicorn):
    return subprocess.Popen(
        [str(uvicorn), "app.main:app", "--host", "0.0.0.0", "--port", str(BACKEND_PORT)],
        cwd=BACKEND,
    )


def _pids_listening_on_port():
    """占用 BACKEND_PORT 的监听进程号。

    Windows 上 uvicorn.exe 是 distlib 启动器，真正监听端口的是它拉起的
    python 子进程；只杀启动器会留下一个仍占着端口的孤儿后端，所以按端口
    反查 PID，而不是只认 Popen 返回的那个 pid。
    """
    try:
        out = subprocess.run(
            ["netstat", "-ano", "-p", "TCP"],
            capture_output=True, text=True, timeout=15,
        ).stdout
    except Exception:
        return []
    pids = set()
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[3] == "LISTENING" and parts[1].endswith(f":{BACKEND_PORT}"):
            try:
                pids.add(int(parts[4]))
            except ValueError:
                pass
    return sorted(pids)


def _kill_backend_tree(proc):
    """杀掉后端：先按端口找真正监听的进程，再兜底杀 Popen 句柄与子树。"""
    killed = []
    for pid in _pids_listening_on_port():
        try:
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                           capture_output=True, timeout=15)
            killed.append(pid)
        except Exception as exc:
            print(f"  Watchdog : taskkill {pid} failed: {exc}")
    if proc is not None and proc.poll() is None:
        try:
            proc.kill()
        except Exception:
            pass
    if proc is not None:
        try:
            proc.wait(timeout=10)
        except Exception:
            pass
    return killed


def _cpu_seconds(pid):
    """进程累计 CPU 秒数（kernel + user）。取不到返回 None。

    Windows 没有 psutil 时只能走 ctypes。注意 GetProcessTimes 的四个时间
    参数都必须传有效指针，传 None 会直接访问违例崩掉整个 start.py。
    """
    if os.name != "nt":
        return None
    import ctypes
    import ctypes.wintypes as wintypes

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    k32 = ctypes.windll.kernel32
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k32.GetProcessTimes.argtypes = [
        wintypes.HANDLE,
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
    ]

    class _FileTime(ctypes.Structure):
        _fields_ = [("lo", wintypes.DWORD), ("hi", wintypes.DWORD)]

    handle = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not handle:
        return None
    try:
        created, exited, kernel, user = _FileTime(), _FileTime(), _FileTime(), _FileTime()
        ok = k32.GetProcessTimes(
            handle, ctypes.byref(created), ctypes.byref(exited),
            ctypes.byref(kernel), ctypes.byref(user),
        )
        if not ok:
            return None
        return (
            ((kernel.hi << 32) | kernel.lo) / 1e7
            + ((user.hi << 32) | user.lo) / 1e7
        )
    finally:
        k32.CloseHandle(handle)


def _backend_busy(proc):
    """后端是否在持续烧 CPU（=正在干活，只是事件循环被同步推理占满）。

    采样窗口内 CPU 时间明显增长即判定为忙。判据用「增长」而非绝对占用率：
    进程刚重启时累计值很低，只看绝对值会把刚开始回填的进程误判为空闲。
    """
    pids = _pids_listening_on_port()
    if not pids and proc is not None and proc.poll() is None:
        pids = [proc.pid]
    if not pids:
        return False

    def total():
        vals = [_cpu_seconds(p) for p in pids]
        vals = [v for v in vals if v is not None]
        return sum(vals) if vals else None

    before = total()
    if before is None:
        return False
    time.sleep(WATCHDOG_BUSY_SAMPLE_SECONDS)
    after = total()
    if after is None:
        return False
    return (after - before) >= WATCHDOG_BUSY_CPU_SECONDS


def _watchdog_loop(holder, stop_event, uvicorn):
    """连续探活失败就强杀后端并拉起，修的是事件循环被占死那种卡顿。"""
    fails = 0
    while not stop_event.wait(WATCHDOG_INTERVAL_SECONDS):
        if _backend_healthy(WATCHDOG_TIMEOUT_SECONDS):
            if fails:
                print("  Watchdog : backend recovered, counter reset")
            fails = 0
            continue

        fails += 1
        print(f"  Watchdog : health check failed ({fails}/{WATCHDOG_FAIL_THRESHOLD})")
        if fails < WATCHDOG_FAIL_THRESHOLD:
            continue

        # 关键判据：探活失败不等于卡死。向量回填是分钟级同步 CPU 推理，期间
        # 事件循环排不上健康检查，但进程完全健康；此时强杀会丢掉它算到一半的
        # 工作，重启后待算量分毫未减，于是「杀→重启→再回填→再被杀」无限循环。
        # 进程在持续烧 CPU 就说明它在干活，只警告、不杀。
        busy = _backend_busy(holder.get("proc"))
        if busy:
            print("  Watchdog : backend unresponsive but burning CPU (working), not killing")
            fails = 0
            continue

        print("  Watchdog : killing unresponsive backend")
        killed = _kill_backend_tree(holder.get("proc"))
        if killed:
            print(f"  Watchdog : killed pid(s) {', '.join(str(p) for p in killed)}")

        holder["proc"] = _spawn_backend(uvicorn)
        print(f"  Watchdog : backend restarted (pid={holder['proc'].pid})")
        fails = 0


def check(cmd, name):
    try:
        subprocess.run(["cmd", "/c", f"{cmd} --version"], capture_output=True, check=True)
    except Exception:
        print(f"[ERROR] {name} not found. Install it first.")
        input()
        sys.exit(1)


def main():
    print("=" * 50)
    print("  AI Knowledge Graph Memory System")
    print("=" * 50)
    print()

    check("python", "Python")
    check("node", "Node.js")

    # .env
    env_file = BACKEND / ".env"
    if not env_file.exists():
        import shutil
        shutil.copy(BACKEND / ".env.example", env_file)
        print("[INFO] Created backend/.env - edit it first")
        print()

    # Backend deps
    print("Installing backend dependencies...")
    venv = BACKEND / ".venv"
    if not venv.exists():
        subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)
    pip = venv / "Scripts" / "pip.exe"
    subprocess.run([str(pip), "install", "-r", "requirements.txt", "-q"], cwd=BACKEND, check=True)

    # Frontend deps
    print("Installing frontend dependencies...")
    subprocess.run(["cmd", "/c", "npm install --silent"], cwd=FRONTEND, check=True)

    # Log dir
    LOG_DIR.mkdir(exist_ok=True)
    frontend_log = open(LOG_DIR / "frontend.log", "w", encoding="utf-8")

    # Start backend (stdout → terminal, stderr → terminal)
    uvicorn = venv / "Scripts" / "uvicorn.exe"
    backend_holder = {"proc": _spawn_backend(uvicorn)}
    print(f"  Backend  : starting (pid={backend_holder['proc'].pid})")

    # Wait and verify backend
    for i in range(20):
        time.sleep(0.5)
        if backend_holder["proc"].poll() is not None:
            print(f"  Backend  : CRASHED (exit={backend_holder['proc'].poll()})")
            chat_log = BACKEND / "data" / "chat.log"
            if chat_log.exists():
                tail = chat_log.read_text(encoding="utf-8").splitlines()[-15:]
                print(f"  Log tail :\n" + "\n".join(tail))
            input("Press Enter to exit...")
            sys.exit(1)
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{BACKEND_PORT}/api/health", timeout=2)
            print(f"  Backend  : ready (http://localhost:{BACKEND_PORT})")
            break
        except Exception:
            pass
    else:
        print("  Backend  : WARNING - health check timed out")

    # 看门狗随 start 一起跑：它和后端同在一个提权进程里，杀得动管理员后端；
    # stop_event 让 Ctrl+C 时它一并退出，不留在后台反复拉起已经不该跑的后端。
    watchdog_stop = threading.Event()
    threading.Thread(
        target=_watchdog_loop,
        args=(backend_holder, watchdog_stop, uvicorn),
        daemon=True,
    ).start()
    print("  Watchdog : on (probes every %ds)" % WATCHDOG_INTERVAL_SECONDS)

    # Start frontend
    frontend_proc = subprocess.Popen(
        ["cmd", "/c", "npx vite --host 0.0.0.0 --port 15173"],
        cwd=FRONTEND,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    ansi_re = re.compile(rb'\x1b\[[0-9;]*[a-zA-Z]')
    def _filter_log():
        pipe = frontend_proc.stdout
        if pipe is None:
            return
        for line in pipe:
            clean = ansi_re.sub(b'', line)
            frontend_log.write(clean.decode('utf-8', errors='replace'))
            frontend_log.flush()
    threading.Thread(target=_filter_log, daemon=True).start()
    print(f"  Frontend : starting (pid={frontend_proc.pid})")
    time.sleep(3)
    print("  Frontend : http://localhost:15173")

    print()
    print("=" * 50)
    print("  API Docs : http://localhost:18000/docs")
    print("  Logs     : data/chat.log, logs/frontend.log")
    print("=" * 50)
    print("Press Ctrl+C to stop")
    print()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping services...")
        watchdog_stop.set()
        backend_holder["proc"].terminate()
        frontend_proc.terminate()
        frontend_log.close()
        try:
            backend_holder["proc"].wait(timeout=5)
            frontend_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        print("Done.")


if __name__ == "__main__":
    main()
