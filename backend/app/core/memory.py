# 知识图谱业务编排层：协调搜索、CRUD 操作，返回原始数据
from . import graph


async def search(query: str, top_k: int = 5) -> list[dict]:
    """搜索图谱，返回原始结果列表。"""
    return await graph.search_entities(query, top_k)


async def save(nodes: list[dict], relations: list[dict], facts: list[dict] | None = None, conv_id: str | None = None, importance: int | None = None, pinned: bool | None = None):
    for node in nodes:
        name = node["name"]
        ntype = node["type"]
        props = node.get("properties", {})
        await graph.upsert_entity(name, ntype, props)
        await graph.bump_importance(name)
        if importance is not None:
            await graph.set_importance(name, importance)
        if pinned is not None:
            await graph.set_pinned(name, pinned)

    for rel in relations:
        await graph.upsert_relation(
            from_type=rel["from_type"],
            from_name=rel["from_name"],
            to_type=rel["to_type"],
            to_name=rel["to_name"],
            rel_type=rel["rel_type"],
            props=rel.get("properties", {}),
        )

    if facts:
        for fact in facts:
            await graph.create_fact(
                content=fact["content"],
                ftype=fact.get("type", "fact"),
                about_entities=fact.get("about_entities", []),
            )

    if conv_id:
        for node in nodes:
            await graph.add_mention(conv_id, node["name"])

    return "已写入图谱"




async def delete_memory(target_type: str, target: str, rel_type: str | None = None) -> str:
    """软删除图谱中的记忆（标记 deprecated_at，24h 后自动清理）。"""
    if target_type == "entity":
        ok = await graph.soft_delete_entity(target)
        if ok:
            return f"已标记实体「{target}」为作废，24 小时后自动清理，期间可恢复"
        return f"未找到实体「{target}」"
    elif target_type == "fact":
        try:
            fact_id = int(target.strip())
        except ValueError:
            return "错误：删除 fact 需要提供数字 ID"
        ok = await graph.soft_delete_fact(fact_id)
        if ok:
            return f"已标记事实 #{fact_id} 为作废，24 小时后自动清理，期间可恢复"
        return f"未找到事实 #{fact_id}"
    elif target_type == "relation":
        # target 格式: "from_name||to_name" 或 "from_name||to_name||rel_type"
        parts = target.split("||")
        if len(parts) < 2:
            return "错误：删除 relation 需要 from_name||to_name 格式"
        from_name, to_name = parts[0], parts[1]
        rt = parts[2] if len(parts) > 2 else None
        count = await graph.delete_relation(from_name, to_name, rt)
        if count > 0:
            return f"已删除 {count} 条关系"
        return "未找到匹配的关系"
    elif target_type == "fact_by_content":
        # 根据内容关键词软删除事实（SQL LIKE，不再全量拉取）
        facts = await graph.search_facts_by_keyword(target)
        deleted = 0
        for f in facts:
            await graph.soft_delete_fact(f["id"])
            deleted += 1
        if deleted > 0:
            return f"已标记 {deleted} 条匹配的事实为作废，24 小时后自动清理，期间可恢复"
        return "未找到匹配的事实"
    else:
        return f"未知的删除目标类型: {target_type}"


async def list_memory(type: str | None = None) -> str:
    """分层浏览图谱。无参数→类型概览；传 type→该类型下实体+子节点数。"""
    if type:
        return await _list_entities_by_type(type)
    return await _list_types_overview()


async def _list_types_overview() -> str:
    """类型级概览：只显示类型名+实体数量。"""
    types = await graph.list_entity_types_summary()
    if not types:
        return "图谱为空"
    total = sum(t["count"] for t in types)
    lines = [f"图谱概览（{total} 个实体）"]
    for t in types:
        lines.append(f"- {t['type']} ({t['count']} 个)")
    return "\n".join(lines)


async def _list_entities_by_type(entity_type: str) -> str:
    """列出指定类型下的实体+子节点数。"""
    entities = await graph.list_entities_by_type(entity_type)
    if not entities:
        return f"类型 '{entity_type}' 下没有实体"
    lines = [f"{entity_type} ({len(entities)} 个)"]
    for e in entities:
        pinned_mark = " [固定]" if e["pinned"] else ""
        child_info = f"（{e['child_count']}个子节点）" if e["child_count"] > 0 else ""
        lines.append(f"  - {e['name']}{pinned_mark}{child_info}")
    return "\n".join(lines)


async def delete_conversation(conv_id: str):
    await graph.delete_conversation(conv_id)


async def restore(target_type: str, target: str) -> str:
    """恢复已软删除的记忆。target_type: entity / fact"""
    if target_type == "entity":
        ok = await graph.restore_entity(target)
        if ok:
            return f"已恢复实体「{target}」"
        return f"未找到已作废的实体「{target}」"
    elif target_type == "fact":
        try:
            fact_id = int(target.strip())
        except ValueError:
            return "错误：恢复 fact 需要提供数字 ID"
        ok = await graph.restore_fact(fact_id)
        if ok:
            return f"已恢复事实 #{fact_id}"
        return f"未找到已作废的事实 #{fact_id}"
    else:
        return f"未知的恢复目标类型: {target_type}"


async def list_deprecated() -> str:
    """列出所有已软删除待清理的实体和事实。"""
    data = await graph.list_deprecated()
    lines = []
    if data["entities"]:
        lines.append(f"## 已作废实体 ({len(data['entities'])} 个)")
        for e in data["entities"]:
            lines.append(f"- [{e['type']}] {e['name']}（{e['deprecated_at']}）")
    if data["facts"]:
        lines.append(f"## 已作废事实 ({len(data['facts'])} 条)")
        for f in data["facts"]:
            lines.append(f"- #{f['id']} {f['content']}（{f['deprecated_at']}）")
    if not lines:
        return "没有已作废的内容"
    return "\n".join(lines)


async def update(target_type: str, target: str, updates: dict) -> str:
    """更新图谱中的记忆。支持更新事实内容和实体属性。"""
    if target_type == "fact":
        try:
            fact_id = int(target.strip())
        except ValueError:
            return "错误：更新 fact 需要提供数字 ID"
        ok = await graph.update_fact(
            fact_id,
            content=updates.get("content"),
            ftype=updates.get("type"),
            about_entities=updates.get("about_entities"),
        )
        if ok:
            return f"已更新事实 #{fact_id}"
        return f"未找到事实 #{fact_id}"

    elif target_type == "entity":
        ok = await graph.update_entity(
            target,
            new_type=updates.get("type"),
            new_props=updates.get("properties"),
        )
        if ok:
            return f"已更新实体「{target}」"
        return f"未找到实体「{target}」"

    elif target_type == "entity_importance":
        imp = updates.get("importance")
        if imp is None:
            return "错误：更新 entity_importance 需要提供 importance 字段"
        ok = await graph.update_entity(target, new_type=None, new_props=None)
        if not ok:
            return f"未找到实体「{target}」"
        await graph.set_importance(target, imp)
        return f"已更新实体「{target}」重要度为 {imp}"

    elif target_type == "entity_pinned":
        pinned = updates.get("pinned")
        if pinned is None:
            return "错误：更新 entity_pinned 需要提供 pinned 字段"
        ok = await graph.update_entity(target, new_type=None, new_props=None)
        if not ok:
            return f"未找到实体「{target}」"
        await graph.set_pinned(target, bool(pinned))
        return f"已更新实体「{target}」固定状态为 {bool(pinned)}"

    else:
        return f"未知的更新目标类型: {target_type}，支持: fact / entity / entity_importance / entity_pinned"


async def merge(source: str, target: str) -> str:
    """将源实体合并到目标实体。"""
    try:
        return await graph.merge_entities(source, target)
    except Exception as e:
        return f"(无法合并实体: {e})"
