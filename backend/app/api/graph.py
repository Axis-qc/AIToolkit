from typing import Annotated

from fastapi import APIRouter, Body

from app.core import config_loader
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


@router.post("/api/graph/search")
async def search_graph(query: str = Body(...), top_k: int = Body(5)) -> dict:
    log = logger.get()
    log.info("图谱记忆检索请求: query_len=%s top_k=%s", len(query or ""), top_k)
    summary = await memory.search(query, top_k)
    no_result = config_loader.get_injection_config()["no_result"]
    log.info(
        "图谱记忆检索完成: has_result=%s summary_len=%s",
        bool(summary and summary != no_result),
        len(summary or ""),
    )
    return {"summary": summary, "has_result": bool(summary and summary != no_result)}


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
    return await _run_tool("list_memory", memory.list_memory, mode=req.mode, entity_name=req.entity_name, depth=req.depth)


@router.post("/api/graph/tool/delete_from_graph")
async def tool_delete_from_graph(req: Annotated[DeleteFromGraphToolRequest, Body()]) -> GraphToolResponse:
    return await _run_tool("delete_from_graph", memory.delete_memory, target_type=req.target_type, target=req.target, rel_type=req.rel_type)


@router.post("/api/graph/tool/restore_memory")
async def tool_restore_memory(req: Annotated[DeleteFromGraphToolRequest, Body()]) -> GraphToolResponse:
    return await _run_tool("restore_memory", memory.restore, target_type=req.target_type, target=req.target)


@router.post("/api/graph/tool/list_deprecated")
async def tool_list_deprecated() -> GraphToolResponse:
    return await _run_tool("list_deprecated", memory.list_deprecated)
