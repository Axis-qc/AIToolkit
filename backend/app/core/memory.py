from .config import settings
from . import graph
from . import storage


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
        return "（未找到相关记忆）"

    lines = []
    for row in merged:
        entity = row["entity"]
        imp = row.get("importance", 1)
        is_pinned = row.get("pinned", False)
        pinned_mark = " [固定]" if is_pinned else ""
        lines.append(f"## 关于 {entity} (重要度 {imp}/10){pinned_mark}")
        if row["user_relation"]:
            lines.append(f"- 你对此的态度：{row['user_relation']}")
        for fact in row.get("facts", []) or []:
            if fact:
                lines.append(f"- [{fact.get('type', '')}] {fact.get('content', '')}")
        related = [r for r in (row.get("related") or []) if r]
        if related:
            lines.append(f"- 关联概念：{', '.join(related)}")
        conversations = [c for c in (row.get("conversations") or []) if c and c.get("id")]
        if conversations:
            srcs = ", ".join(f"[{c.get('title', c['id'][:8])}]" for c in conversations)
            lines.append(f"- 来源对话：{srcs}")

    return "\n".join(lines)


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


UPDATE_SYSTEM_PROMPT = """你需要深入审视以下完整对话，将值得长期记忆的内容写入知识图谱。

处理步骤：
1. 先调用 search_memory 检索与对话内容相关的已有记忆
2. 提取并调用 save_to_graph 写入：
   - 用户的新偏好、习惯、风格（type 用 preference）
   - 用户提到的新事实、经历、计划（type 用 fact / event / plan）
   - 用户学习的知识点及其关系（type 用 topic）
   - 用户交代的待办事项（type 用 todo）
3. 对比已有记忆，发现矛盾时记录 type=conflict 的 fact，描述新旧信息冲突
4. 发现未解决问题、模糊表述、待跟进事项，记录 type=pending 的 fact
5. 输出一句话核心综述

注意：
- 只保存用户明确表达的内容，不要推测
- 不为琐碎的闲聊建立记忆
- 关系命名用简洁的动词，如"学过""偏好""计划""经历"等"""


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
    import json as _json
    from openai import AsyncOpenAI
    from app.tools import TOOL_DEFINITIONS, dispatch
    from .config import settings

    data = storage.load(conv_id)
    if not data or not data.get("messages"):
        return

    conversation_text = _format_history(data["messages"])
    msgs = [
        {"role": "system", "content": UPDATE_SYSTEM_PROMPT},
        {"role": "user", "content": conversation_text},
    ]

    client = AsyncOpenAI(
        api_key=settings.chat_api_key,
        base_url=settings.chat_base_url,
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
            fact_id = int(target)
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
        facts = await graph.list_all_facts(100)
        lines.append(f"## 事实 ({len(facts)} 条)")
        for f in facts:
            about = ", ".join(f["about_entities"]) if f["about_entities"] else "(无关联实体)"
            lines.append(f"- #{f['id']} [{f['type']}] {f['content'][:80]} （关联: {about}）")
    return "\n".join(lines) if lines else "图谱为空"


async def delete_conversation(conv_id: str):
    await graph.delete_conversation(conv_id)
