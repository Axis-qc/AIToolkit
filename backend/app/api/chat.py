import asyncio
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

import httpx
import tiktoken
from fastapi import APIRouter, Body
from fastapi.responses import StreamingResponse
from openai import AsyncOpenAI

from app.core import memory
from app.core import storage
from app.core import logger as log
from app.core.config import settings
from app.core.cache import cache, hash_messages, hash_args, hash_content, TTL_REQUEST, TTL_TOOL_RESULT, WRITE_TOOLS
from app.models.chat import ChatRequest
from app.tools import TOOL_DEFINITIONS, dispatch

router = APIRouter(tags=["chat"])

client = AsyncOpenAI(
    api_key=settings.chat_api_key,
    base_url=settings.chat_base_url,
    http_client=httpx.AsyncClient(trust_env=False),
)

from ..core import config_loader

enc = tiktoken.get_encoding("cl100k_base")

SYSTEM_PROMPT = config_loader.render_prompt("chat_system",
    center_list=config_loader.build_center_list_md(),
    category_list=config_loader.build_category_list_md())

MAIN_CONV_ID = "main"
CONTEXT_LIMIT = 200000


def _estimate_tokens(text: str) -> int:
    try:
        return len(enc.encode(text))
    except Exception:
        return len(text) // 2


_HEADER_RE = re.compile(r'^.+\s\(\d+-\d+/\d+\)$')


def _slice_and_format(path: str, full_content: str, offset: int, limit: int) -> str:
    lines = full_content.splitlines()
    total = len(lines)
    if offset < 1:
        offset = 1
    if offset > total:
        return f"{path} (0-0/{total})"
    start = offset - 1
    end = min(start + limit, total)
    chunk = lines[start:end]
    result_lines = [f"{i}: {line}" for i, line in enumerate(chunk, start + 1)]
    header = f"{path} ({start + 1}-{end}/{total})"
    return header + "\n" + "\n".join(result_lines)


def _unformat(formatted: str) -> str | None:
    lines = formatted.splitlines()
    if lines and _HEADER_RE.match(lines[0]):
        lines = lines[1:]
    else:
        return None

    result = []
    for line in lines:
        if ": " in line:
            colon_idx = line.index(": ")
            result.append(line[colon_idx + 2:])
        else:
            result.append(line)
    return "\n".join(result)


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

        # ── 缓存写入准备 ──
        event_buffer: list[dict] = []
        had_write = False

        def _emit(event: dict) -> str:
            """记录事件到缓冲区并返回 SSE 字符串。"""
            event_buffer.append(event)
            return f"data: {json.dumps(event)}\n\n"

        if should_archive:
            yield _emit({'type': 'archived', 'archived_id': archived_id, 'title': archived_title})

        current_messages = messages

        # ── 请求级缓存检查（在记忆注入前，按单条用户消息计算 key）──
        last_user_content = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                last_user_content = m.get("content", "")
                break
        req_cache_key = f"req:{settings.chat_model}:{hash_content(last_user_content)}"
        cached_events = cache.get(req_cache_key)
        if cached_events is not None:
            log.get().info(f"[缓存] 请求级命中 | key={req_cache_key[:24]}...")
            for evt in cached_events:
                yield f"data: {json.dumps(evt)}\n\n"
            return

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
                    yield _emit({'type': 'memory_context', 'content': memory_result})
                else:
                    yield _emit({'type': 'memory_context', 'content': ''})
            except Exception as e:
                log.get().warning(f"[记忆] 预检索失败，跳过注入: {e}")
                yield _emit({'type': 'memory_context', 'content': ''})

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
                        yield _emit({'type': 'token', 'content': delta.content})

                    if getattr(delta, 'reasoning_content', None):
                        reasoning_buffer += delta.reasoning_content
                        yield _emit({'type': 'reasoning', 'content': delta.reasoning_content})

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
                            try:
                                args = json.loads(tc["arguments"]) if tc["arguments"] else {}
                            except json.JSONDecodeError as e:
                                log.get().warning(f"[工具] JSON解析失败 {tc['name']}: {e} | raw={tc['arguments'][:200]}")
                                args = {}
                            args_preview = json.dumps(args, ensure_ascii=False)[:120]
                            log.get().info(f"[工具] 调用 {tc['name']} | args={args_preview}")
                            yield _emit({'type': 'tool_call', 'name': tc['name'], 'arguments': args})

                    current_messages.append(assistant_msg)

                    for tc in tool_calls:
                        if tc["name"]:
                            try:
                                args = json.loads(tc["arguments"]) if tc["arguments"] else {}
                            except json.JSONDecodeError as e:
                                log.get().warning(f"[工具] JSON解析失败 {tc['name']}: {e} | raw={tc['arguments'][:200]}")
                                args = {}

                            # ── 工具结果缓存 ──
                            if tc['name'] == 'read_file':
                                path = args.get('path', '')
                                offset = args.get('offset', 1)
                                limit = args.get('limit', 200)
                                full_cache_key = f"tool:read_file:{hash_args({'path': path})}"

                                cached_full = cache.get(full_cache_key)
                                if cached_full is not None:
                                    result = _slice_and_format(path, cached_full, offset, limit)
                                    log.get().info(f"[缓存] read_file 命中 {path}")
                                else:
                                    raw_result = await dispatch('read_file',
                                        {'path': path, 'offset': 1, 'limit': 999999}, conv_id=conv_id)
                                    raw = _unformat(raw_result)
                                    if raw is not None:
                                        cache.set(full_cache_key, raw)
                                        result = _slice_and_format(path, raw, offset, limit)
                                    else:
                                        result = raw_result
                            else:
                                tool_cache_key = f"tool:{tc['name']}:{hash_args(args)}"
                                cached_result = cache.get(tool_cache_key)
                                if cached_result is not None:
                                    result = cached_result
                                    log.get().info(f"[缓存] 工具结果命中 {tc['name']}")
                                else:
                                    result = await dispatch(tc["name"], args, conv_id=conv_id)
                                    if tc['name'] not in WRITE_TOOLS:
                                        cache.set(tool_cache_key, result, TTL_TOOL_RESULT)

                            # ── 写操作触发失效 ──
                            if tc['name'] in WRITE_TOOLS:
                                if tc['name'] in ('write_file', 'edit_file'):
                                    path = args.get('path', '')
                                    if path:
                                        cache.invalidate_file_cache(path)
                                cache.invalidate_write(tc['name'])
                                had_write = True

                            result_preview = result[:120].replace("\n", " ")
                            log.get().info(f"[工具] 结果 {tc['name']} | {result_preview}")
                            yield _emit({'type': 'tool_result', 'tool_call_id': tc['id'], 'content': result})
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

        done_event = {'type': 'done', 'conversation_id': conv_id, 'prompt_tokens': total_prompt_tokens, 'completion_tokens': total_completion_tokens}

        # ── 请求级缓存写入（无写操作时） ──
        if not had_write:
            event_buffer.append(done_event)
            cache.set(req_cache_key, event_buffer, TTL_REQUEST)
            log.get().info(f"[缓存] 请求级写入 | key={req_cache_key[:24]}... | events={len(event_buffer)}")

        yield f"data: {json.dumps(done_event)}\n\n"
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
