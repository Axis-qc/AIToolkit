from app.core.file_guard import resolve


async def write_file(path: str, content: str) -> str:
    filepath = resolve(path)
    if filepath.is_dir():
        return f"无法写入目录: {path}"
    filepath.parent.mkdir(parents=True, exist_ok=True)
    filepath.write_text(content, encoding="utf-8")
    size = len(content.encode("utf-8"))
    return f"已写入 {path} ({size} 字节)"
