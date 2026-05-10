import asyncio
import os

from app.core.file_guard import resolve


async def run_command(command: str, workdir: str | None = None, timeout: int = 60) -> str:
    if workdir:
        cwd = resolve(workdir)
    else:
        from app.core.file_guard import WHITELIST
        cwd = WHITELIST[0] if WHITELIST else os.getcwd()
    proc = None
    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(cwd),
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        output = stdout.decode("gbk", errors="replace")
    except asyncio.TimeoutError:
        if proc:
            try:
                proc.kill()
            except Exception:
                pass
        return f"命令超时 ({timeout}s): {command}"
    limit = 8000
    if len(output) > limit:
        output = output[:limit] + f"\n... (截断，共 {len(output)} 字符)"
    return f"退出码: {proc.returncode}\n工作目录: {cwd}\n\n{output}"
