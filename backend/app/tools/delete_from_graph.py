from app.core import memory as mem


async def delete_from_graph(target_type: str, target: str, rel_type: str | None = None) -> str:
    """从知识图谱中删除记忆。

    参数:
        target_type: 删除目标类型 —— "entity"(实体), "fact"(事实, 需提供 ID),
                     "fact_by_content"(按内容关键词匹配删除), "relation"(关系)
        target: 删除目标标识 —— entity 填名称, fact 填数字 ID,
                fact_by_content 填关键词, relation 填 "from_name||to_name"
        rel_type: 仅当 target_type="relation" 时可选，用于精确匹配关系类型
    """
    try:
        return await mem.delete_memory(target_type, target, rel_type=rel_type)
    except Exception as e:
        return f"(无法删除记忆: {e})"


async def list_memory(otype: str = "all") -> str:
    """列出知识图谱中的内容，用于浏览和查找要删除的条目。

    参数:
        otype: "entities"(实体), "facts"(事实), "all"(全部)
    """
    try:
        return await mem.list_memory(otype)
    except Exception as e:
        return f"(无法列出记忆: {e})"
