import aiosqlite
import asyncio
import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from . import config_loader

DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "graph.db"
_db = None
_db_lock = asyncio.Lock()

_IDENT_RE = re.compile(r"[A-Za-z][A-Za-z0-9_.:-]{1,}")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]+")
_CJK_STOPWORDS = {
    "一个", "一些", "一下", "不是", "不用", "不能", "什么", "他们", "以及", "但是",
    "你的", "里面", "关于", "其实", "刚才", "原来", "可以", "因为", "如果", "应该",
    "当前", "怎么", "我们", "所以", "换成", "是否", "现在", "然后", "看看", "这个",
    "这些", "这样", "那个", "那些", "需要", "还是", "这里",
}
_CJK_STOP_CHARS = set("的一是在了和就都而及与或也很到把被给用")
_MIN_SEARCH_SCORE = 4


def _extract_search_keywords(query: str) -> dict[str, int]:
    """Extract conservative keywords for graph search.

    Weight 5: code-like identifiers, file names, event IDs.
    Weight 2: Chinese terms with at least 3 chars.
    Weight 1: short Chinese terms, only used as weak signals.
    """
    keywords: dict[str, int] = {}

    def add(raw: str, weight: int):
        kw = raw.strip().lower()
        if len(kw) < 2 or kw in _CJK_STOPWORDS:
            return
        keywords[kw] = max(keywords.get(kw, 0), weight)

    for ident in _IDENT_RE.findall(query):
        add(ident, 5)

    for chunk in _CJK_RE.findall(query):
        if chunk in _CJK_STOPWORDS:
            continue
        if 3 <= len(chunk) <= 8:
            add(chunk, 2)
        if len(chunk) >= 3:
            for n in (3, 4, 5, 6):
                if len(chunk) < n:
                    continue
                for i in range(len(chunk) - n + 1):
                    gram = chunk[i:i + n]
                    if gram in _CJK_STOPWORDS or any(ch in _CJK_STOP_CHARS for ch in gram):
                        continue
                    add(gram, 2)
        if len(chunk) >= 2:
            for i in range(len(chunk) - 1):
                gram = chunk[i:i + 2]
                if gram in _CJK_STOPWORDS or any(ch in _CJK_STOP_CHARS for ch in gram):
                    continue
                add(gram, 1)

    return keywords


def _ensure_dir():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)


async def _connect():
    global _db
    if _db is not None:
        return _db
    async with _db_lock:
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
    try:
        await db.execute("ALTER TABLE entities ADD COLUMN importance INTEGER NOT NULL DEFAULT 1")
    except Exception:
        pass
    try:
        await db.execute("ALTER TABLE entities ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0")
    except Exception:
        pass
    # 清理已有重复事实，然后创建唯一索引
    await db.execute("""
        DELETE FROM facts WHERE id NOT IN (
            SELECT MIN(id) FROM facts GROUP BY content, type, about_entities
        )
    """)
    try:
        await db.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_facts_unique
            ON facts(content, type, about_entities)
        """)
    except Exception:
        pass
    # 自动创建/修复根实体
    for c in config_loader.get_centers():
        if c.get("is_root"):
            cur = await db.execute("SELECT type FROM entities WHERE name=?", (c["name"],))
            existing = await cur.fetchone()
            if existing and existing["type"] != c["type"]:
                await db.execute("UPDATE entities SET type=?, importance=10, pinned=1 WHERE name=?", (c["type"], c["name"]))
            else:
                await db.execute(
                    "INSERT OR IGNORE INTO entities (name, type, properties, importance, pinned)"
                    " VALUES (?, ?, '{}', 10, 1)",
                    (c["name"], c["type"]),
                )

    # 自动创建根实体间关系
    for rel in config_loader.get_root_relations():
        from_type, from_name = rel["from"]
        to_type, to_name = rel["to"]
        rel_type = rel["rel_type"]
        await db.execute(
            "INSERT OR IGNORE INTO relations (from_type, from_name, to_type, to_name, rel_type, properties)"
            " VALUES (?, ?, ?, ?, ?, '{}')",
            (from_type, from_name, to_type, to_name, rel_type),
        )

    # 自动创建分类实体及与根中心的「包含」关系
    for cat in config_loader.get_all_categories():
        cat_name = cat["entity_name"]
        await db.execute(
            "INSERT OR IGNORE INTO entities (name, type, properties, importance, pinned)"
            " VALUES (?, 'category', '{}', 10, 1)",
            (cat_name,),
        )
        await db.execute(
            "INSERT OR IGNORE INTO relations (from_type, from_name, to_type, to_name, rel_type, properties)"
            " VALUES (?, ?, 'category', ?, '包含', '{}')",
            (cat["center_type"], cat["center_name"], cat_name),
        )

    await db.commit()


async def close():
    global _db
    if _db is not None:
        await _db.close()
        _db = None


async def search_entities(query: str, top_k: int) -> list[dict]:
    db = await _connect()
    keywords = _extract_search_keywords(query)
    if not keywords:
        return []

    cursor = await db.execute("SELECT name, type, properties, importance, pinned FROM entities")
    rows = await cursor.fetchall()

    # 一次性拉取所有事实，避免 N+1 且用 JSON 解析精确匹配 about_entities
    facts_cur = await db.execute("SELECT content, type, about_entities FROM facts")
    all_facts = [(json.loads(f["about_entities"]), f["content"], f["type"]) for f in await facts_cur.fetchall()]

    scored = []
    for row in rows:
        name_lower = row["name"].lower()
        type_lower = row["type"].lower()
        proteins = json.loads(row["properties"])
        props_text = json.dumps(proteins, ensure_ascii=False).lower()

        score = 0
        for kw, weight in keywords.items():
            if kw == name_lower:
                score += 12
            elif kw in name_lower or (weight >= 2 and name_lower in kw):
                score += 10 if weight >= 5 else 6 if weight >= 2 else 3
            if kw == type_lower:
                score += 4
            elif kw in type_lower or kw in props_text:
                score += 2 if weight >= 2 else 1

        for about_entities, content, _ftype in all_facts:
            if row["name"] not in about_entities:
                continue
            content_lower = content.lower()
            for kw, weight in keywords.items():
                if kw in content_lower:
                    score += 4 if weight >= 5 else 2 if weight >= 2 else 1

        if score >= _MIN_SEARCH_SCORE:
            scored.append((row["name"], row["type"], row["properties"], row["importance"], row["pinned"], score))

    scored.sort(key=lambda x: x[5], reverse=True)

    if not scored:
        return []

    top_entities = [(name, etype) for name, etype, *_ in scored[:top_k]]

    placeholders = ','.join('(?,?)' for _ in top_entities)
    flat_params = [x for pair in top_entities for x in pair]
    cur = await db.execute(f"""
        SELECT to_type, to_name, from_type, from_name, rel_type
        FROM relations
        WHERE (to_type, to_name) IN ({placeholders})
    """, flat_params)
    center_map = defaultdict(list)
    for row in await cur.fetchall():
        center_map[(row['to_type'], row['to_name'])].append({
            "center_type": row['from_type'],
            "center_name": row['from_name'],
            "rel_type": row['rel_type'],
        })

    # 解析分类链：将 category 类型的 from 追溯到其父根中心
    center_ids = config_loader.get_center_ids()
    cat_center_map = config_loader.get_category_center_map()
    for key, rels in center_map.items():
        resolved = []
        for rel in rels:
            ckey = f"{rel['center_type']}|{rel['center_name']}"
            if ckey in center_ids:
                resolved.append(rel)
            elif rel["center_name"] in cat_center_map:
                cc = cat_center_map[rel["center_name"]]
                resolved.append({
                    "center_type": cc["center_type"],
                    "center_name": cc["center_name"],
                    "rel_type": rel["rel_type"],
                })
        center_map[key] = resolved if resolved else rels

    results = []
    for name, etype, props_json, importance, pinned, score in scored[:top_k]:
        # 从 all_facts 中筛选属于该实体的事实（精确匹配）
        entity_facts = []
        for about_entities, content, ftype in all_facts:
            if name in about_entities:
                entity_facts.append({"content": content, "type": ftype})
                if len(entity_facts) >= 3:
                    break

        cur = await db.execute(
            "SELECT to_name FROM relations WHERE from_name=?"
            " UNION SELECT from_name FROM relations WHERE to_name=?"
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
            "center_relations": center_map.get((etype, name), []),
            "facts": entity_facts,
            "related": related,
            "conversations": conversations,
            "importance": importance,
            "pinned": bool(pinned),
        })

    return results


async def upsert_entity(name: str, etype: str, props: dict):
    db = await _connect()
    await db.execute(
        "INSERT INTO entities (name, type, properties) VALUES (?, ?, ?)"
        " ON CONFLICT(name) DO UPDATE SET type=excluded.type, properties=excluded.properties",
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
    # 去重：内容+类型+关联实体完全相同时跳过
    about_json = json.dumps(about_entities)
    cur = await db.execute(
        "SELECT id FROM facts WHERE content=? AND type=? AND about_entities=?",
        (content, ftype, about_json),
    )
    if await cur.fetchone():
        return  # 已存在，跳过
    ts = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "INSERT INTO facts (content, type, about_entities, ts) VALUES (?, ?, ?, ?)",
        (content, ftype, about_json, ts),
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


async def delete_entity(name: str) -> bool:
    """删除实体，并级联删除其所有关联关系和事实。返回是否实际删除了内容。"""
    db = await _connect()
    deleted = False
    cur = await db.execute("SELECT COUNT(*) FROM entities WHERE name=?", (name,))
    if (await cur.fetchone())[0] > 0:
        await db.execute("DELETE FROM entities WHERE name=?", (name,))
        deleted = True
    await db.execute("DELETE FROM relations WHERE from_name=? OR to_name=?", (name, name))
    # 精确匹配 about_entities JSON 数组中的实体名
    facts_cur = await db.execute("SELECT id, about_entities FROM facts")
    for row in await facts_cur.fetchall():
        about = json.loads(row["about_entities"])
        if name in about:
            await db.execute("DELETE FROM facts WHERE id=?", (row["id"],))
            deleted = True
    await db.execute("DELETE FROM conversation_mentions WHERE entity_name=?", (name,))
    await db.commit()
    return deleted


async def delete_fact(fact_id: int) -> bool:
    """按 ID 删除单条事实。返回是否找到并删除。"""
    db = await _connect()
    cur = await db.execute("SELECT COUNT(*) FROM facts WHERE id=?", (fact_id,))
    if (await cur.fetchone())[0] == 0:
        return False
    await db.execute("DELETE FROM facts WHERE id=?", (fact_id,))
    await db.commit()
    return True


async def delete_relation(from_name: str, to_name: str, rel_type: str | None = None) -> int:
    """删除匹配的关系。如果指定 rel_type 则精确匹配，否则删除所有。返回删除行数。"""
    db = await _connect()
    if rel_type:
        cur = await db.execute(
            "DELETE FROM relations WHERE from_name=? AND to_name=? AND rel_type=?",
            (from_name, to_name, rel_type),
        )
    else:
        cur = await db.execute(
            "DELETE FROM relations WHERE from_name=? AND to_name=?",
            (from_name, to_name),
        )
    await db.commit()
    return cur.rowcount


async def get_fact_by_id(fact_id: int) -> dict | None:
    """按 ID 获取单条事实。"""
    db = await _connect()
    cur = await db.execute("SELECT * FROM facts WHERE id=?", (fact_id,))
    row = await cur.fetchone()
    if not row:
        return None
    return {
        "id": row["id"],
        "content": row["content"],
        "type": row["type"],
        "about_entities": json.loads(row["about_entities"]),
        "ts": row["ts"],
    }


async def list_all_facts(limit: int = 500) -> list[dict]:
    """列出所有事实（用于浏览删除）。"""
    db = await _connect()
    cur = await db.execute("SELECT * FROM facts ORDER BY ts DESC LIMIT ?", (limit,))
    rows = await cur.fetchall()
    return [
        {
            "id": r["id"],
            "content": r["content"],
            "type": r["type"],
            "about_entities": json.loads(r["about_entities"]),
            "ts": r["ts"],
        }
        for r in rows
    ]


async def list_all_entities() -> list[dict]:
    """列出所有实体。"""
    db = await _connect()
    cur = await db.execute("SELECT * FROM entities ORDER BY name")
    return [
        {"name": r["name"], "type": r["type"], "properties": json.loads(r["properties"]), "importance": r["importance"], "pinned": bool(r["pinned"])}
        for r in await cur.fetchall()
    ]


async def add_mention(conv_id: str, entity_name: str):
    db = await _connect()
    await db.execute(
        "INSERT OR IGNORE INTO conversation_mentions (conv_id, entity_name) VALUES (?, ?)",
        (conv_id, entity_name),
    )
    await db.commit()


async def bump_importance(entity_name: str, delta: int = 1):
    db = await _connect()
    await db.execute(
        "UPDATE entities SET importance = MIN(importance + ?, 10) WHERE name = ?",
        (delta, entity_name),
    )
    await db.commit()


async def set_pinned(entity_name: str, pinned: bool):
    db = await _connect()
    val = 1 if pinned else 0
    await db.execute(
        "UPDATE entities SET pinned = ?, importance = CASE WHEN ? THEN 10 ELSE importance END WHERE name = ?",
        (val, val, entity_name),
    )
    await db.commit()


async def set_importance(entity_name: str, importance: int):
    db = await _connect()
    await db.execute(
        "UPDATE entities SET importance = MAX(1, MIN(?, 10)) WHERE name = ?",
        (importance, entity_name),
    )
    await db.commit()


async def get_pinned_entities() -> list[dict]:
    """获取所有固定注入的实体及其关联信息"""
    db = await _connect()
    cur = await db.execute("SELECT name, type, properties, importance, pinned FROM entities WHERE pinned=1 ORDER BY importance DESC")
    rows = await cur.fetchall()

    if not rows:
        return []

    entity_keys = [(r["type"], r["name"]) for r in rows]
    placeholders = ','.join('(?,?)' for _ in entity_keys)
    flat_params = [x for pair in entity_keys for x in pair]
    cur = await db.execute(f"""
        SELECT to_type, to_name, from_type, from_name, rel_type
        FROM relations
        WHERE (to_type, to_name) IN ({placeholders})
    """, flat_params)
    center_map = defaultdict(list)
    for row in await cur.fetchall():
        center_map[(row['to_type'], row['to_name'])].append({
            "center_type": row['from_type'],
            "center_name": row['from_name'],
            "rel_type": row['rel_type'],
        })

    # 解析分类链：将 category 类型的 from 追溯到其父根中心
    center_ids = config_loader.get_center_ids()
    cat_center_map = config_loader.get_category_center_map()
    for key, rels in center_map.items():
        resolved = []
        for rel in rels:
            ckey = f"{rel['center_type']}|{rel['center_name']}"
            if ckey in center_ids:
                resolved.append(rel)
            elif rel["center_name"] in cat_center_map:
                cc = cat_center_map[rel["center_name"]]
                resolved.append({
                    "center_type": cc["center_type"],
                    "center_name": cc["center_name"],
                    "rel_type": rel["rel_type"],
                })
        center_map[key] = resolved if resolved else rels

    results = []
    # 一次性拉取所有事实
    facts_cur = await db.execute("SELECT content, type, about_entities FROM facts")
    all_facts = [(json.loads(f["about_entities"]), f["content"], f["type"]) for f in await facts_cur.fetchall()]

    for row in rows:
        name, etype, props_json, importance, pinned = row["name"], row["type"], row["properties"], row["importance"], row["pinned"]

        entity_facts = []
        for about_entities, content, ftype in all_facts:
            if name in about_entities:
                entity_facts.append({"content": content, "type": ftype})
                if len(entity_facts) >= 3:
                    break

        cur = await db.execute(
            "SELECT to_name FROM relations WHERE from_name=?"
            " UNION SELECT from_name FROM relations WHERE to_name=?"
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
        conversations = [{"id": r["id"], "title": r["title"]} for r in await cur.fetchall()]

        results.append({
            "entity": name,
            "type": etype,
            "center_relations": center_map.get((etype, name), []),
            "facts": entity_facts,
            "related": related,
            "conversations": conversations,
            "importance": importance,
            "pinned": bool(pinned),
        })

    return results


async def get_all_graph() -> dict:
    db = await _connect()

    cur = await db.execute("SELECT name, type, properties, importance, pinned FROM entities")
    nodes = []
    for row in await cur.fetchall():
        nodes.append({
            "id": f"{row['type']}|{row['name']}",
            "name": row["name"],
            "type": row["type"],
            "importance": row["importance"],
            "pinned": bool(row["pinned"]),
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


async def get_roots() -> dict:
    """返回根节点：优先 root_node_ids 配置，否则取无入边的实体"""
    db = await _connect()
    root_ids = config_loader.get_root_node_ids()

    if root_ids:
        nodes = []
        for rid in root_ids:
            parts = rid.split("|", 1)
            if len(parts) != 2:
                continue
            etype, ename = parts
            cur = await db.execute(
                "SELECT name, type, properties, importance, pinned FROM entities WHERE name=? AND type=?",
                (ename, etype),
            )
            row = await cur.fetchone()
            if row:
                nodes.append({
                    "id": rid,
                    "name": row["name"],
                    "type": row["type"],
                    "importance": row["importance"],
                    "pinned": bool(row["pinned"]),
                })
    else:
        cur = await db.execute("SELECT name, type, properties, importance, pinned FROM entities")
        all_entities = await cur.fetchall()
        cur = await db.execute("SELECT DISTINCT to_type, to_name FROM relations")
        has_incoming = set(f"{r['to_type']}|{r['to_name']}" for r in await cur.fetchall())
        nodes = []
        for row in all_entities:
            eid = f"{row['type']}|{row['name']}"
            if eid not in has_incoming:
                nodes.append({
                    "id": eid,
                    "name": row["name"],
                    "type": row["type"],
                    "importance": row["importance"],
                    "pinned": bool(row["pinned"]),
                })

    return {"nodes": nodes, "edges": []}


async def get_children(entity_type: str, entity_name: str) -> dict:
    """返回指定实体的直接子节点和关系边"""
    db = await _connect()

    cur = await db.execute(
        "SELECT to_type, to_name, rel_type FROM relations WHERE from_type=? AND from_name=?",
        (entity_type, entity_name),
    )
    relation_rows = await cur.fetchall()

    child_keys = {}
    for r in relation_rows:
        key = f"{r['to_type']}|{r['to_name']}"
        if key not in child_keys:
            child_keys[key] = (r['to_type'], r['to_name'])

    if not child_keys:
        return {"nodes": [], "edges": [], "has_children": {}}

    nodes = []
    for key, (ttype, tname) in child_keys.items():
        cur = await db.execute(
            "SELECT name, type, properties, importance, pinned FROM entities WHERE name=? AND type=?",
            (tname, ttype),
        )
        row = await cur.fetchone()
        if row:
            nodes.append({
                "id": key,
                "name": row["name"],
                "type": row["type"],
                "importance": row["importance"],
                "pinned": bool(row["pinned"]),
            })

    edges = [
        {
            "source": f"{entity_type}|{entity_name}",
            "target": f"{r['to_type']}|{r['to_name']}",
            "rel_type": r["rel_type"],
        }
        for r in relation_rows
    ]

    # 批量查询子节点是否有 outgoing 边（单 SQL 避免 N+1）
    if child_keys:
        placeholders = ",".join("(?,?)" for _ in child_keys)
        flat = [x for key in child_keys.values() for x in key]
        cur = await db.execute(f"""
            SELECT from_type, from_name, COUNT(*) AS cnt
            FROM relations
            WHERE (from_type, from_name) IN ({placeholders})
            GROUP BY from_type, from_name
        """, flat)
        has_outgoing = {f"{r['from_type']}|{r['from_name']}": r['cnt'] > 0 for r in await cur.fetchall()}
    else:
        has_outgoing = {}

    has_children = {key: has_outgoing.get(key, False) for key in child_keys}

    return {"nodes": nodes, "edges": edges, "has_children": has_children}


async def get_orphans() -> dict:
    """返回所有无边孤岛实体（排除根节点）"""
    db = await _connect()
    root_ids = config_loader.get_root_node_ids()

    cur = await db.execute("SELECT name, type, properties, importance, pinned FROM entities")
    all_entities = await cur.fetchall()

    cur = await db.execute("SELECT DISTINCT from_type, from_name FROM relations UNION SELECT DISTINCT to_type, to_name FROM relations")
    connected = set(f"{r['from_type']}|{r['from_name']}" for r in await cur.fetchall())

    nodes = []
    for row in all_entities:
        eid = f"{row['type']}|{row['name']}"
        if eid not in connected and eid not in root_ids:
            nodes.append({
                "id": eid,
                "name": row["name"],
                "type": row["type"],
                "importance": row["importance"],
                "pinned": bool(row["pinned"]),
            })

    return {"nodes": nodes}


async def get_facts(entity_type: str, entity_name: str) -> dict:
    """返回指定实体的记忆事实（使用 SQL json_each 避免全量拉取）"""
    db = await _connect()
    cur = await db.execute(
        "SELECT id, content, type, about_entities, ts FROM facts WHERE EXISTS ("
        "  SELECT 1 FROM json_each(about_entities) WHERE value = ?"
        ") ORDER BY ts DESC LIMIT 50",
        (entity_name,),
    )
    rows = await cur.fetchall()
    facts = [
        {
            "id": r["id"],
            "content": r["content"],
            "type": r["type"],
            "about_entities": json.loads(r["about_entities"]),
            "ts": r["ts"],
        }
        for r in rows
    ]
    return {"facts": facts}
