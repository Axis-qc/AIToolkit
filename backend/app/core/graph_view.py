"""
前端图谱可视化专用查询：全量图、根节点、子节点、孤岛、事实。
从 core/graph.py 拆出。
"""
import json

from .db import _connect


async def get_all_graph() -> dict:
    db = await _connect()

    cur = await db.execute("SELECT name, type, properties, importance, pinned FROM entities WHERE deprecated_at IS NULL")
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
        "SELECT from_type, from_name, to_type, to_name, rel_type FROM relations WHERE deprecated_at IS NULL"
    )
    edges = []
    for row in await cur.fetchall():
        edges.append({
            "source": f"{row['from_type']}|{row['from_name']}",
            "target": f"{row['to_type']}|{row['to_name']}",
            "rel_type": row["rel_type"],
        })

    cur = await db.execute(
        "SELECT content, type, about_entities FROM facts WHERE deprecated_at IS NULL ORDER BY ts DESC LIMIT 200"
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
    """返回根节点：is_root=1 的实体"""
    db = await _connect()
    cur = await db.execute(
        "SELECT name, type, importance, pinned FROM entities WHERE is_root=1 AND deprecated_at IS NULL"
    )
    nodes = []
    for row in await cur.fetchall():
        nodes.append({
            "id": f"{row['type']}|{row['name']}",
            "name": row["name"],
            "type": row["type"],
            "importance": row["importance"],
            "pinned": bool(row["pinned"]),
        })
    return {"nodes": nodes, "edges": []}


async def get_children(entity_type: str, entity_name: str) -> dict:
    """返回指定实体的直接子节点和关系边（含入边和出边）"""
    db = await _connect()
    child_keys: dict[str, tuple[str, str]] = {}
    edges: list[dict] = []

    # 1) 出边
    cur = await db.execute(
        "SELECT to_type, to_name, rel_type FROM relations WHERE from_type=? AND from_name=? AND deprecated_at IS NULL",
        (entity_type, entity_name),
    )
    for r in await cur.fetchall():
        key = f"{r['to_type']}|{r['to_name']}"
        if key not in child_keys:
            child_keys[key] = (r['to_type'], r['to_name'])
        edges.append({
            "source": f"{entity_type}|{entity_name}",
            "target": key,
            "rel_type": r["rel_type"],
        })

    # 2) 入边
    cur = await db.execute(
        "SELECT from_type, from_name, rel_type FROM relations WHERE to_type=? AND to_name=? AND deprecated_at IS NULL",
        (entity_type, entity_name),
    )
    for r in await cur.fetchall():
        key = f"{r['from_type']}|{r['from_name']}"
        if key not in child_keys:
            child_keys[key] = (r['from_type'], r['from_name'])
        edges.append({
            "source": f"{entity_type}|{entity_name}",
            "target": key,
            "rel_type": r["rel_type"],
        })

    if not child_keys:
        return {"nodes": [], "edges": [], "has_children": {}}

    # 3) 加载子节点信息
    nodes = []
    for key, (ttype, tname) in child_keys.items():
        cur = await db.execute(
            "SELECT name, type, properties, importance, pinned FROM entities WHERE name=? AND type=? AND deprecated_at IS NULL",
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

    # 4) 批量查询子节点是否有下级边
    if child_keys:
        placeholders = ",".join("(?,?)" for _ in child_keys)
        flat = [x for key in child_keys.values() for x in key]
        cur = await db.execute(f"""
            SELECT from_type, from_name, COUNT(*) AS cnt
            FROM relations
            WHERE deprecated_at IS NULL AND (from_type, from_name) IN ({placeholders})
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

    cur = await db.execute(
        "SELECT name, type, importance, pinned FROM entities "
        "WHERE deprecated_at IS NULL AND is_root=0"
    )
    all_entities = await cur.fetchall()

    cur = await db.execute(
        "SELECT DISTINCT from_type, from_name FROM relations WHERE deprecated_at IS NULL "
        "UNION SELECT DISTINCT to_type, to_name FROM relations WHERE deprecated_at IS NULL"
    )
    connected = set(f"{r['from_type']}|{r['from_name']}" for r in await cur.fetchall())

    nodes = []
    for row in all_entities:
        eid = f"{row['type']}|{row['name']}"
        if eid not in connected:
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
        "SELECT id, content, type, about_entities, ts FROM facts WHERE deprecated_at IS NULL AND EXISTS ("
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
