"""
知识图谱 CRUD：实体/关系/事实增删改、软删除、恢复、合并、浏览。
从 core/graph.py 拆出。
"""
import json
from datetime import datetime, timezone

from .db import _connect, cleanup_expired


# ── 实体 CRUD ───────────────────────────────────────────


async def upsert_entity(name: str, etype: str, props: dict):
    db = await _connect()
    await db.execute(
        "INSERT INTO entities (name, type, properties) VALUES (?, ?, ?)"
        " ON CONFLICT(name) DO UPDATE SET type=excluded.type, properties=excluded.properties",
        (name, etype, json.dumps(props)),
    )
    await db.commit()


async def update_entity(name: str, new_type: str | None = None, new_props: dict | None = None) -> bool:
    """更新实体类型和/或属性。返回是否找到并更新。"""
    db = await _connect()
    cur = await db.execute("SELECT * FROM entities WHERE name=?", (name,))
    row = await cur.fetchone()
    if not row:
        return False
    etype = new_type if new_type is not None else row["type"]
    props = json.dumps(new_props) if new_props is not None else row["properties"]
    await db.execute(
        "UPDATE entities SET type=?, properties=? WHERE name=?",
        (etype, props, name),
    )
    await db.commit()
    return True


async def delete_entity(name: str) -> bool:
    """
    ⛔ 硬删除已禁用！请使用 soft_delete_entity（软删除，可恢复）替代。
    """
    raise RuntimeError(
        f"硬删除已禁用：delete_entity('{name}') 会级联删除关联事实。"
        "请使用 soft_delete_entity 进行软删除（可恢复）。"
    )


async def list_entity_keywords() -> list[dict]:
    """轻量级列出所有实体名称+类型（不含 facts/properties），用于浏览索引。"""
    db = await _connect()
    cur = await db.execute(
        "SELECT name, type, importance, pinned FROM entities WHERE deprecated_at IS NULL ORDER BY importance DESC, name"
    )
    return [
        {"name": r["name"], "type": r["type"], "importance": r["importance"], "pinned": bool(r["pinned"])}
        for r in await cur.fetchall()
    ]


# ── 关系 CRUD ───────────────────────────────────────────


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


# ── 事实 CRUD ───────────────────────────────────────────


async def create_fact(content: str, ftype: str, about_entities: list[str]):
    db = await _connect()
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


async def update_fact(fact_id: int, content: str | None = None, ftype: str | None = None, about_entities: list[str] | None = None) -> bool:
    """更新事实的内容/类型/关联实体。只更新传了值的字段。返回是否找到并更新。"""
    db = await _connect()
    cur = await db.execute("SELECT * FROM facts WHERE id=?", (fact_id,))
    row = await cur.fetchone()
    if not row:
        return False
    new_content = content if content is not None else row["content"]
    new_type = ftype if ftype is not None else row["type"]
    new_about = json.dumps(about_entities) if about_entities is not None else row["about_entities"]
    await db.execute(
        "UPDATE facts SET content=?, type=?, about_entities=? WHERE id=?",
        (new_content, new_type, new_about, fact_id),
    )
    await db.commit()
    return True


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


async def search_facts_by_keyword(keyword: str, limit: int = 500) -> list[dict]:
    """按关键词搜索事实内容（SQL LIKE），用于 fact_by_content 删除。"""
    db = await _connect()
    pattern = f"%{keyword}%"
    cur = await db.execute(
        "SELECT id, content, type, about_entities FROM facts"
        " WHERE deprecated_at IS NULL AND content LIKE ? LIMIT ?",
        (pattern, limit),
    )
    return [
        {"id": r["id"], "content": r["content"], "type": r["type"], "about_entities": json.loads(r["about_entities"])}
        for r in await cur.fetchall()
    ]


async def delete_fact(fact_id: int) -> bool:
    """
    ⛔ 硬删除已禁用！请使用 soft_delete_fact（软删除，可恢复）替代。
    """
    raise RuntimeError(
        f"硬删除已禁用：delete_fact({fact_id}) 不可恢复。"
        "请使用 soft_delete_fact 进行软删除（可恢复）。"
    )


# ── 对话索引 ────────────────────────────────────────────


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


# ── 重要度 & 固定 ──────────────────────────────────────


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


# ── 软删除 + 恢复 ──────────────────────────────────────


async def soft_delete_entity(name: str) -> bool:
    """软删除实体：标记 deprecated_at=now，硬删其所有关系。返回是否找到。"""
    db = await _connect()
    cur = await db.execute("SELECT name FROM entities WHERE name=? AND deprecated_at IS NULL", (name,))
    if not await cur.fetchone():
        return False
    now = datetime.now(timezone.utc).isoformat()
    await db.execute("UPDATE entities SET deprecated_at=? WHERE name=?", (now, name))
    await db.execute("DELETE FROM relations WHERE from_name=? OR to_name=?", (name, name))
    await db.execute("DELETE FROM conversation_mentions WHERE entity_name=?", (name,))
    await db.commit()
    await cleanup_expired()
    return True


async def soft_delete_fact(fact_id: int) -> bool:
    """软删除事实：标记 deprecated_at=now。返回是否找到。"""
    db = await _connect()
    cur = await db.execute("SELECT id FROM facts WHERE id=? AND deprecated_at IS NULL", (fact_id,))
    if not await cur.fetchone():
        return False
    now = datetime.now(timezone.utc).isoformat()
    await db.execute("UPDATE facts SET deprecated_at=? WHERE id=?", (now, fact_id))
    await db.commit()
    await cleanup_expired()
    return True


async def restore_entity(name: str) -> bool:
    """恢复软删除的实体：清除 deprecated_at 标记。"""
    db = await _connect()
    cur = await db.execute("SELECT name FROM entities WHERE name=? AND deprecated_at IS NOT NULL", (name,))
    if not await cur.fetchone():
        return False
    await db.execute("UPDATE entities SET deprecated_at=NULL WHERE name=?", (name,))
    await db.commit()
    return True


async def restore_fact(fact_id: int) -> bool:
    """恢复软删除的事实：清除 deprecated_at 标记。"""
    db = await _connect()
    cur = await db.execute("SELECT id FROM facts WHERE id=? AND deprecated_at IS NOT NULL", (fact_id,))
    if not await cur.fetchone():
        return False
    await db.execute("UPDATE facts SET deprecated_at=NULL WHERE id=?", (fact_id,))
    await db.commit()
    return True


async def list_deprecated() -> list[dict]:
    """列出所有已软删除待清理的实体和事实。"""
    db = await _connect()
    cur = await db.execute(
        "SELECT name, type, properties, importance, deprecated_at FROM entities WHERE deprecated_at IS NOT NULL ORDER BY deprecated_at DESC"
    )
    entities = [{"name": r["name"], "type": r["type"], "deprecated_at": r["deprecated_at"]} for r in await cur.fetchall()]
    cur = await db.execute(
        "SELECT id, content, type, about_entities, deprecated_at FROM facts WHERE deprecated_at IS NOT NULL ORDER BY deprecated_at DESC"
    )
    facts = [{"id": r["id"], "content": r["content"][:80], "type": r["type"], "deprecated_at": r["deprecated_at"]} for r in await cur.fetchall()]
    return {"entities": entities, "facts": facts}


# ── 合并 ────────────────────────────────────────────────


async def merge_entities(source: str, target: str) -> str:
    """将源实体合并到目标实体：迁移关系+事实+属性，然后软删除源实体。"""
    if source == target:
        return "源实体和目标实体相同，无需合并"

    db = await _connect()

    src = await (await db.execute(
        "SELECT * FROM entities WHERE name=? AND deprecated_at IS NULL", (source,)
    )).fetchone()
    if not src:
        return f"未找到源实体「{source}」"

    tgt = await (await db.execute(
        "SELECT * FROM entities WHERE name=? AND deprecated_at IS NULL", (target,)
    )).fetchone()
    if not tgt:
        return f"未找到目标实体「{target}」"

    stats = {"relations_out": 0, "relations_in": 0, "facts": 0, "props": 0}

    # 1. 迁移出边
    for edge in await (await db.execute(
        "SELECT from_type, to_type, to_name, rel_type, properties FROM relations WHERE from_name=?",
        (source,),
    )).fetchall():
        await db.execute(
            "INSERT OR IGNORE INTO relations (from_type, from_name, to_type, to_name, rel_type, properties)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (edge["from_type"], target, edge["to_type"], edge["to_name"], edge["rel_type"], edge["properties"]),
        )
        stats["relations_out"] += 1

    # 2. 迁移入边
    for edge in await (await db.execute(
        "SELECT from_type, from_name, to_type, rel_type, properties FROM relations WHERE to_name=?",
        (source,),
    )).fetchall():
        await db.execute(
            "INSERT OR IGNORE INTO relations (from_type, from_name, to_type, to_name, rel_type, properties)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (edge["from_type"], edge["from_name"], edge["to_type"], target, edge["rel_type"], edge["properties"]),
        )
        stats["relations_in"] += 1

    # 3. 清除 source 的旧关系
    await db.execute("DELETE FROM relations WHERE from_name=? OR to_name=?", (source, source))

    # 4. 迁移事实
    for row in await (await db.execute(
        "SELECT id, about_entities FROM facts WHERE deprecated_at IS NULL"
    )).fetchall():
        about = json.loads(row["about_entities"])
        if source in about:
            new_about = json.dumps(list(set(
                target if x == source else x for x in about
            )))
            await db.execute("UPDATE facts SET about_entities=? WHERE id=?", (new_about, row["id"]))
            stats["facts"] += 1

    # 5. 合并属性
    src_props = json.loads(src["properties"])
    tgt_props = json.loads(tgt["properties"])
    merged = {**tgt_props}
    for k, v in src_props.items():
        if k not in merged:
            merged[k] = v
            stats["props"] += 1
    if stats["props"] > 0:
        await db.execute("UPDATE entities SET properties=? WHERE name=?", (json.dumps(merged), target))

    # 6. 重要度和固定状态取高值
    if (src["importance"] or 1) > (tgt["importance"] or 1):
        await db.execute("UPDATE entities SET importance=? WHERE name=?", (src["importance"], target))
    if src["pinned"]:
        await db.execute("UPDATE entities SET pinned=1 WHERE name=?", (target,))

    await db.commit()

    # 7. 软删除源实体
    await soft_delete_entity(source)

    return (
        f"已合并实体「{source}」→「{target}」："
        f"迁移出边 {stats['relations_out']} 条、入边 {stats['relations_in']} 条、"
        f"事实 {stats['facts']} 条、属性 {stats['props']} 项。"
    )


# ── 邻域查询（BFS）──────────────────────────────────────


async def get_entity_neighborhood(entity_name: str, depth: int = 2) -> dict:
    """BFS 查询指定实体的 N 层关联子图。

    返回: {
        "center": {"name": ..., "type": ..., "importance": ..., "facts": [...]},
        "layers": [[edge_dict, ...], ...],
        "entities": {name: {type, importance, pinned, facts_count}},
    }
    """
    db = await _connect()
    depth = max(1, min(depth, 3))

    cur = await db.execute(
        "SELECT name, type, importance, pinned FROM entities WHERE name=? AND deprecated_at IS NULL",
        (entity_name,),
    )
    center_row = await cur.fetchone()
    if not center_row:
        return {"center": None, "error": f"未找到实体「{entity_name}」"}

    facts_cur = await db.execute(
        "SELECT content, type FROM facts WHERE deprecated_at IS NULL AND about_entities LIKE ?",
        (f'%"{entity_name}"%',),
    )
    center_facts = [{"content": f["content"], "type": f["type"]} for f in await facts_cur.fetchall()]

    center = {
        "name": center_row["name"],
        "type": center_row["type"],
        "importance": center_row["importance"],
        "pinned": bool(center_row["pinned"]),
        "facts": center_facts,
    }

    # BFS 展开
    visited = {entity_name}
    current_level = [entity_name]
    all_layers = []
    all_entity_names = {entity_name}

    for _ in range(depth):
        if not current_level:
            break
        placeholders = ",".join("?" for _ in current_level)
        cur = await db.execute(f"""
            SELECT from_type, from_name, rel_type, to_type, to_name
            FROM relations
            WHERE from_name IN ({placeholders}) OR to_name IN ({placeholders})
        """, current_level + current_level)

        next_level_set: set[str] = set()
        layer_edges: list[dict] = []
        for row in await cur.fetchall():
            edge = {
                "from_type": row["from_type"],
                "from_name": row["from_name"],
                "rel_type": row["rel_type"],
                "to_type": row["to_type"],
                "to_name": row["to_name"],
            }
            layer_edges.append(edge)
            for name in (row["from_name"], row["to_name"]):
                if name not in visited:
                    next_level_set.add(name)

        visited.update(next_level_set)
        all_entity_names.update(next_level_set)
        current_level = list(next_level_set)
        all_layers.append(layer_edges)

    # 批量查所有涉及实体的摘要
    entities_summary: dict[str, dict] = {}
    if all_entity_names:
        placeholders = ",".join("?" for _ in all_entity_names)
        cur = await db.execute(
            f"SELECT name, type, importance, pinned FROM entities WHERE name IN ({placeholders}) AND deprecated_at IS NULL",
            list(all_entity_names),
        )
        for r in await cur.fetchall():
            entities_summary[r["name"]] = {
                "type": r["type"],
                "importance": r["importance"],
                "pinned": bool(r["pinned"]),
            }

    # 批量查 facts 数量
    for ename in all_entity_names:
        cnt_cur = await db.execute(
            "SELECT COUNT(*) as cnt FROM facts WHERE deprecated_at IS NULL AND about_entities LIKE ?",
            (f'%"{ename}"%',),
        )
        cnt_row = await cnt_cur.fetchone()
        if ename in entities_summary:
            entities_summary[ename]["facts_count"] = cnt_row["cnt"] if cnt_row else 0

    return {
        "center": center,
        "layers": all_layers,
        "entities": entities_summary,
    }
