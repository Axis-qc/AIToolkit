from .config import settings
from . import graph
from . import storage
from . import config_loader


async def search(query: str, top_k: int = 5) -> str:
    # 固定记忆 + 关键词匹配
    pinned = await graph.get_pinned_entities()
    keyword_results = await graph.search_entities(query, top_k)

    # 去重：已存在同名实体跳过
    seen = set()
    merged = []
    for r in pinned:
        if r["entity"] not in seen:
            seen.add(r["entity"])
            merged.append(r)
    for r in keyword_results:
        if r["entity"] not in seen:
            seen.add(r["entity"])
            merged.append(r)

    if not merged:
        return config_loader.get_injection_config()["no_result"]

    inj = config_loader.get_injection_config()
    groups: dict[str, list] = {}
    unknown = []

    for row in merged:
        crs = row.get("center_relations", [])
        if not crs:
            unknown.append(row)
        else:
            for cr in crs:
                key = f"{cr['center_type']}|{cr['center_name']}"
                groups.setdefault(key, []).append(row)

    lines = [inj["header"]]

    for c in config_loader.get_centers():
        key = f"{c['type']}|{c['name']}"
        if key in groups:
            lines.append(inj["section_template"].format(icon=c["icon"], label=c["label"]))
            for row in groups[key]:
                _append_entity(lines, row, inj)

    if unknown:
        lines.append(inj["section_template"].format(
            icon=inj["unknown_section_icon"],
            label=inj["unknown_section_label"],
        ))
        for row in unknown:
            _append_entity(lines, row, inj)

    return "\n".join(lines) if len(lines) > 1 else inj["no_result"]


def _append_entity(lines: list, row: dict, inj: dict):
    entity = row["entity"]
    imp = row.get("importance", 1)
    is_pinned = row.get("pinned", False)
    pinned_mark = " [固定]" if is_pinned else ""
    lines.append(inj["item_template"].format(entity=entity, importance=imp, pinned_mark=pinned_mark))
    for fact in row.get("facts", []) or []:
        if fact:
            lines.append(f"- [{fact.get('type', '')}] {fact.get('content', '')}")
    related = [r for r in (row.get("related") or []) if r]
    if related:
        lines.append(f"- {inj['relation_label']}：{', '.join(related)}")
    conversations = [c for c in (row.get("conversations") or []) if c and c.get("id")]
    if conversations:
        srcs = ", ".join(f"[{c.get('title', c['id'][:8])}]" for c in conversations)
        lines.append(f"- {inj['source_label']}：{srcs}")


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


async def index_conversation(conv_id: str, file_path: str, title: str):
    await graph.index_conversation(conv_id, file_path, title)


async def mark_archived(conv_id: str):
    await graph.mark_archived(conv_id)
    storage.mark_archived_file(conv_id)


def _get_update_prompt() -> str:
    return config_loader.render_prompt("graph_update",
        center_list=config_loader.build_center_list_md(),
        category_list=config_loader.build_category_list_md())


def _format_history(messages: list[dict]) -> str:
    lines = []
    for m in messages:
        role = "用户" if m["role"] == "user" else "助手"
        content = m.get("content", "")
        if content:
            lines.append(f"[{role}]: {content}")
        if m.get("tool_calls"):
            for tc in m["tool_calls"]:
                name = tc.get("function", {}).get("name", "")
                if name:
                    lines.append(f"[工具调用: {name}]")
    return "\n".join(lines)


async def archive_conversation(conv_id: str):
    import httpx
    import json as _json
    from openai import AsyncOpenAI
    from app.tools import TOOL_DEFINITIONS, dispatch
    from .config import settings

    data = storage.load(conv_id)
    if not data or not data.get("messages"):
        return

    conversation_text = _format_history(data["messages"])
    msgs = [
        {"role": "system", "content": _get_update_prompt()},
        {"role": "user", "content": conversation_text},
    ]

    client = AsyncOpenAI(
        api_key=settings.chat_api_key,
        base_url=settings.chat_base_url,
        http_client=httpx.AsyncClient(trust_env=False),
    )

    response = await client.chat.completions.create(
        model=settings.chat_model,
        messages=msgs,
        tools=TOOL_DEFINITIONS,
        tool_choice="auto",
    )

    msg = response.choices[0].message
    if msg.tool_calls:
        for tc in msg.tool_calls:
            args = _json.loads(tc.function.arguments) if tc.function.arguments else {}
            await dispatch(tc.function.name, args, conv_id=conv_id)

    await mark_archived(conv_id)


async def delete_memory(target_type: str, target: str, rel_type: str | None = None) -> str:
    """删除图谱中的记忆。target_type: entity | fact | relation"""
    if target_type == "entity":
        ok = await graph.delete_entity(target)
        if ok:
            return f"已删除实体「{target}」及其所有关联关系和事实"
        return f"未找到实体「{target}」"
    elif target_type == "fact":
        try:
            fact_id = int(target.strip())
        except ValueError:
            return "错误：删除 fact 需要提供数字 ID"
        ok = await graph.delete_fact(fact_id)
        if ok:
            return f"已删除事实 #{fact_id}"
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
        # 根据内容关键词删除事实
        facts = await graph.list_all_facts(500)
        deleted = 0
        for f in facts:
            if target.lower() in f["content"].lower():
                await graph.delete_fact(f["id"])
                deleted += 1
        if deleted > 0:
            return f"已删除 {deleted} 条匹配的事实"
        return "未找到匹配的事实"
    else:
        return f"未知的删除目标类型: {target_type}"


async def list_memory(otype: str = "all") -> str:
    """列出图谱中的内容，用于浏览。otype: entities | facts | all"""
    lines = []
    if otype in ("entities", "all"):
        entities = await graph.list_all_entities()
        lines.append(f"## 实体 ({len(entities)} 个)")
        for e in entities:
            lines.append(f"- [{e['type']}] {e['name']}")
    if otype in ("facts", "all"):
        facts = await graph.list_all_facts(500)
        lines.append(f"## 事实 ({len(facts)} 条)")
        for f in facts:
            about = ", ".join(f["about_entities"]) if f["about_entities"] else "(无关联实体)"
            lines.append(f"- #{f['id']} [{f['type']}] {f['content'][:80]} （关联: {about}）")
    return "\n".join(lines) if lines else "图谱为空"


async def delete_conversation(conv_id: str):
    await graph.delete_conversation(conv_id)
