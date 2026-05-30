# 图谱 REST API 端点：提供实体/关系/事实的 CRUD 操作、搜索、工具调用接口
from typing import Annotated

from fastapi import APIRouter, Body

from app.core import memory
from app.core import graph as graph_core
from app.core import logger
from app.models.graph_tool import (
    DeleteFromGraphToolRequest,
    GraphToolResponse,
    ListMemoryToolRequest,
    SaveToGraphToolRequest,
    SearchMemoryToolRequest,
)

router = APIRouter(tags=["graph"])


# ── 图谱查询端点（无 LLM，只读 SQLite） ─────────────────────

@router.get("/api/graph")
async def get_graph():
    return await graph_core.get_all_graph()


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


# ── 图谱工具端点（直调 core/memory） ──────────────────────

def _tool_args(req) -> dict:
    return req.model_dump(exclude_none=True)


async def _run_tool(tool_name: str, fn, **kwargs) -> GraphToolResponse:
    log = logger.get()
    log.info("[tool] 调用 %s | args=%s", tool_name, str(kwargs)[:300])
    result = await fn(**kwargs)
    log.info("[tool] 结果 %s | result_len=%s", tool_name, len(result or ""))
    return GraphToolResponse(ok=True, tool=tool_name, result=result)


@router.post("/api/graph/tool/search_memory")
async def tool_search_memory(req: Annotated[SearchMemoryToolRequest, Body()]) -> GraphToolResponse:
    return await _run_tool("search_memory", memory.search, query=req.query, top_k=req.top_k)


@router.post("/api/graph/tool/save_to_graph")
async def tool_save_to_graph(req: Annotated[SaveToGraphToolRequest, Body()]) -> GraphToolResponse:
    args = _tool_args(req)
    return await _run_tool("save_to_graph", memory.save, nodes=args.get("nodes", []), relations=args.get("relations", []), facts=args.get("facts"))


@router.post("/api/graph/tool/list_memory")
async def tool_list_memory(req: Annotated[ListMemoryToolRequest, Body()]) -> GraphToolResponse:
    return await _run_tool("list_memory", memory.list_memory, type=req.type)


@router.post("/api/graph/tool/delete_from_graph")
async def tool_delete_from_graph(req: Annotated[DeleteFromGraphToolRequest, Body()]) -> GraphToolResponse:
    return await _run_tool("delete_from_graph", memory.delete_memory, target_type=req.target_type, target=req.target, rel_type=req.rel_type)


@router.post("/api/graph/tool/restore_memory")
async def tool_restore_memory(req: Annotated[DeleteFromGraphToolRequest, Body()]) -> GraphToolResponse:
    return await _run_tool("restore_memory", memory.restore, target_type=req.target_type, target=req.target)


@router.post("/api/graph/tool/list_deprecated")
async def tool_list_deprecated() -> GraphToolResponse:
    return await _run_tool("list_deprecated", memory.list_deprecated)
