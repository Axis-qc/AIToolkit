import json
from typing import Annotated

from fastapi import APIRouter, Body, HTTPException
from openai import AsyncOpenAI

from app.core import memory
from app.core import storage
from app.core import graph as graph_core
from app.core.config import settings
from app.models.chat import (
    UpdateGraphRequest,
    UpdateGraphResponse,
    ConversationItem,
    ConversationListResponse,
    ConversationDetail,
)
from app.tools import TOOL_DEFINITIONS, dispatch

router = APIRouter(tags=["graph"])

client = AsyncOpenAI(
    api_key=settings.chat_api_key,
    base_url=settings.chat_base_url,
)

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


@router.post("/api/chat/update-graph")
async def update_graph(req: Annotated[UpdateGraphRequest, Body()]) -> UpdateGraphResponse:
    data = storage.load(req.conversation_id)
    if not data:
        raise HTTPException(status_code=404, detail="对话记录不存在")

    conversation_text = format_history(data["messages"])
    messages = [
        {"role": "system", "content": UPDATE_SYSTEM_PROMPT},
        {"role": "user", "content": conversation_text},
    ]

    response = await client.chat.completions.create(
        model=settings.chat_model,
        messages=messages,
        tools=TOOL_DEFINITIONS,
        tool_choice="auto",
    )

    summary = ""
    msg = response.choices[0].message

    if msg.content:
        summary = msg.content

    if msg.tool_calls:
        for tc in msg.tool_calls:
            args = json.loads(tc.function.arguments) if tc.function.arguments else {}
            await dispatch(tc.function.name, args, conv_id=req.conversation_id)

    if not summary:
        summary = "已更新图谱"

    await memory.mark_archived(req.conversation_id)

    return UpdateGraphResponse(success=True, summary=summary)


def format_history(messages: list[dict]) -> str:
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


@router.get("/api/conversations")
async def list_conversations() -> ConversationListResponse:
    items = [ConversationItem(**c) for c in storage.list_all()]
    return ConversationListResponse(conversations=items)


@router.get("/api/conversations/{conv_id}")
async def get_conversation(conv_id: str) -> ConversationDetail:
    data = storage.load(conv_id)
    if not data:
        raise HTTPException(status_code=404, detail="对话记录不存在")
    return ConversationDetail(
        id=data["id"],
        title=data.get("title", ""),
        created_at=data.get("created_at", ""),
        updated_at=data.get("updated_at", ""),
        archived=data.get("archived", False),
        messages=data.get("messages", []),
    )


@router.delete("/api/conversations/{conv_id}")
async def delete_conv(conv_id: str) -> dict[str, str]:
    data = storage.load(conv_id)
    if not data:
        raise HTTPException(status_code=404, detail="对话记录不存在")
    await memory.delete_conversation(conv_id)
    storage.delete(conv_id)
    return {"status": "deleted"}


@router.get("/api/graph")
async def get_graph():
    return await graph_core.get_all_graph()
