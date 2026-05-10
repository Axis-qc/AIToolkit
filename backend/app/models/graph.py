from pydantic import BaseModel


class EntityNode(BaseModel):
    name: str
    type: str
    properties: dict | None = None


class RelationInput(BaseModel):
    from_type: str
    from_name: str
    to_type: str
    to_name: str
    rel_type: str
    properties: dict | None = None


class FactInput(BaseModel):
    content: str
    type: str
    about_entities: list[str] | None = None


class SaveToGraphInput(BaseModel):
    nodes: list[EntityNode]
    relations: list[RelationInput]
    facts: list[FactInput] | None = None
