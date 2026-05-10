from app.core import memory as mem


async def save_to_graph(nodes: list, relations: list, facts: list | None = None, conv_id: str | None = None) -> str:
    return await mem.save(nodes, relations, facts, conv_id=conv_id)
