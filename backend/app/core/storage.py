import json
import uuid
from datetime import datetime, date
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "conversations"


def _ensure_dir(path: Path):
    path.mkdir(parents=True, exist_ok=True)


def _conv_file(conv_id: str) -> Path | None:
    if not DATA_DIR.exists():
        return None
    for f in DATA_DIR.rglob(f"{conv_id}.json"):
        return f
    return None


def generate_id() -> str:
    return uuid.uuid4().hex[:12]


def save(conv_id: str, title: str, messages: list[dict],
         archived: bool = False, last_prompt_tokens: int = 0) -> str:
    today = date.today()
    file_dir = DATA_DIR / str(today.year) / f"{today.month:02d}-{today.day:02d}"
    _ensure_dir(file_dir)
    file_path = file_dir / f"{conv_id}.json"
    now = datetime.utcnow().isoformat()

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
        _write_json(_conv_file(conv_id), data)


def _write_json(path: Path, data: dict):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load(conv_id: str) -> dict | None:
    f = _conv_file(conv_id)
    if not f:
        return None
    return json.loads(f.read_text(encoding="utf-8"))


def clear(conv_id: str) -> str | None:
    f = _conv_file(conv_id)
    if not f:
        return None
    data = json.loads(f.read_text(encoding="utf-8"))
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
    for year_dir in sorted(DATA_DIR.glob("*"), reverse=True):
        if not year_dir.is_dir():
            continue
        for date_dir in sorted(year_dir.iterdir(), reverse=True):
            if not date_dir.is_dir():
                continue
            for f in sorted(date_dir.glob("*.json"), reverse=True):
                try:
                    data = json.loads(f.read_text(encoding="utf-8"))
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
