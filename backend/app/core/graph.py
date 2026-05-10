import aiosqlite
import json
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "graph.db"
_db = None


def _ensure_dir():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)


async def _connect():
    global _db
    if _db is not None:
        return _db
    _ensure_dir()
    _db = await aiosqlite.connect(str(DB_PATH))
    await _db.execute("PRAGMA journal_mode=WAL")
    await _db.execute("PRAGMA synchronous=NORMAL")
    _db.row_factory = aiosqlite.Row
    return _db


async def init_db():
    db = await _connect()
    await db.executescript("""
        CREATE TABLE IF NOT EXISTS entities (
            name TEXT PRIMARY KEY,
            type TEXT NOT NULL,
            properties TEXT DEFAULT '{}'
        );
        CREATE TABLE IF NOT EXISTS relations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            from_type TEXT NOT NULL,
            from_name TEXT NOT NULL,
            to_type TEXT NOT NULL DEFAULT '',
            to_name TEXT NOT NULL,
            rel_type TEXT NOT NULL,
            properties TEXT DEFAULT '{}',
            UNIQUE(from_type, from_name, to_name, rel_type)
        );
        CREATE TABLE IF NOT EXISTS facts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content TEXT NOT NULL,
            type TEXT NOT NULL DEFAULT 'fact',
            ts TEXT NOT NULL,
            about_entities TEXT DEFAULT '[]'
        );
        CREATE TABLE IF NOT EXISTS conversations (
            id TEXT PRIMARY KEY,
            file_path TEXT NOT NULL,
            title TEXT NOT NULL,
            created_at TEXT NOT NULL,
            archived INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS conversation_mentions (
            conv_id TEXT,
            entity_name TEXT,
            PRIMARY KEY (conv_id, entity_name)
        );
    """)
    try:
        await db.execute("ALTER TABLE relations ADD COLUMN to_type TEXT NOT NULL DEFAULT ''")
    except Exception:
        pass
    await db.commit()


async def close():
    global _db
    if _db is not None:
        await _db.close()
        _db = None


async def search_entities(query: str, top_k: int) -> list[dict]:
    db = await _connect()
    keywords = query.lower().split()

    cursor = await db.execute("SELECT name, type, properties FROM entities")
    rows = await cursor.fetchall()

    scored = []
    for row in rows:
        name_lower = row["name"].lower()
        type_lower = row["type"].lower()
        proteins = json.loads(row["properties"])
        props_text = json.dumps(proteins, ensure_ascii=False).lower()

        score = 0
        for kw in keywords:
            if kw in name_lower:
                score += 10
            elif name_lower.startswith(kw):
                score += 5
            if kw in type_lower or kw in props_text:
                score += 1

        cur = await db.execute(
            "SELECT content FROM facts WHERE about_entities LIKE ?",
            (f'%"{row["name"]}"%',),
        )
        for f in await cur.fetchall():
            content_lower = f["content"].lower()
            if any(kw in content_lower for kw in keywords):
                score += 2

        if score > 0:
            scored.append((row["name"], row["type"], row["properties"], score))

    scored.sort(key=lambda x: x[3], reverse=True)

    results = []
    for name, etype, props_json, score in scored[:top_k]:
        cur = await db.execute(
            "SELECT rel_type FROM relations"
            " WHERE from_type='User' AND from_name='default' AND to_name=?",
            (name,),
        )
        user_rel = await cur.fetchone()
        user_relation = user_rel["rel_type"] if user_rel else None

        cur = await db.execute(
            "SELECT content, type FROM facts WHERE about_entities LIKE ?",
            (f'%"{name}"%',),
        )
        entity_facts = []
        for f in await cur.fetchall():
            entity_facts.append({"content": f["content"], "type": f["type"]})
            if len(entity_facts) >= 3:
                break

        cur = await db.execute(
            "SELECT to_name FROM relations WHERE from_name=? AND from_type!='User'"
            " UNION SELECT from_name FROM relations WHERE to_name=? AND from_type!='User'"
            " LIMIT 5",
            (name, name),
        )
        related = [r[0] for r in await cur.fetchall()]

        cur = await db.execute(
            "SELECT c.id, c.title FROM conversations c"
            " JOIN conversation_mentions cm ON c.id = cm.conv_id"
            " WHERE cm.entity_name=? LIMIT 3",
            (name,),
        )
        conversations = [
            {"id": r["id"], "title": r["title"]} for r in await cur.fetchall()
        ]

        results.append({
            "entity": name,
            "type": etype,
            "user_relation": user_relation,
            "facts": entity_facts,
            "related": related,
            "conversations": conversations,
        })

    return results


async def upsert_entity(name: str, etype: str, props: dict):
    db = await _connect()
    await db.execute(
        "INSERT OR REPLACE INTO entities (name, type, properties) VALUES (?, ?, ?)",
        (name, etype, json.dumps(props)),
    )
    await db.commit()


async def upsert_relation(
    from_type: str, from_name: str, to_type: str, to_name: str, rel_type: str, props: dict
):
    db = await _connect()
    await db.execute(
        "INSERT OR REPLACE INTO relations (from_type, from_name, to_type, to_name, rel_type, properties)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (from_type, from_name, to_type, to_name, rel_type, json.dumps(props)),
    )
    await db.commit()


async def create_fact(content: str, ftype: str, about_entities: list[str]):
    db = await _connect()
    ts = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "INSERT INTO facts (content, type, about_entities, ts) VALUES (?, ?, ?, ?)",
        (content, ftype, json.dumps(about_entities), ts),
    )
    await db.commit()


async def index_conversation(conv_id: str, file_path: str, title: str):
    db = await _connect()
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "INSERT OR REPLACE INTO conversations (id, file_path, title, created_at, archived)"
        " VALUES (?, ?, ?, ?, 0)",
        (conv_id, file_path, title, now),
    )
    await db.commit()


async def mark_archived(conv_id: str):
    db = await _connect()
    await db.execute("UPDATE conversations SET archived=1 WHERE id=?", (conv_id,))
    await db.commit()


async def delete_conversation(conv_id: str):
    db = await _connect()
    await db.execute("DELETE FROM conversations WHERE id=?", (conv_id,))
    await db.execute("DELETE FROM conversation_mentions WHERE conv_id=?", (conv_id,))
    await db.commit()


async def add_mention(conv_id: str, entity_name: str):
    db = await _connect()
    await db.execute(
        "INSERT OR IGNORE INTO conversation_mentions (conv_id, entity_name) VALUES (?, ?)",
        (conv_id, entity_name),
    )
    await db.commit()


async def get_all_graph() -> dict:
    db = await _connect()

    cur = await db.execute("SELECT name, type, properties FROM entities")
    nodes = []
    for row in await cur.fetchall():
        nodes.append({
            "id": f"{row['type']}|{row['name']}",
            "name": row["name"],
            "type": row["type"],
        })

    cur = await db.execute(
        "SELECT from_type, from_name, to_type, to_name, rel_type FROM relations"
    )
    edges = []
    for row in await cur.fetchall():
        edges.append({
            "source": f"{row['from_type']}|{row['from_name']}",
            "target": f"{row['to_type']}|{row['to_name']}",
            "rel_type": row["rel_type"],
        })

    cur = await db.execute(
        "SELECT content, type, about_entities FROM facts ORDER BY ts DESC LIMIT 200"
    )
    facts = []
    for row in await cur.fetchall():
        facts.append({
            "content": row["content"],
            "type": row["type"],
            "about_entities": json.loads(row["about_entities"]),
        })

    return {"nodes": nodes, "edges": edges, "facts": facts}
