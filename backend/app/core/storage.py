import json
import uuid
from datetime import datetime, date
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "conversations"
MAIN_CONV_ID = "main"


def _ensure_dir(path: Path):
    path.mkdir(parents=True, exist_ok=True)


def _conv_file(conv_id: str) -> Path | None:
    if not DATA_DIR.exists():
        return None
    if conv_id == MAIN_CONV_ID:
        f = DATA_DIR / "main.json"
        return f if f.exists() else None
    for f in DATA_DIR.rglob(f"{conv_id}.json"):
        return f
    return None


def _migrate_main_if_needed() -> Path | None:
    """检测日期目录中的旧 main.json 并移到根目录"""
    for f in DATA_DIR.rglob("main.json"):
        if f.parent != DATA_DIR:
            try:
                content = f.read_text(encoding="utf-8")
                f.unlink()
                (DATA_DIR / "main.json").write_text(content, encoding="utf-8")
                return DATA_DIR / "main.json"
            except Exception:
                pass
    return None


def generate_id() -> str:
    return uuid.uuid4().hex[:12]


def save(conv_id: str, title: str, messages: list[dict],
         archived: bool = False, last_prompt_tokens: int = 0) -> str:
    now = datetime.utcnow().isoformat()

    if conv_id == MAIN_CONV_ID:
        file_path = _conv_file(MAIN_CONV_ID)
        if not file_path:
            migrated = _migrate_main_if_needed()
            file_path = migrated or (DATA_DIR / "main.json")
    else:
        today = date.today()
        file_dir = DATA_DIR / str(today.year) / f"{today.month:02d}-{today.day:02d}"
        _ensure_dir(file_dir)
        file_path = file_dir / f"{conv_id}.json"

    data = {
        "id": conv_id,
        "title": title,
        "created_at": now,
        "updated_at": now,
        "archived": archived,
        "last_prompt_tokens": last_prompt_tokens,
        "messages": messages,
    }

    if file_path.exists():
        try:
            existing = json.loads(file_path.read_text(encoding="utf-8"))
            data["created_at"] = existing.get("created_at", now)
            data["archived"] = existing.get("archived", False) or archived
            if not last_prompt_tokens:
                data["last_prompt_tokens"] = existing.get("last_prompt_tokens", 0)
        except Exception:
            pass

    file_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(file_path.relative_to(DATA_DIR.parent.parent))


def mark_archived_file(conv_id: str):
    data = load(conv_id)
    if data:
        data["archived"] = True
        f = _conv_file(conv_id)
        if f:
            _write_json(f, data)


def _write_json(path: Path, data: dict):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load(conv_id: str) -> dict | None:
    f = _conv_file(conv_id)
    if not f:
        return None
    raw = f.read_text(encoding="utf-8").strip()
    if not raw:
        return None
    return json.loads(raw)


def clear(conv_id: str) -> str | None:
    f = _conv_file(conv_id)
    if not f:
        return None
    raw = f.read_text(encoding="utf-8").strip()
    data = json.loads(raw) if raw else {}
    now = datetime.utcnow().isoformat()
    data["messages"] = []
    data["updated_at"] = now
    data["last_prompt_tokens"] = 0
    _write_json(f, data)
    return str(f.relative_to(DATA_DIR.parent.parent))


def delete(conv_id: str) -> bool:
    f = _conv_file(conv_id)
    if not f:
        return False
    f.unlink()
    return True


def list_all() -> list[dict]:
    conversations = []
    if not DATA_DIR.exists():
        return conversations

    main_file = DATA_DIR / "main.json"
    if main_file.exists():
        try:
            data = json.loads(main_file.read_text(encoding="utf-8"))
            conversations.append({
                "id": data.get("id", "main"),
                "title": data.get("title", ""),
                "created_at": data.get("created_at", ""),
                "updated_at": data.get("updated_at", ""),
                "archived": data.get("archived", False),
                "message_count": len(data.get("messages", [])),
                "last_prompt_tokens": data.get("last_prompt_tokens", 0),
            })
        except Exception:
            pass

    for year_dir in sorted(DATA_DIR.glob("*"), reverse=True):
        if not year_dir.is_dir():
            continue
        for date_dir in sorted(year_dir.iterdir(), reverse=True):
            if not date_dir.is_dir():
                continue
            for f in sorted(date_dir.glob("*.json"), reverse=True):
                try:
                    raw = f.read_text(encoding="utf-8").strip()
                    if not raw:
                        continue
                    data = json.loads(raw)
                    conversations.append({
                        "id": data.get("id"),
                        "title": data.get("title", ""),
                        "created_at": data.get("created_at", ""),
                        "updated_at": data.get("updated_at", ""),
                        "archived": data.get("archived", False),
                        "message_count": len(data.get("messages", [])),
                        "last_prompt_tokens": data.get("last_prompt_tokens", 0),
                    })
                except Exception:
                    pass
    return conversations
