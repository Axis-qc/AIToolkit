"""
图谱 REST 端点 —— 为前端可视化提供数据。
直调 core/graph（实际转发到 graph_crud / graph_view / graph_search），
不包含业务逻辑，仅做 HTTP 转接。
"""
from fastapi import APIRouter, Query
from app.core import graph

router = APIRouter(prefix="/api/graph", tags=["graph"])


@router.get("")
async def get_full_graph():
    """全量图谱数据（节点+边+事实）。"""
    return await graph.get_full_graph()


@router.get("/roots")
async def get_roots():
    """根节点列表。"""
    return await graph.get_roots()


@router.get("/children")
async def get_children(
    type: str = Query(..., description="实体类型"),
    name: str = Query(..., description="实体名称"),
):
    """指定实体的子节点（出边邻居）。"""
    return await graph.get_children(type, name)


@router.get("/facts")
async def get_facts(
    type: str = Query(..., description="实体类型"),
    name: str = Query(..., description="实体名称"),
):
    """指定实体的关联事实。"""
    return await graph.get_facts(type, name)


@router.get("/orphans")
async def get_orphans():
    """孤立节点（没有任何关系）。"""
    return await graph.get_orphans()


@router.get("/entities")
async def list_entities(
    offset: int = Query(0, ge=0, description="起始偏移"),
    limit: int = Query(100, ge=1, le=1000, description="每页条数"),
    type: str | None = Query(None, description="按实体类型过滤"),
    include_content: bool = Query(True, description="是否返回 content"),
):
    """分页列出实体（默认带 content），用于分批判断过时。"""
    return await graph.list_entities_paged(
        offset=offset, limit=limit, entity_type=type, include_content=include_content
    )


@router.get("/health")
async def health(
    checks: str | None = Query(
        None,
        description="逗号分隔的检查项：duplicates,stale,types,dangling,lint，不传则全查",
    ),
):
    """图谱体检（只读）：重复候选、过时台账、类型碎片、悬空关系、lint 违规。"""
    parsed = [c.strip() for c in checks.split(",") if c.strip()] if checks else None
    return await graph.health_check(parsed)
