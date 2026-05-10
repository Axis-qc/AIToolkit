from app.core.file_guard import resolve


async def edit_file(path: str, old_string: str, new_string: str, replace_all: bool = False) -> str:
    filepath = resolve(path)
    if not filepath.exists():
        return f"文件不存在: {path}"
    content = filepath.read_text(encoding="utf-8")
    count = content.count(old_string)
    if count == 0:
        return f"未找到匹配文本: {old_string[:80]}"
    if count > 1 and not replace_all:
        return f"找到 {count} 处匹配，请提供更多上下文或设置 replace_all=true"
    new_content = content.replace(old_string, new_string) if replace_all else content.replace(old_string, new_string, 1)
    filepath.write_text(new_content, encoding="utf-8")
    return f"已修改 {path} (替换 {count if replace_all else 1} 处)"
