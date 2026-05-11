from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.core import graph
from app.core import logger
from app.core.config import settings
from app.core import file_guard
from app.core.cache import cache
from app.api.chat import router as chat_router
from app.api.graph import router as graph_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "conversations"
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    logger.init()
    await graph.init_db()
    file_guard.init(settings.whitelist_paths)
    cache.load_from_disk()

    yield

    cache.save_to_disk()
    await graph.close()
    logger.close()


app = FastAPI(title="AI 知识图谱记忆系统", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:15173", "http://127.0.0.1:15173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(chat_router)
app.include_router(graph_router)

static_dir = Path(__file__).resolve().parent / "static"
static_dir.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


@app.get("/graph")
async def graph_view():
    from fastapi.responses import HTMLResponse
    html_path = static_dir / "graph.html"
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
