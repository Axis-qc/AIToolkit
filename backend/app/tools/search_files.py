from pathlib import Path

from app.core.file_guard import resolve, WHITELIST

SKIP = {".git", "__pycache__", "node_modules", ".venv", ".mypy_cache", ".pytest_cache"}


async def search_files(pattern: str, directory: str | None = None) -> str:
    if directory:
        dirpath = resolve(directory)
    else:
        dirpath = WHITELIST[0] if WHITELIST else Path(".")
    if not dirpath.exists():
        return f"目录不存在: {directory or str(dirpath)}"
    if not dirpath.is_dir():
        return f"不是目录: {directory or str(dirpath)}"
    matches = list(dirpath.rglob(pattern))
    if not matches:
        return f"在 {dirpath} 下未找到匹配 {pattern} 的文件"
    lines = []
    for m in sorted(matches, key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True):
        parts = set(m.parts)
        if parts & SKIP:
            continue
        lines.append(str(m.relative_to(dirpath)))
        if len(lines) >= 30:
            break
    if not lines:
        return f"未找到匹配文件（已过滤隐藏目录）"
    return "\n".join(lines)
