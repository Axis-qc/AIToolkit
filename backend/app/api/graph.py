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
