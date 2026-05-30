# Pydantic 数据模型：定义图谱工具的请求/响应数据结构，包括节点、关系、事实等类型
from typing import Any

from pydantic import BaseModel, Field


class GraphNode(BaseModel):
    name: str
    type: str
    properties: dict[str, Any] = Field(default_factory=dict)


class GraphRelation(BaseModel):
    from_type: str
    from_name: str
    to_type: str
    to_name: str
    rel_type: str
    properties: dict[str, Any] = Field(default_factory=dict)


class GraphFact(BaseModel):
    content: str
    type: str = "fact"
    about_entities: list[str] = Field(default_factory=list)


class SearchMemoryToolRequest(BaseModel):
    query: str
    top_k: int = 5


class SaveToGraphToolRequest(BaseModel):
    nodes: list[GraphNode] = Field(default_factory=list)
    relations: list[GraphRelation] = Field(default_factory=list)
    facts: list[GraphFact] | None = None
    importance: int | None = None
    pinned: bool | None = None


class ListMemoryToolRequest(BaseModel):
    type: str | None = None


class DeleteFromGraphToolRequest(BaseModel):
    target_type: str
    target: str
    rel_type: str | None = None


class GraphToolResponse(BaseModel):
    ok: bool
    tool: str
    result: str
