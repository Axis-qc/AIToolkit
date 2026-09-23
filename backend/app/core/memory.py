# 知识图谱业务编排层：协调搜索、CRUD 操作，返回原始数据
from . import graph


async def search(query: str, top_k: int = 5, with_diagnostics: bool = True) -> list[dict]:
    """搜索图谱，返回原始结果列表。

    with_diagnostics=True（默认）时每条结果额外带 score 与 matched_fields，
    用于判断两个相似实体是否在互相抢位、以及整理后检索是否真的改善。
    """
    return await graph.search_entities(query, top_k, with_diagnostics=with_diagnostics)


async def list_pinned() -> list[dict]:
    """列出所有固定（pinned）注入的实体，按重要度降序，含完整字段。"""
    return await graph.get_pinned_entities()


async def get_entity(name: str) -> dict | None:
    """精准匹配读取单个实体的完整字段。"""
    return await graph.get_entity_detail(name)


async def get_entities(names: list[str]) -> dict:
    """批量读取实体完整字段（含 content 与时间戳）。

    替代逐条 get_entity：641 个实体判断过时不必再调几百次。
    返回 found 与 missing 两份，缺失的名字明确列出，不静默跳过。
    """
    if not names:
        return {"found": [], "missing": [], "found_count": 0, "missing_count": 0, "requested_count": 0}
    ordered = list(dict.fromkeys(names))
    found = await graph.get_entities_by_names(names)
    found_names = {e["name"] for e in found}
    missing = [n for n in ordered if n not in found_names]
    return {
        "requested_count": len(ordered),
        "found": found,
        "found_count": len(found),
        "missing": missing,
        "missing_count": len(missing),
    }


async def list_entities(
    offset: int = 0,
    limit: int = 100,
    entity_type: str | None = None,
    include_content: bool = True,
) -> dict:
    """分页列出实体，默认带 content，用于分批判断过时。"""
    return await graph.list_entities_paged(
        offset=offset, limit=limit, entity_type=entity_type, include_content=include_content
    )


async def health(
    checks: list[str] | None = None,
) -> dict:
    """图谱体检：一次返回重复候选、过时台账、类型碎片、悬空关系、lint 违规。

    checks 为空则全查，可选 duplicates / stale / types / dangling / lint。
    只读，不改任何数据。
    """
    return await graph.health_check(checks)


async def mark_verified(
    names: list[str],
    verified_until: str | None = None,
    stale_marked_at: str | None = None,
    clear: bool = False,
) -> str:
    """批量写入或清除实体的过时标注（结构化字段，不写正文）。

    标注落在 stale_marked_at / verified_until 两列，
    体检和清理都直接查字段，不再靠 content 里写【待验证】再文本匹配。
    clear=True 表示核实无误，去掉标注。
    """
    if not names:
        return "错误：names 为空"
    applied: list[str] = []
    missing: list[str] = []
    for name in dict.fromkeys(names):
        ok = await graph.set_stale_mark(
            name,
            stale_marked_at=stale_marked_at,
            verified_until=verified_until,
            clear=clear,
        )
        if ok:
            applied.append(name)
        else:
            missing.append(name)
    action = "清除过时标注" if clear else "写入过时标注"
    parts = [f"已对 {len(applied)} 个实体{action}"]
    if missing:
        parts.append(f"未找到 {len(missing)} 个：{'、'.join(missing[:10])}")
    return "；".join(parts)


async def batch_update(
    names: list[str],
    new_type: str | None = None,
    importance: int | None = None,
    stale_marked_at: str | None = None,
    verified_until: str | None = None,
    clear_stale: bool = False,
    soft_delete: bool = False,
) -> str:
    """批量修改实体：重设类型 / 打标注 / 设重要度 / 软删除。

    整理天生是批量的，一次处理一批目标，替代逐条 update_memory。
    """
    if not names:
        return "错误：names 为空"
    if not any([new_type, importance is not None, stale_marked_at, verified_until, clear_stale, soft_delete]):
        return "错误：没有指定任何要修改的字段"
    result = await graph.batch_update_entities(
        names,
        new_type=new_type,
        importance=importance,
        stale_marked_at=stale_marked_at,
        verified_until=verified_until,
        clear_stale=clear_stale,
        soft_delete=soft_delete,
    )
    actions = []
    if new_type:
        actions.append(f"类型改为 {new_type}")
    if importance is not None:
        actions.append(f"重要度改为 {importance}")
    if clear_stale:
        actions.append("清除过时标注")
    elif stale_marked_at or verified_until:
        actions.append("写入过时标注")
    if soft_delete:
        actions.append("软删除")
    parts = [f"已对 {result['applied_count']} 个实体执行：{'、'.join(actions)}"]
    if result["missing"]:
        parts.append(f"未找到 {result['missing_count']} 个：{'、'.join(result['missing'][:10])}")
    return "；".join(parts)


async def list_tombstones(kind: str | None = None, limit: int = 200) -> str:
    """列出已过保留窗口、被物理删除的内容（墓地）。"""
    items = await graph.list_tombstones(kind=kind, limit=limit)
    if not items:
        return "墓地为空（还没有内容被清理，或本次清理未含任何到期条目）"
    lines = [f"已清理内容 {len(items)} 条（保留窗口 {graph.retention_hours()} 小时）"]
    for it in items:
        if it["kind"] == "entity":
            lines.append(f"- [实体/{it['type']}] {it['ref']}（作废于 {it['deprecated_at']}，清理于 {it['purged_at']}）")
        elif it["kind"] == "fact":
            lines.append(f"- [事实/{it['type']}] #{it['ref']} {it['content'][:60]}（清理于 {it['purged_at']}）")
        else:
            detail = it["detail"]
            lines.append(f"- [关系] {detail.get('from')} → {detail.get('to')}（{detail.get('rel_type')}）")
    return "\n".join(lines)


async def save(
    nodes: list[dict],
    facts: list[dict] | None = None,
    conv_id: str | None = None,
    importance: int | None = None,
    pinned: bool | None = None,
    is_root: bool | None = None,
):
    """保存到图谱。nodes 中可含 content/relations（新格式）。
    relations 必须嵌入到对应 node 的 relations 字段，不再接受顶层 relations 参数。
    """
    for node in nodes:
        name = node["name"]
        ntype = node["type"]
        content = node.get("content", "")
        rels = node.get("relations", None)
        props = node.get("properties", {})
        node_is_root = node.get("is_root", is_root)
        effective_root = bool(node_is_root) if node_is_root is not None else False
        await graph.upsert_entity(name, ntype, content=content, relations=rels, props=props, is_root=effective_root)
        if effective_root:
            await graph.set_root(name, True)
        await graph.bump_importance(name)
        if importance is not None:
            await graph.set_importance(name, importance)
        if pinned is not None:
            await graph.set_pinned(name, pinned)

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
    """列出所有已软删除待清理的实体、关系索引和事实。"""
    data = await graph.list_deprecated()
    lines = []
    if data["entities"]:
        lines.append(f"## 已作废实体 ({len(data['entities'])} 个)")
        for e in data["entities"]:
            lines.append(f"- [{e['type']}] {e['name']}（{e['deprecated_at']}）")
    if data["relation_index"]:
        lines.append(f"## 已失效关系 ({len(data['relation_index'])} 条)")
        for r in data["relation_index"]:
            lines.append(f"- {r['from']} → {r['to']}（{r['rel_type']}）[{r['deprecated_at']}]")
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
        new_name = updates.get("name")
        try:
            ok = await graph.update_entity(
                target,
                new_name=new_name,
                new_type=updates.get("type"),
                new_props=updates.get("properties"),
                new_content=updates.get("content"),
                new_relations=updates.get("relations"),
            )
        except ValueError as e:
            return f"错误：{e}"
        if ok:
            display_name = new_name or target
            return f"已更新实体「{display_name}」"
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

    elif target_type == "entity_root":
        is_root = updates.get("is_root")
        if is_root is None:
            return "错误：更新 entity_root 需要提供 is_root 字段"
        ok = await graph.update_entity(target, new_type=None, new_props=None)
        if not ok:
            return f"未找到实体「{target}」"
        await graph.set_root(target, bool(is_root))
        return f"已更新实体「{target}」根节点状态为 {bool(is_root)}"

    else:
        return f"未知的更新目标类型: {target_type}，支持: fact / entity / entity_importance / entity_pinned / entity_root"


async def merge(source: str, target: str, preview: bool = False) -> str:
    """将源实体合并到目标实体。

    preview=True 时只干跑，返回会迁走哪些关系、哪些事实、属性有无冲突，不落库。
    正式执行时走的也是同一份计划，保证预览与执行一致。
    """
    try:
        if preview:
            plan = await graph.preview_merge(source, target)
            if not plan.get("ok"):
                return plan.get("error", "无法预览合并")
            return _format_merge_preview(plan)
        return await graph.merge_entities(source, target)
    except Exception as e:
        return f"(无法合并实体: {e})"


def _format_merge_preview(plan: dict) -> str:
    """把合并计划排版成可直接阅读的干跑报告。"""
    lines = [
        f"合并干跑：「{plan['source']['name']}」→「{plan['target']['name']}」（未写入任何数据）",
        f"源实体：type={plan['source']['type']}，重要度 {plan['source']['importance']}，"
        f"内容 {plan['source']['content_length']} 字{'（有内容，合并后会随源实体作废）' if plan['source_content_lost'] else '（内容为空）'}",
        f"目标实体：type={plan['target']['type']}，重要度 {plan['target']['importance']}",
    ]
    if plan["type_conflict"]:
        lines.append(
            f"类型冲突：源 {plan['source']['type']} 与目标 {plan['target']['type']} 不同，"
            "合并后源类型丢失，需确认是否本就同类"
        )
    lines.append(
        f"关系：将迁走 {plan['relations_to_move_count']} 条，"
        f"目标已有 {plan['relations_already_present_count']} 条重复（跳过）"
    )
    for r in plan["relations_to_move"][:20]:
        lines.append(f"  + {plan['source']['name']} → {r.get('name')}（{r.get('rel', '')}）")
    lines.append(f"入边：{plan['inbound_relations_count']} 条指向源实体的关系将改指目标")
    for r in plan["inbound_relations"][:10]:
        lines.append(f"  ~ {r['from']} → {plan['source']['name']}（{r['rel_type']}）")
    lines.append(f"事实：{plan['facts_to_move_count']} 条将改挂到目标实体")
    for f in plan["facts_to_move"][:10]:
        lines.append(f"  + #{f['id']} {f['content'][:70]}")
    lines.append(f"属性：新增 {plan['properties_to_add_count']} 项")
    for k, v in list(plan["properties_to_add"].items())[:10]:
        lines.append(f"  + {k} = {v}")
    if plan["properties_conflict_count"]:
        lines.append(f"属性冲突 {plan['properties_conflict_count']} 项（保留目标值）：")
        for k, v in list(plan["properties_conflict"].items())[:10]:
            lines.append(f"  ! {k}：源 {v['source']!r} / 目标 {v['target']!r}")
    if plan["importance_after"] != plan["importance_before"]:
        lines.append(f"重要度：{plan['importance_before']} → {plan['importance_after']}（取高值）")
    if plan["pinned_after"] and not plan["target"]["pinned"]:
        lines.append("固定状态：源实体已固定，目标将变为固定")
    lines.append(f"最后一步：软删除源实体「{plan['source']['name']}」")
    return "\n".join(lines)
