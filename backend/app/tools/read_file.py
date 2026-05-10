from app.core.file_guard import resolve


async def read_file(path: str, offset: int = 1, limit: int = 200) -> str:
    filepath = resolve(path)
    if not filepath.exists():
        return f"文件不存在: {path}"
    if filepath.is_dir():
        return f"路径是目录而非文件: {path}"
    try:
        content = filepath.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        content = filepath.read_text(encoding="gbk", errors="replace")
    lines = content.splitlines()
    total = len(lines)
    if offset < 1:
        offset = 1
    start = offset - 1
    end = min(start + limit, total)
    chunk = lines[start:end]
    result = []
    for i, line in enumerate(chunk, start + 1):
        result.append(f"{i}: {line}")
    header = f"{path} ({start + 1}-{end}/{total})"
    return header + "\n" + "\n".join(result)
