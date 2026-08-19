# FastAPI 应用入口：CORS 中间件、lifespan 生命周期管理、MCP 端点
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from app.core import graph
from app.core import logger
from app import mcp_server
from app.api.graph import router as graph_router
from app.api.phone_monitor import router as phone_router, start_poller as phone_start_poller
from app.api.theme import router as theme_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.init()
    await graph.init_db()
    phone_start_poller()  # 手机负载监控轮询（只读，失败仅无数据）
    async with mcp_server.mcp.session_manager.run():
        yield
    await graph.close()
    logger.close()


app = FastAPI(title="AI 知识图谱工具集", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:15173", "http://127.0.0.1:15173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 图谱 REST 端点
app.include_router(graph_router)

# 手机负载监控端点（纯被动只读，见 api/phone_monitor.py）
app.include_router(phone_router)

# 主题持久化端点（见 api/theme.py）
app.include_router(theme_router)

# ── MCP SSE 端点 ──────────────────────────────────────
app.mount("/mcp", mcp_server.mcp.sse_app())

# ── MCP Streamable HTTP 端点（Codex 自定义 MCP） ─────────
app.routes.extend(mcp_server.mcp.streamable_http_app().routes)

# ── MCP 直通端点 ──────────────────────────────────────
from starlette.responses import JSONResponse
import json as json_mod
from mcp.server.models import InitializationOptions
from mcp.server.session import ServerSession
from mcp.types import JSONRPCMessage
from mcp.shared.message import SessionMessage, ServerMessageMetadata
import anyio


@app.post("/mcp-direct")
async def mcp_direct(request: Request):
    """直接处理 MCP 请求，不依赖 SSE 长连接。"""
    body = await request.body()
    if not body:
        return Response("Empty body", status_code=400)

    try:
        jmsg = JSONRPCMessage.model_validate_json(body)
    except Exception as e:
        return Response(f"Invalid MCP message: {e}", status_code=400)

    mcp_srv = mcp_server.mcp._mcp_server
    init_opts = mcp_srv.create_initialization_options()
    result_json = None

    async def run_session():
        nonlocal result_json
        read_writer, read_reader = anyio.create_memory_object_stream(1)
        write_writer, write_reader = anyio.create_memory_object_stream(1)

        from contextlib import AsyncExitStack
        async with AsyncExitStack() as stack:
            lifespan_ctx = await stack.enter_async_context(mcp_srv.lifespan(mcp_srv))
            session = await stack.enter_async_context(
                ServerSession(read_reader, write_writer, init_opts, stateless=True)
            )

            session_msg = SessionMessage(jmsg, metadata=ServerMessageMetadata())
            await read_writer.send(session_msg)
            read_writer.close()

            async for msg in session.incoming_messages:
                await mcp_srv._handle_message(msg, session, lifespan_ctx, raise_exceptions=False)

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
            return Response(content=result_json, status_code=200, media_type="application/json")
        return Response("No response", status_code=500)

    except Exception as e:
        return Response(f"Internal error: {e}", status_code=500)


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
