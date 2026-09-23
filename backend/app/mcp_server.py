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
    description=(
        "搜索知识图谱中的长期记忆。返回与查询相关的实体、关系和事实。"
        "结果带 score（检索得分）与 matched_fields（哪个字段贡献了多少分、命中了哪些词），"
        "用于判断两个相似实体是否在互相抢位，以及整理后检索是否真的改善。"
    )
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
        "建议先用 preview=true 干跑：返回会迁走哪些关系、哪些事实、属性有无冲突，"
        "以及类型是否冲突，确认后再正式执行。合并后可用 update_memory 微调目标实体的属性。"
    )
)
async def merge_entities(
    source: str = Field(description="源实体名称（将被合并到目标实体后删除）"),
    target: str = Field(description="目标实体名称（接收所有迁移数据）"),
    preview: bool = Field(default=False, description="是否只干跑预览，不写入任何数据"),
) -> str:
    """MCP 工具入口 —— 合并两个实体。"""
    try:
        return await mem.merge(source, target, preview=preview)
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
    description="列出所有已软删除待清理的实体和事实（超过保留窗口后自动物理删除）。"
)
async def list_deprecated_tool() -> str:
    """MCP 工具入口 —— 列出已作废内容。"""
    try:
        return await mem.list_deprecated()
    except Exception as e:
        return f"(无法列出已作废内容: {e})"


@mcp.tool(
    name="graph_health_check",
    description=(
        "知识图谱体检：一次调用返回五类问题的清单，每条都带可判定证据，只读不改数据。"
        "duplicates 重复候选（以 content 字符二元组余弦为主信号，分高置信与待观察两档，"
        "名字相似度只作辅助证据）；"
        "stale 过时台账（90/180 两档，并单列时间戳缺失与格式非法的实体）；"
        "types 类型碎片与单例清单（含大小写变体归并建议）；"
        "dangling 悬空关系清单（指向不存在实体的关系索引，被前端 JOIN 静默丢弃）；"
        "lint 违规清单（content 必填、环境前缀、装饰符号）。"
        "checks 为空则全查，也可只跑其中几项，如 ['duplicates','lint']。"
    )
)
async def graph_health_check(
    checks: list[str] | None = Field(
        default=None,
        description="要执行的检查项，可选 duplicates/stale/types/dangling/lint，不传则全查",
    ),
) -> dict:
    """MCP 工具入口 —— 图谱体检。"""
    try:
        return await mem.health(checks)
    except Exception as e:
        return {"error": f"(无法完成体检: {e})"}


@mcp.tool(
    name="get_entities",
    description=(
        "批量读取实体完整字段（含 content 与时间戳）。传入名字数组，一次拿回全部内容，"
        "替代逐条 get_entity：判断 641 个实体是否过时不必再调几百次。"
        "返回 found 与 missing 两份，缺失的名字明确列出。"
    )
)
async def get_entities(
    names: list[str] = Field(description="实体名称数组（精准匹配）"),
) -> dict:
    """MCP 工具入口 —— 批量读取实体。"""
    try:
        return await mem.get_entities(names)
    except Exception as e:
        return {"error": f"(无法批量读取实体: {e})"}


@mcp.tool(
    name="list_entities",
    description=(
        "分页列出实体，默认带 content，用于分批遍历全库判断过时。"
        "返回 total（总数）、offset、limit、items。可用 type 过滤。"
    )
)
async def list_entities(
    offset: int = Field(default=0, description="起始偏移"),
    limit: int = Field(default=100, description="每页条数（1-1000）"),
    type: str | None = Field(default=None, description="按实体类型过滤，不传则全部"),
    include_content: bool = Field(default=True, description="是否返回 content 字段"),
) -> dict:
    """MCP 工具入口 —— 分页列出实体。"""
    try:
        return await mem.list_entities(
            offset=offset, limit=limit, entity_type=type, include_content=include_content
        )
    except Exception as e:
        return {"error": f"(无法列出实体: {e})"}


@mcp.tool(
    name="mark_verified",
    description=(
        "批量写入或清除实体的过时标注，落在结构化字段 stale_marked_at/verified_until 上，"
        "不写正文，因此不靠文本匹配就能查。verified_until 表示核实到哪个日期为止有效，"
        "体检的过时台账优先按它判定。clear=true 表示核实无误，去掉标注。"
    )
)
async def mark_verified(
    names: list[str] = Field(description="实体名称数组"),
    verified_until: str | None = Field(
        default=None, description="核实截止日期，格式 YYYY:MM:DD:HH:MM:SS，不传则无截止"
    ),
    stale_marked_at: str | None = Field(
        default=None, description="标注时间，不传则取当前系统时间"
    ),
    clear: bool = Field(default=False, description="是否清除标注（核实无误时用）"),
) -> str:
    """MCP 工具入口 —— 批量写入或清除过时标注。"""
    try:
        return await mem.mark_verified(
            names, verified_until=verified_until, stale_marked_at=stale_marked_at, clear=clear
        )
    except Exception as e:
        return f"(无法写入过时标注: {e})"


@mcp.tool(
    name="batch_update_memory",
    description=(
        "批量修改实体：重设类型 / 打标注 / 设重要度 / 软删除，一次处理一批目标。"
        "整理天生是批量的，此工具替代逐条 update_memory。"
        "返回 applied 与 missing 两份名单，未找到的不会被静默跳过。"
    )
)
async def batch_update_memory(
    names: list[str] = Field(description="实体名称数组"),
    new_type: str | None = Field(default=None, description="统一改成这个类型"),
    importance: int | None = Field(default=None, ge=1, le=10, description="统一设为这个重要度"),
    stale_marked_at: str | None = Field(default=None, description="统一打过时标注的时间"),
    verified_until: str | None = Field(default=None, description="统一设核实截止日期"),
    clear_stale: bool = Field(default=False, description="统一清除过时标注"),
    soft_delete: bool = Field(default=False, description="统一软删除（保留窗口内可恢复）"),
) -> str:
    """MCP 工具入口 —— 批量修改实体。"""
    try:
        return await mem.batch_update(
            names,
            new_type=new_type,
            importance=importance,
            stale_marked_at=stale_marked_at,
            verified_until=verified_until,
            clear_stale=clear_stale,
            soft_delete=soft_delete,
        )
    except Exception as e:
        return f"(无法批量修改实体: {e})"


@mcp.tool(
    name="list_tombstones",
    description=(
        "查看墓地：已过保留窗口、被物理删除的实体、事实与关系。"
        "用于「我出清单、你确认、再执行」跨天回来后的追溯。"
    )
)
async def list_tombstones(
    kind: str | None = Field(default=None, description="过滤类型：entity/fact/relation，不传则全部"),
    limit: int = Field(default=200, description="最多返回条数"),
) -> str:
    """MCP 工具入口 —— 查看已清理内容。"""
    try:
        return await mem.list_tombstones(kind=kind, limit=limit)
    except Exception as e:
        return f"(无法列出已清理内容: {e})"
