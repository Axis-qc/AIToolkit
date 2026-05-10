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
FRONTEND = ROOT / "web" / "AIChat"
LOG_DIR = ROOT / "logs"


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
    backend_proc = subprocess.Popen(
        [str(uvicorn), "app.main:app", "--host", "0.0.0.0", "--port", "18000"],
        cwd=BACKEND,
    )
    print(f"  Backend  : starting (pid={backend_proc.pid})")

    # Wait and verify backend
    for i in range(20):
        time.sleep(0.5)
        if backend_proc.poll() is not None:
            print(f"  Backend  : CRASHED (exit={backend_proc.poll()})")
            chat_log = BACKEND / "data" / "chat.log"
            if chat_log.exists():
                tail = chat_log.read_text(encoding="utf-8").splitlines()[-15:]
                print(f"  Log tail :\n" + "\n".join(tail))
            input("Press Enter to exit...")
            sys.exit(1)
        try:
            urllib.request.urlopen("http://127.0.0.1:18000/api/health", timeout=2)
            print("  Backend  : ready (http://localhost:18000)")
            break
        except Exception:
            pass
    else:
        print("  Backend  : WARNING - health check timed out (Neo4j may be down)")

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
        backend_proc.terminate()
        frontend_proc.terminate()
        frontend_log.close()
        try:
            backend_proc.wait(timeout=5)
            frontend_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        print("Done.")


if __name__ == "__main__":
    main()
