import json
from datetime import datetime
from typing import Annotated

import httpx
from fastapi import APIRouter, Body, HTTPException
from openai import AsyncOpenAI

from app.core import config_loader
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
    http_client=httpx.AsyncClient(trust_env=False),
)

UPDATE_SYSTEM_PROMPT = config_loader.render_prompt("graph_update",
    center_list=config_loader.build_center_list_md(),
    category_list=config_loader.build_category_list_md())


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
    steps = []
    msg = response.choices[0].message

    if msg.content:
        summary = msg.content

    if msg.tool_calls:
        for tc in msg.tool_calls:
            args = json.loads(tc.function.arguments) if tc.function.arguments else {}
            result = await dispatch(tc.function.name, args, conv_id=req.conversation_id)
            steps.append({"name": tc.function.name, "args": args, "result": result})

    if not summary:
        summary = "已更新图谱"

    if req.conversation_id == "main":
        msgs = data.get("messages", [])
        if msgs:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            archived_id = f"main_archive_{ts}"
            archived_title = (data.get("title") or "对话") + " (归档)"
            storage.save(archived_id, archived_title, msgs,
                         archived=True, last_prompt_tokens=data.get("last_prompt_tokens", 0))
            storage.clear("main")
            await memory.mark_archived(archived_id)
    else:
        await memory.mark_archived(req.conversation_id)

    return UpdateGraphResponse(success=True, summary=summary, steps=steps)


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


@router.get("/api/graph/config")
async def get_graph_config():
    return {
        "centers": config_loader.get_centers(),
        "entity_types": config_loader.get_entity_types(),
        "default_render": config_loader.get_all_config()["default_render"],
        "root_node_ids": config_loader.get_root_node_ids(),
        "injection": config_loader.get_injection_config(),
    }


@router.get("/api/graph/roots")
async def get_roots():
    return await graph_core.get_roots()


@router.get("/api/graph/children")
async def get_children(type: str, name: str):
    return await graph_core.get_children(type, name)


@router.get("/api/graph/facts")
async def get_facts(type: str, name: str):
    return await graph_core.get_facts(type, name)


@router.get("/api/graph/orphans")
async def get_orphans():
    return await graph_core.get_orphans()
