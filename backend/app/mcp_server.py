"""
MCP 服务器——将知识图谱暴露为 MCP（Model Context Protocol）工具。

所有工具函数直接调用 app.core.memory → SQLite，
与 REST 端点 /api/graph/tool/* 是独立的访问路径。
"""
import json
import logging

import anyio
from anyio.streams.memory import MemoryObjectSendStream, MemoryObjectReceiveStream
from mcp.server.fastmcp import FastMCP
from mcp.server.models import InitializationOptions
from mcp.server.session import ServerSession
from mcp.types import JSONRPCMessage
from mcp.shared.message import SessionMessage, ServerMessageMetadata
from pydantic import BaseModel, Field
from starlette.responses import Response
from starlette.types import Receive, Scope, Send

from app.core import memory as mem

logger = logging.getLogger(__name__)


async def mcp_oneshot_app(scope: Scope, receive: Receive, send: Send) -> None:
    """
    绕过 SSE 会话检查，直接处理单次 MCP 请求。
挂在 /mcp-direct 路径下，避免与 SSE mount 冲突。

    为每个 POST 请求创建一个独立的 stateless MCP session，
    处理完立即返回结果，不依赖持久 SSE 连接。
    """
    if scope["type"] != "http":
        return

    from starlette.requests import Request
    request = Request(scope, receive)

    # 只接受 POST
    if request.method != "POST":
        response = Response("Method not allowed", status_code=405)
        return await response(scope, receive, send)

    body = await request.body()
    if not body:
        response = Response("Empty body", status_code=400)
        return await response(scope, receive, send)

    try:
        jmsg = JSONRPCMessage.model_validate_json(body)
    except Exception as e:
        logger.warning(f"Invalid MCP message: {e}")
        response = Response(f"Invalid MCP message: {e}", status_code=400)
        return await response(scope, receive, send)

    # 创建一次性内存流
    read_writer, read_reader = anyio.create_memory_object_stream(1)
    write_writer, write_reader = anyio.create_memory_object_stream(1)

    mcp_server = mcp._mcp_server
    init_opts = mcp_server.create_initialization_options()

    result_json = None

    async def run_session():
        nonlocal result_json
        from contextlib import AsyncExitStack

        async with AsyncExitStack() as stack:
            lifespan_ctx = await stack.enter_async_context(mcp_server.lifespan(mcp_server))
            session = await stack.enter_async_context(
                ServerSession(read_reader, write_writer, init_opts, stateless=True)
            )

            # 投递消息
            session_msg = SessionMessage(jmsg, metadata=ServerMessageMetadata())
            await read_writer.send(session_msg)
            read_writer.close()

            # 等待并处理响应
            async for msg in session.incoming_messages:
                await mcp_server._handle_message(msg, session, lifespan_ctx, raise_exceptions=False)
                # 响应已通过 message.respond() 写入 write_writer

            # 收集 write stream 上的响应
            write_writer.close()
            responses = []
            try:
                async for resp in write_reader:
                    responses.append(resp)
            except anyio.EndOfStream:
                pass

            if responses:
                last = responses[-1]
                if hasattr(last, 'message') and hasattr(last.message, 'root'):
                    result_json = last.message.root.model_dump_json(
                        by_alias=True, exclude_none=True
                    )

    try:
        async with anyio.create_task_group() as tg:
            tg.start_soon(run_session)

        if result_json:
            response = Response(
                content=result_json,
                status_code=200,
                media_type="application/json",
            )
        else:
            response = Response("No response", status_code=500)

    except Exception as e:
        logger.exception(f"One-shot MCP processing failed: {e}")
        response = Response(f"Internal error: {e}", status_code=500)

    await response(scope, receive, send)

# ============================================================
# MCP 服务器实例
# ============================================================
mcp = FastMCP("Knowledge Graph", streamable_http_path="/mcp-http")


# ============================================================
# Pydantic 模型 —— FastMCP 自动生成 JSON Schema
# ============================================================

class GraphNode(BaseModel):
    """图谱中的实体节点"""
    name: str = Field(description="实体名称")
    type: str = Field(description="实体类型，如 User/Project/AI")
    content: str = Field(description="实体内容（描述/备注），必填，简要说明该实体是什么")
    relations: list[dict] = Field(default_factory=list, description="关系声明，如 [{name:目标实体,rel:了解}]")
    properties: dict = Field(default_factory=dict, description="附加属性")
    is_root: bool | None = Field(default=None, description="是否设为根节点")


class GraphFact(BaseModel):
    """与实体关联的事实"""
    content: str = Field(description="事实内容")
    type: str = Field(default="fact", description="事实类型")
    about_entities: list[str] = Field(default_factory=list, description="关联的实体名称列表")


# ============================================================
# MCP 工具 —— 所有工具直接调 core/memory
# ============================================================

@mcp.tool(
    name="get_entity",
    description="精准匹配读取单个实体的完整字段（name/type/content/relations/properties/importance/pinned/is_root/created_at/updated_at/deprecated_at）。未找到返回 null。"
)
async def get_entity(
    name: str = Field(description="实体名称（精准匹配）"),
) -> dict | None:
    """MCP 工具入口 —— 读取实体完整信息。"""
    try:
        return await mem.get_entity(name)
    except Exception as e:
        return {"error": f"(无法读取实体: {e})"}


@mcp.tool(
    name="search_memory",
    description="搜索知识图谱中的长期记忆。返回与查询相关的实体、关系和事实。"
)
async def search_memory(query: str, top_k: int = 5) -> list[dict]:
    """MCP 工具入口 —— 搜索图谱记忆。"""
    try:
        return await mem.search(query, top_k)
    except Exception as e:
        return [{"error": f"(无法检索记忆: {e})"}]


@mcp.tool(
    name="save_to_graph",
    description=(
        "将实体和事实保存到知识图谱。"
        "nodes 是实体列表（每个节点必填 content 描述字段，"
        "可含 relations 关系声明，relations 会自动构建关系索引），"
        "facts 是事实列表。三者均可选，至少提供一个。"
    )
)
async def save_to_graph(
    nodes: list[GraphNode] = Field(default_factory=list, description="要新增或更新的实体（可含 content/relations）"),
    facts: list[GraphFact] | None = Field(default=None, description="要记住的事实"),
    importance: int | None = Field(default=None, ge=1, le=10, description="重要性（1-10）"),
    pinned: bool | None = Field(default=None, description="是否固定"),
    is_root: bool | None = Field(default=None, description="是否设为根节点"),
) -> str:
    """MCP 工具入口 —— 保存到图谱。"""
    try:
        nodes_dict = [n.model_dump() for n in nodes]
        facts_dict = [f.model_dump() for f in facts] if facts else None
        return await mem.save(
            nodes_dict, facts=facts_dict,
            importance=importance, pinned=pinned, is_root=is_root,
        )
    except Exception as e:
        return f"(无法保存到图谱: {e})"


@mcp.tool(
    name="list_memory",
    description=(
        "分层浏览知识图谱。无参数时返回类型概览（如 User (3个)）；"
        "传入 type 时列出该类型下所有实体标题和子节点数。"
        "只显示骨架，详细内容用 search_memory 获取。"
    )
)
async def list_memory(
    type: str | None = Field(default=None, description="实体类型，如 User/AI/Project。不传则返回类型概览"),
) -> str:
    """MCP 工具入口 —— 分层浏览图谱。"""
    try:
        return await mem.list_memory(type=type)
    except Exception as e:
        return f"(无法列出记忆: {e})"


@mcp.tool(
    name="list_pinned",
    description=(
        "列出所有固定（pinned）注入的实体，按重要度降序，"
        "含完整字段（name/type/content/relations/properties/importance）。"
        "适合每轮对话开始前注入的常驻规则记忆。"
    )
)
async def list_pinned() -> list[dict]:
    """MCP 工具入口 —— 列出所有固定注入的实体。"""
    try:
        return await mem.list_pinned()
    except Exception as e:
        return [{"error": f"(无法列出固定实体: {e})"}]


@mcp.tool(
    name="delete_from_graph",
    description=(
        "从知识图谱中删除实体或事实。"
        "target_type='entity' 时 target 填实体名称；"
        "target_type='fact' 时 target 填事实 ID；"
        "target_type='fact_by_content' 时 target 填关键词。"
    )
)
async def delete_from_graph(
    target_type: str = Field(description="删除类型：entity/fact/fact_by_content"),
    target: str = Field(description="目标标识（含义见描述）"),
) -> str:
    """MCP 工具入口 —— 从图谱删除内容。"""
    try:
        return await mem.delete_memory(target_type, target)
    except Exception as e:
        return f"(无法删除记忆: {e})"


@mcp.tool(
    name="update_memory",
    description=(
        "更新知识图谱中的已有记忆。支持更新事实内容和实体属性。"
        "target_type='fact' 时 target 填事实的数字 ID，updates 可含 content/type/about_entities；"
        "target_type='entity' 时 target 填实体名称，updates 可含 type/properties/content/relations；"
        "target_type='entity_importance' 时 target 填实体名称，updates 需含 importance(1-10)；"
        "target_type='entity_pinned' 时 target 填实体名称，updates 需含 pinned(true/false)；"
        "target_type='entity_root' 时 target 填实体名称，updates 需含 is_root(true/false)。"
    )
)
async def update_memory(
    target_type: str = Field(description="更新类型：fact / entity / entity_importance / entity_pinned"),
    target: str = Field(description="目标标识：事实填数字ID，实体填名称"),
    updates: dict = Field(default_factory=dict, description="更新字段字典（按 target_type 不同含义不同）"),
) -> str:
    """MCP 工具入口 —— 更新图谱记忆。"""
    try:
        return await mem.update(target_type, target, updates)
    except Exception as e:
        return f"(无法更新记忆: {e})"


@mcp.tool(
    name="merge_entities",
    description=(
        "将源实体合并到目标实体。迁移源实体的所有关系、事实关联和属性到目标实体，"
        "然后软删除源实体。可用于消除重复实体、重组图谱结构。"
        "合并后可用 update_memory 微调目标实体的属性。"
    )
)
async def merge_entities(
    source: str = Field(description="源实体名称（将被合并到目标实体后删除）"),
    target: str = Field(description="目标实体名称（接收所有迁移数据）"),
) -> str:
    """MCP 工具入口 —— 合并两个实体。"""
    try:
        return await mem.merge(source, target)
    except Exception as e:
        return f"(无法合并实体: {e})"


@mcp.tool(
    name="restore_memory",
    description=(
        "恢复已软删除的实体或事实。"
        "delete_from_graph 改为软删除后 24h 才自动清理，此工具可在期间恢复。"
        "target_type='entity' 时 target 填实体名称；"
        "target_type='fact' 时 target 填事实的数字 ID。"
    )
)
async def restore_memory(
    target_type: str = Field(description="恢复类型：entity / fact"),
    target: str = Field(description="目标标识：实体填名称，事实填数字ID"),
) -> str:
    """MCP 工具入口 —— 恢复已软删除的记忆。"""
    try:
        return await mem.restore(target_type, target)
    except Exception as e:
        return f"(无法恢复记忆: {e})"


@mcp.tool(
    name="list_deprecated",
    description="列出所有已软删除待清理的实体和事实（24h 后自动物理删除）。"
)
async def list_deprecated_tool() -> str:
    """MCP 工具入口 —— 列出已作废内容。"""
    try:
        return await mem.list_deprecated()
    except Exception as e:
        return f"(无法列出已作废内容: {e})"
