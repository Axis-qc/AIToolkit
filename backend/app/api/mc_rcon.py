"""
MC 服务器远程控制台（RCON 直连）
—— 通过 Minecraft 自带 RCON 协议向服务端发送控制台命令（list / stop / spark tps 等）。
   RCON 要求服务端已运行才能连接；开关服/进程检查此前曾走 SSH，但因后台进程无法正常调度 CPU 已移除。
与 phone_monitor（只读监控）不同，这里是【可执行操作】：拥有 RCON 密码 / 网页密码 = 服务端管理员权限。

配置（backend/.env，不硬编码）：
  MC_RCON_HOST   RCON 主机（留空则用 PHONE_HOST）
  MC_RCON_PORT   RCON 端口（默认 25575，需与 server.properties 的 rcon.port 一致）
  MC_RCON_PASS   RCON 密码（需与 server.properties 的 rcon.password 一致）
  MC_WEB_AUTH    本面板网页访问密码（请求须带 X-MC-Auth 头）
"""
import hmac
import logging
import os
import re
import socket
import struct
import time
from pathlib import Path

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

# 屏蔽 /api/mc/status 轮询在 uvicorn access log 的刷屏
class _McAccessFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return "/api/mc/status" not in record.getMessage()


logging.getLogger("uvicorn.access").addFilter(_McAccessFilter())

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


_env = _load_env_file()


def _env_or(name: str, default: str = "") -> str:
    return os.environ.get(name) or _env.get(name) or default


def _env_or_int(name: str, default: int) -> int:
    try:
        return int(_env_or(name, str(default)))
    except ValueError:
        return default


def _web_secret() -> str:
    return _env_or("MC_WEB_AUTH")


def _rcon_target() -> tuple[str, int, str]:
    host = _env_or("MC_RCON_HOST") or _env_or("PHONE_HOST")
    port = _env_or_int("MC_RCON_PORT", 25575)
    password = _env_or("MC_RCON_PASS")
    if not host or not password:
        raise RuntimeError("未配置 RCON（需 MC_RCON_HOST/PHONE_HOST 与 MC_RCON_PASS）")
    return host, port, password


class RCON:
    """极简 RCON 客户端（纯 socket，无第三方依赖）。"""

    def __init__(self, host: str, port: int, password: str, timeout: float = 6.0):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.sock.settimeout(timeout)
        self._send(3, password.encode("utf-8"))  # 认证
        rid, _rtype, _payload = self._read()
        if rid == -1:
            raise RuntimeError("RCON 认证失败（密码错误？）")

    def _recv_exact(self, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("RCON 连接断开")
            buf += chunk
        return buf

    def _send(self, ptype: int, payload: bytes) -> None:
        body = struct.pack("<ii", 1, ptype) + payload + b"\x00\x00"
        self.sock.sendall(struct.pack("<i", len(body)) + body)

    def _read(self) -> tuple[int, int, bytes]:
        head = self._recv_exact(4)
        length = struct.unpack("<i", head)[0]
        if length < 10:
            raise ConnectionError("RCON 响应长度异常")
        data = self._recv_exact(length)
        rid, rtype = struct.unpack("<ii", data[:8])
        return rid, rtype, data[8:-2]

    def command(self, cmd: str, wait: float = 0.15, max_total: float = 2.5) -> str:
        """发送命令并收集全部响应。
        spark 等插件的输出会分成多个包陆续到达（常先来一个空包，再陆续来内容），
        这里在短窗口内持续收包：只有在已收到非空内容且安静 wait 秒后才结束，
        保证 spark tps 之类的多包输出能完整返回。"""
        self._send(2, cmd.encode("utf-8"))
        prev_timeout = self.sock.gettimeout()
        self.sock.settimeout(wait)
        chunks: list[str] = []
        end = time.time() + max_total
        try:
            while time.time() < end:
                try:
                    _rid, _rtype, payload = self._read()
                    if payload:
                        chunks.append(payload.decode("utf-8", errors="replace"))
                except socket.timeout:
                    if chunks:
                        break
                except (ConnectionError, OSError):
                    break
        finally:
            self.sock.settimeout(prev_timeout)
        return "\n".join(chunks)

    def close(self) -> None:
        try:
            self.sock.close()
        except Exception:
            pass


router = APIRouter(prefix="/api/mc", tags=["mc-console"])


class AuthReq(BaseModel):
    token: str = ""


class CmdReq(BaseModel):
    command: str = ""


def _check_auth(token: str) -> bool:
    secret = _web_secret()
    return bool(secret) and hmac.compare_digest(token or "", secret)


def _unauthorized() -> HTTPException:
    return HTTPException(status_code=401, detail="未授权")


_LIST_RE = re.compile(r"There are (\d+) of a max of (\d+) players online:\s*(.*)", re.S)


def _parse_list(out: str) -> dict:
    m = _LIST_RE.search(out)
    if not m:
        return {"players": None, "players_max": None, "names": []}
    players = int(m.group(1))
    pmax = int(m.group(2))
    tail = (m.group(3) or "").strip()
    names = [n.strip() for n in tail.split(",") if n.strip()]
    return {"players": players, "players_max": pmax, "names": names}


@router.post("/auth")
async def auth(req: AuthReq):
    return {"ok": _check_auth(req.token)}


# 以下两个端点用同步 def：FastAPI 自动放入线程池，避免阻塞事件循环
@router.get("/status")
def status(x_mc_auth: str = Header(default="", alias="X-MC-Auth")):
    if not _check_auth(x_mc_auth):
        raise _unauthorized()
    try:
        host, port, password = _rcon_target()
        r = RCON(host, port, password)
        try:
            out = r.command("list")
        finally:
            r.close()
        return {"ok": True, "online": True, "raw": out, **_parse_list(out)}
    except Exception as e:
        return {"ok": False, "online": False, "error": str(e)}


@router.post("/cmd")
def cmd(req: CmdReq, x_mc_auth: str = Header(default="", alias="X-MC-Auth")):
    if not _check_auth(x_mc_auth):
        raise _unauthorized()
    command = (req.command or "").strip()
    if not command or len(command) > 200:
        raise HTTPException(status_code=400, detail="命令非法")
    try:
        host, port, password = _rcon_target()
        r = RCON(host, port, password)
        try:
            out = r.command(command)
        finally:
            r.close()
        return {"ok": True, "output": out}
    except Exception as e:
        return {"ok": False, "error": str(e)}
