from app.core import memory as mem


async def search_memory(query: str, top_k: int = 5, scope: str = "all") -> str:
    try:
        return await mem.search(query, top_k)
    except Exception as e:
        return f"(无法检索记忆: {e})"
