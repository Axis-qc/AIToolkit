"""
前端可视化专用查询 —— 为图谱可视化页面提供数据。
仅查询，不修改数据。调 core/db 直读 SQLite。
"""
import json

from .db import _connect


async def get_roots() -> dict:
    """获取根节点列表——图谱浏览的起始节点。
    只返回 is_root=1 的实体，由数据库标记决定。
    返回: { "nodes": [...], "edges": [...] }
    """
    db = await _connect()
    cur = await db.execute(
        "SELECT name, type, importance, pinned FROM entities "
        "WHERE deprecated_at IS NULL AND is_root=1 ORDER BY importance DESC"
    )
    rows = await cur.fetchall()
    if not rows:
        return {"nodes": []}

    nodes = []
    for r in rows:
        node_id = f"{r['type']}|{r['name']}"
        nodes.append({
            "id": node_id,
            "name": r["name"],
            "type": r["type"],
            "importance": r["importance"],
            "pinned": bool(r["pinned"]),
        })

    return {"nodes": nodes}


async def get_children(entity_type: str, entity_name: str) -> dict:
    """获取指定实体的直接子节点（基于 relation_index 出边）。
    返回: { "nodes": [...], "edges": [...], "has_children": { "type|name": bool } }
    """
    db = await _connect()
    # 出边：entity_name → target
    cur = await db.execute("""
        SELECT ri.target_name, ri.rel_type,
               e.type AS target_type, e.importance AS target_imp, e.pinned AS target_pinned
        FROM relation_index ri
        JOIN entities e ON e.name = ri.target_name AND e.deprecated_at IS NULL
        WHERE ri.entity_name = ? AND ri.deprecated_at IS NULL
    """, (entity_name,))
    rows = await cur.fetchall()

    nodes = []
    edges = []
    child_entity_ids = []
    for r in rows:
        child_id = f"{r['target_type']}|{r['target_name']}"
        nodes.append({
            "id": child_id,
            "name": r["target_name"],
            "type": r["target_type"],
            "importance": r["target_imp"],
            "pinned": bool(r["target_pinned"]),
        })
        edges.append({
            "source": f"{entity_type}|{entity_name}",
            "target": child_id,
            "rel_type": r["rel_type"],
        })
        child_entity_ids.append(r["target_name"])

    # 批量查询每个子节点是否有自己的子节点（用于前端展开箭头）
    has_children: dict[str, bool] = {}
    if child_entity_ids:
        placeholders = ",".join("?" for _ in child_entity_ids)
        cur = await db.execute(f"""
            SELECT ri.entity_name AS parent, COUNT(*) > 0 AS has
            FROM relation_index ri
            JOIN entities e ON e.name = ri.target_name AND e.deprecated_at IS NULL
            WHERE ri.entity_name IN ({placeholders}) AND ri.deprecated_at IS NULL
            GROUP BY ri.entity_name
        """, child_entity_ids)
        for r in await cur.fetchall():
            # 用节点在 nodes 中的 id 来标记
            for n in nodes:
                if n["name"] == r["parent"]:
                    has_children[n["id"]] = bool(r["has"])
                    break

    return {"nodes": nodes, "edges": edges, "has_children": has_children}


async def get_facts(entity_type: str, entity_name: str) -> dict:
    """获取指定实体的关联事实 + 实体自身描述。
    返回: { "facts": [...], "entity": { "content": "...", "type": "...", "name": "..." } }
    """
    db = await _connect()

    # 实体自身信息
    entity = {"name": entity_name, "type": entity_type, "content": ""}
    cur = await db.execute(
        "SELECT content, importance, pinned FROM entities WHERE name=? AND deprecated_at IS NULL",
        (entity_name,),
    )
    row = await cur.fetchone()
    if row:
        entity["content"] = row["content"] or ""
        entity["importance"] = row["importance"]
        entity["pinned"] = bool(row["pinned"])

    # 关联事实
    cur = await db.execute(
        "SELECT id, content, type, about_entities, ts FROM facts "
        "WHERE deprecated_at IS NULL AND about_entities LIKE ? ORDER BY ts DESC",
        (f'%"{entity_name}"%',),
    )
    facts = []
    for r in await cur.fetchall():
        facts.append({
            "id": r["id"],
            "content": r["content"],
            "type": r["type"],
            "about_entities": json.loads(r["about_entities"]),
            "ts": r["ts"],
        })
    return {"facts": facts, "entity": entity}


async def get_orphans() -> dict:
    """获取孤立节点——没有任何关系的实体。
    返回: { "nodes": [...] }
    """
    db = await _connect()
    cur = await db.execute("""
        SELECT e.name, e.type, e.importance, e.pinned
        FROM entities e
        WHERE e.deprecated_at IS NULL AND e.is_root=0
          AND NOT EXISTS (
              SELECT 1 FROM relation_index ri
              WHERE ri.deprecated_at IS NULL
                AND (ri.entity_name = e.name OR ri.target_name = e.name)
          )
        ORDER BY e.importance DESC, e.name
    """)
    nodes = []
    for r in await cur.fetchall():
        nodes.append({
            "id": f"{r['type']}|{r['name']}",
            "name": r["name"],
            "type": r["type"],
            "importance": r["importance"],
            "pinned": bool(r["pinned"]),
        })
    return {"nodes": nodes}


async def get_full_graph() -> dict:
    """获取全量图谱数据（所有活跃节点 + 边 + 事实数量）。
    返回: { "nodes": [...], "edges": [...], "facts": [...] }
    """
    db = await _connect()

    # 所有活跃实体
    cur = await db.execute(
        "SELECT name, type, importance, pinned FROM entities WHERE deprecated_at IS NULL ORDER BY importance DESC"
    )
    nodes = []
    entity_names = []
    for r in await cur.fetchall():
        nodes.append({
            "id": f"{r['type']}|{r['name']}",
            "name": r["name"],
            "type": r["type"],
            "importance": r["importance"],
            "pinned": bool(r["pinned"]),
        })
        entity_names.append(r["name"])

    # 所有活跃关系
    cur = await db.execute("""
        SELECT ri.entity_name, ri.target_name, ri.rel_type,
               e1.type AS src_type, e2.type AS tgt_type
        FROM relation_index ri
        JOIN entities e1 ON e1.name = ri.entity_name AND e1.deprecated_at IS NULL
        JOIN entities e2 ON e2.name = ri.target_name AND e2.deprecated_at IS NULL
        WHERE ri.deprecated_at IS NULL
    """)
    edges = []
    for r in await cur.fetchall():
        edges.append({
            "source": f"{r['src_type']}|{r['entity_name']}",
            "target": f"{r['tgt_type']}|{r['target_name']}",
            "rel_type": r["rel_type"],
        })

    # 所有活跃事实（限制条数，避免过大）
    cur = await db.execute(
        "SELECT id, content, type, about_entities, ts FROM facts "
        "WHERE deprecated_at IS NULL ORDER BY ts DESC LIMIT 500"
    )
    facts = []
    for r in await cur.fetchall():
        facts.append({
            "id": r["id"],
            "content": r["content"],
            "type": r["type"],
            "about_entities": json.loads(r["about_entities"]),
            "ts": r["ts"],
        })

    return {"nodes": nodes, "edges": edges, "facts": facts}
