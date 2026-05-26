from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.core import graph
from app.core import logger
from app.api.graph import router as graph_router
from app import mcp_server  # MCP SSE 端点，直连 core/memory，不走缓存


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.init()
    await graph.init_db()

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

app.include_router(graph_router)

static_dir = Path(__file__).resolve().parent / "static"
static_dir.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

# ── MCP SSE 端点 ──────────────────────────────────────
# 暴露知识图谱工具供外部 MCP 客户端（Reasonix、Claude Desktop 等）调用。
# 所有工具直接调 core/memory → SQLite，不走缓存。
# 客户端连接地址：http://127.0.0.1:18000/mcp/sse
# ───────────────────────────────────────────────────────
app.mount("/mcp", mcp_server.mcp.sse_app())

# ── MCP 直通端点（无状态单次处理） ──────────────────
# 绕过 SSE 长连接限制，每次请求独立创建 MCP session
from starlette.routing import Route
from app.mcp_server import mcp_oneshot_app
app.add_route("/mcp/direct", route=mcp_oneshot_app, methods=["POST"])


@app.get("/graph")
async def graph_view():
    from fastapi.responses import HTMLResponse
    html_path = static_dir / "graph.html"
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
