import re

from app.core.file_guard import resolve, WHITELIST
from pathlib import Path

SKIP = {".git", "__pycache__", "node_modules", ".venv", ".mypy_cache", ".pytest_cache"}


async def search_content(pattern: str, directory: str | None = None, file_types: str | None = None) -> str:
    if directory:
        dirpath = resolve(directory)
    else:
        dirpath = WHITELIST[0] if WHITELIST else Path(".")
    if not dirpath.exists():
        return f"目录不存在: {directory or str(dirpath)}"

    try:
        regex = re.compile(pattern)
    except re.error as e:
        return f"正则表达式错误: {e}"

    results = []
    for f in dirpath.rglob("*"):
        if not f.is_file():
            continue
        if set(f.parts) & SKIP:
            continue
        if file_types:
            if not any(f.name.endswith(ft) for ft in file_types.split(",")):
                continue
        try:
            content = f.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for lineno, line in enumerate(content.splitlines(), 1):
            if regex.search(line):
                rel = str(f.relative_to(dirpath))
                results.append(f"{rel}:{lineno}: {line[:200]}")

    if not results:
        return f"在 {dirpath} 下未找到匹配 {pattern}"
    return "\n".join(results[:30])
