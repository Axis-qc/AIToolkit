from pydantic import BaseModel


class ChatRequest(BaseModel):
    conversation_id: str | None = None
    messages: list[dict]


class UpdateGraphRequest(BaseModel):
    conversation_id: str


class UpdateGraphResponse(BaseModel):
    success: bool
    summary: str


class ConversationItem(BaseModel):
    id: str
    title: str
    created_at: str
    updated_at: str
    archived: bool
    message_count: int


class ConversationListResponse(BaseModel):
    conversations: list[ConversationItem]


class ConversationDetail(BaseModel):
    id: str
    title: str
    created_at: str
    updated_at: str
    archived: bool
    messages: list[dict]
