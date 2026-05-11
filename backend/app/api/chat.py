import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

import tiktoken
from fastapi import APIRouter, Body
from fastapi.responses import StreamingResponse
from openai import AsyncOpenAI

from app.core import memory
from app.core import storage
from app.core import logger as log
from app.core.config import settings
from app.models.chat import ChatRequest
from app.tools import TOOL_DEFINITIONS, dispatch

router = APIRouter(tags=["chat"])

client = AsyncOpenAI(
    api_key=settings.chat_api_key,
    base_url=settings.chat_base_url,
)

enc = tiktoken.get_encoding("cl100k_base")

SYSTEM_PROMPT = (Path(__file__).resolve().parent.parent / "prompts" / "chat_system.txt").read_text(encoding="utf-8")

MAIN_CONV_ID = "main"
CONTEXT_LIMIT = 200000


def _estimate_tokens(text: str) -> int:
    try:
        return len(enc.encode(text))
    except Exception:
        return len(text) // 2


@router.post("/api/chat")
async def chat(req: Annotated[ChatRequest, Body()]) -> StreamingResponse:
    conv_id = MAIN_CONV_ID
    should_archive = False
    archived_id = ""
    archived_title = ""

    user_msgs = [m.get("content", "") for m in req.messages if m.get("role") == "user"]
    last_user = user_msgs[-1][:80] if user_msgs else "(空)"
    log.get().info(f"[请求] 用户消息: {last_user} | 上下文 {len(req.messages)} 条")

    prev = storage.load(MAIN_CONV_ID)
    if prev and prev.get("messages"):
        existing_text = ""
        for m in prev["messages"]:
            existing_text += str(m.get("content", ""))
        existing_tokens = _estimate_tokens(existing_text)

        new_text = ""
        for m in req.messages:
            new_text += str(m.get("content", ""))
        new_tokens = _estimate_tokens(new_text)

        if existing_tokens + new_tokens > CONTEXT_LIMIT:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            archived_id = f"main_archive_{ts}"
            archived_title = (prev.get("title") or "对话") + " (归档)"
            log.get().info(f"[归档] 超限 {existing_tokens + new_tokens} tokens → {archived_id} | 旧消息 {len(prev['messages'])} 条")
            storage.save(archived_id, archived_title, prev["messages"],
                         archived=True, last_prompt_tokens=prev.get("last_prompt_tokens", 0))
            storage.clear(MAIN_CONV_ID)

            async def _safe_archive(cid: str):
                try:
                    await memory.archive_conversation(cid)
                    log.get().info(f"[归档] 后台图谱更新完成: {cid}")
                except Exception as e:
                    log.get().error(f"[归档] 后台图谱更新失败: {cid} | {e}")
            asyncio.create_task(_safe_archive(archived_id))

            user_msgs = [m for m in req.messages if m.get("role") == "user"]
            new_messages = user_msgs[-1:] if user_msgs else req.messages[-1:]
            req.messages = new_messages
            should_archive = True

    clean_req = []
    for m in req.messages:
        if m.get("role") == "tool":
            continue
        if m.get("role") == "assistant" and not m.get("content") and not m.get("tool_calls"):
            continue
        clean_req.append(m)
    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + clean_req

    async def generate():
        yield ": connected\n\n"

        if should_archive:
            yield f"data: {json.dumps({'type': 'archived', 'archived_id': archived_id, 'title': archived_title})}\n\n"

        current_messages = messages

        # === 自动检索记忆并注入上下文 ===
        last_user_msg = ""
        last_user_idx = -1
        for i in range(len(current_messages) - 1, -1, -1):
            if current_messages[i].get("role") == "user":
                last_user_msg = current_messages[i].get("content", "")
                last_user_idx = i
                break

        if last_user_msg:
            try:
                memory_result = await memory.search(query=last_user_msg, top_k=15)
                if memory_result and memory_result != "（未找到相关记忆）":
                    memory_msg = {
                        "role": "system",
                        "content": "[记忆检索结果]\n" + memory_result,
                    }
                    current_messages.insert(last_user_idx, memory_msg)
                    log.get().info(f"[记忆] 注入记忆梗概 ({len(memory_result)} 字符)")
                    yield f"data: {json.dumps({'type': 'memory_context', 'content': memory_result})}\n\n"
                else:
                    yield f"data: {json.dumps({'type': 'memory_context', 'content': ''})}\n\n"
            except Exception as e:
                log.get().warning(f"[记忆] 预检索失败，跳过注入: {e}")
                yield f"data: {json.dumps({'type': 'memory_context', 'content': ''})}\n\n"

        def _save_checkpoint():
            now = datetime.now(timezone.utc).isoformat()
            title = ""
            saved = []
            for m in current_messages[1:]:
                entry = {**m, "timestamp": now}
                saved.append(entry)
                if not title and m["role"] == "user":
                    title = m.get("content", "")[:30]
            storage.save(conv_id, title, saved, archived=False, last_prompt_tokens=0)

        done = False
        round_num = 0

        try:
            while not done:
                round_num += 1
                msg_count = len(current_messages)
                log.get().info(f"[LLM] 第 {round_num} 轮 | model={settings.chat_model} | messages={msg_count}")
                response = await client.chat.completions.create(
                    model=settings.chat_model,
                    messages=current_messages,
                    tools=TOOL_DEFINITIONS,
                    tool_choice="auto",
                    stream=True,
                )

                tool_calls = []
                content_buffer = ""
                reasoning_buffer = ""

                async for chunk in response:
                    delta = chunk.choices[0].delta

                    if delta.tool_calls:
                        for tc in delta.tool_calls:
                            if tc.index is None:
                                continue
                            while len(tool_calls) <= tc.index:
                                tool_calls.append({"id": "", "name": "", "arguments": ""})
                            if tc.id:
                                tool_calls[tc.index]["id"] = tc.id
                            if tc.function:
                                if tc.function.name:
                                    tool_calls[tc.index]["name"] = tc.function.name
                                if tc.function.arguments:
                                    tool_calls[tc.index]["arguments"] += tc.function.arguments

                    if delta.content:
                        content_buffer += delta.content
                        yield f"data: {json.dumps({'type': 'token', 'content': delta.content})}\n\n"

                    if getattr(delta, 'reasoning_content', None):
                        reasoning_buffer += delta.reasoning_content
                        yield f"data: {json.dumps({'type': 'reasoning', 'content': delta.reasoning_content})}\n\n"

                if tool_calls:
                    assistant_msg = {
                        "role": "assistant",
                        "content": content_buffer or None,
                        "tool_calls": [],
                    }
                    if reasoning_buffer:
                        assistant_msg["reasoning_content"] = reasoning_buffer
                    for tc in tool_calls:
                        if tc["name"]:
                            assistant_msg["tool_calls"].append({
                                "id": tc["id"],
                                "type": "function",
                                "function": {
                                    "name": tc["name"],
                                    "arguments": tc["arguments"],
                                },
                            })
                            args = json.loads(tc["arguments"]) if tc["arguments"] else {}
                            args_preview = json.dumps(args, ensure_ascii=False)[:120]
                            log.get().info(f"[工具] 调用 {tc['name']} | args={args_preview}")
                            yield f"data: {json.dumps({'type': 'tool_call', 'name': tc['name'], 'arguments': args})}\n\n"

                    current_messages.append(assistant_msg)

                    for tc in tool_calls:
                        if tc["name"]:
                            args = json.loads(tc["arguments"]) if tc["arguments"] else {}
                            result = await dispatch(tc["name"], args, conv_id=conv_id)
                            result_preview = result[:120].replace("\n", " ")
                            log.get().info(f"[工具] 结果 {tc['name']} | {result_preview}")
                            yield f"data: {json.dumps({'type': 'tool_result', 'tool_call_id': tc['id'], 'content': result})}\n\n"
                            current_messages.append({
                                "role": "tool",
                                "tool_call_id": tc["id"],
                                "content": result,
                            })

                    _save_checkpoint()
                else:
                    assistant_msg = {
                        "role": "assistant",
                        "content": content_buffer or None,
                    }
                    if reasoning_buffer:
                        assistant_msg["reasoning_content"] = reasoning_buffer
                    current_messages.append(assistant_msg)
                    done = True
                    _save_checkpoint()
        finally:
            _save_checkpoint()

        now = datetime.now(timezone.utc).isoformat()
        title = ""
        saved_messages = []
        prompt_text = ""
        completion_text = ""
        for m in current_messages[1:]:
            entry = {**m, "timestamp": now}
            saved_messages.append(entry)
            if not title and m["role"] == "user":
                title = m.get("content", "")[:30]
            prompt_text += str(m.get("content", ""))
            if m["role"] == "assistant":
                completion_text += str(m.get("content", "") or "") + str(m.get("reasoning_content", "") or "")
        prompt_text = str(current_messages[0].get("content", "")) + prompt_text

        try:
            total_prompt_tokens = len(enc.encode(prompt_text))
            total_completion_tokens = len(enc.encode(completion_text))
        except Exception:
            total_prompt_tokens = sum(len(str(m.get("content", ""))) for m in current_messages) // 2
            total_completion_tokens = completion_text and len(completion_text) // 2 or 0

        for i in range(len(saved_messages) - 1, -1, -1):
            if saved_messages[i]["role"] == "assistant":
                saved_messages[i]["prompt_tokens"] = total_prompt_tokens
                saved_messages[i]["completion_tokens"] = total_completion_tokens
                break

        file_path = storage.save(conv_id, title, saved_messages,
                                 archived=False, last_prompt_tokens=total_prompt_tokens)
        try:
            await memory.index_conversation(conv_id, file_path, title)
        except Exception:
            pass

        yield f"data: {json.dumps({'type': 'done', 'conversation_id': conv_id, 'prompt_tokens': total_prompt_tokens, 'completion_tokens': total_completion_tokens})}\n\n"
        log.get().info(f"[完成] prompt={total_prompt_tokens} completion={total_completion_tokens} | 总消息 {len(saved_messages)} 条")

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
