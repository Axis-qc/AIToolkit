"""主题持久化 API：读取 / 保存前端主题主色（hex）。

存储于 backend/data/theme.json，无则用默认经典金 #d4af37。
仅存主色，前端负责派生高光/暗色/线条等整套配色。
"""
import json
import re
from pathlib import Path
from fastapi import APIRouter
from pydantic import BaseModel

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
THEME_FILE = DATA_DIR / "theme.json"

DEFAULT_ACCENT = "#d4af37"

router = APIRouter(prefix="/api/theme", tags=["theme"])

_HEX_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


class ThemePayload(BaseModel):
    accent: str


def _ensure_file() -> dict:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not THEME_FILE.exists():
        THEME_FILE.write_text(
            json.dumps({"accent": DEFAULT_ACCENT}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return {"accent": DEFAULT_ACCENT}
    try:
        data = json.loads(THEME_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not _HEX_RE.match(str(data.get("accent", ""))):
            return {"accent": DEFAULT_ACCENT}
        return data
    except Exception:
        return {"accent": DEFAULT_ACCENT}


@router.get("")
async def get_theme() -> dict:
    """当前主题主色。"""
    data = _ensure_file()
    return {"accent": data.get("accent", DEFAULT_ACCENT)}


@router.put("")
async def set_theme(payload: ThemePayload) -> dict:
    """保存主题主色，校验 hex 格式（#rgb / #rrggbb）。"""
    accent = (payload.accent or "").strip()
    if not _HEX_RE.match(accent):
        return {"ok": False, "error": "无效的主色格式，需为 #rgb 或 #rrggbb"}
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    THEME_FILE.write_text(
        json.dumps({"accent": accent}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return {"ok": True, "accent": accent}
