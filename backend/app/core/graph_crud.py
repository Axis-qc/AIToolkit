"""
知识图谱 CRUD：实体/关系/事实增删改、软删除、恢复、合并、浏览。
从 core/graph.py 拆出。
"""
import json

from .db import _connect, cleanup_expired, now_ts


# ── 实体 CRUD ───────────────────────────────────────────


async def upsert_entity(
    name: str, etype: str,
    content: str = "",
    relations: list | None = None,
    props: dict | None = None,
    is_root: bool = False,
):
    """创建或更新实体。支持 content/relations 新字段，自动维护关系索引。"""
    db = await _connect()
    now = now_ts()
    relations_json = json.dumps(relations or [], ensure_ascii=False)
    props_json = json.dumps(props or {}, ensure_ascii=False)

    # 检查是否存在——决定 created_at
    cur = await db.execute("SELECT name FROM entities WHERE name=?", (name,))
    exists = await cur.fetchone() is not None

    if exists:
        await db.execute(
            "UPDATE entities SET type=?, content=?, relations=?, properties=?, is_root=?, updated_at=? WHERE name=?",
            (etype, content, relations_json, props_json, int(is_root), now, name),
        )
    else:
        await db.execute(
            "INSERT INTO entities (name, type, content, relations, properties, is_root, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (name, etype, content, relations_json, props_json, int(is_root), now, now),
        )
    await db.commit()

    # 自动维护关系索引
    await refresh_relation_index(name)


async def update_entity(
    name: str,
    new_name: str | None = None,
    new_type: str | None = None,
    new_props: dict | None = None,
    new_content: str | None = None,
    new_relations: list | None = None,
    new_is_root: bool | None = None,
) -> bool:
    """更新实体字段。传 None 的字段保留原值。支持重命名（new_name）。
    重命名时自动更新 relation_index / facts.about_entities / conversation_mentions。
    返回是否找到并更新。"""
    db = await _connect()
    cur = await db.execute("SELECT * FROM entities WHERE name=?", (name,))
    row = await cur.fetchone()
    if not row:
        return False

    now = now_ts()
    effective_name = new_name if new_name is not None else name
    etype = new_type if new_type is not None else row["type"]
    content = new_content if new_content is not None else row["content"]
    props = json.dumps(new_props) if new_props is not None else row["properties"]
    relations_json = (
        json.dumps(new_relations, ensure_ascii=False)
        if new_relations is not None
        else row["relations"]
    )

    # ── 重命名：更新主键 + 所有关联引用 ──
    if new_name is not None and new_name != name:
        # 目标名称已被占用则报错
        check = await (await db.execute(
            "SELECT name FROM entities WHERE name=? AND deprecated_at IS NULL", (new_name,)
        )).fetchone()
        if check:
            raise ValueError(f"目标名称「{new_name}」已被其他实体占用")

        # 1. 主表改名（INSERT 新行 + DELETE 旧行，因为 PRIMARY KEY 不可 UPDATE）
        rename_is_root = int(new_is_root) if new_is_root is not None else None
        if rename_is_root is not None:
            await db.execute(
                "INSERT INTO entities (name, type, content, relations, properties, "
                "importance, pinned, is_root, created_at, updated_at, deprecated_at) "
                "SELECT ?, type, content, relations, properties, "
                "importance, pinned, ?, created_at, ?, deprecated_at "
                "FROM entities WHERE name=?",
                (new_name, rename_is_root, now, name),
            )
        else:
            await db.execute(
                "INSERT INTO entities (name, type, content, relations, properties, "
                "importance, pinned, is_root, created_at, updated_at, deprecated_at) "
                "SELECT ?, type, content, relations, properties, "
                "importance, pinned, is_root, created_at, ?, deprecated_at "
                "FROM entities WHERE name=?",
                (new_name, now, name),
            )
        await db.execute("DELETE FROM entities WHERE name=?", (name,))

        # 2. relation_index：出边 entity_name
        await db.execute(
            "UPDATE relation_index SET entity_name=? WHERE entity_name=?",
            (new_name, name),
        )
        # 3. relation_index：入边 target_name
        await db.execute(
            "UPDATE relation_index SET target_name=? WHERE target_name=?",
            (new_name, name),
        )
        # 4. facts.about_entities JSON 替换
        cur_facts = await db.execute(
            "SELECT id, about_entities FROM facts WHERE deprecated_at IS NULL"
        )
        for frow in await cur_facts.fetchall():
            about = json.loads(frow["about_entities"])
            if name in about:
                new_about = json.dumps(list(set(
                    new_name if x == name else x for x in about
                )), ensure_ascii=False)
                await db.execute(
                    "UPDATE facts SET about_entities=? WHERE id=?",
                    (new_about, frow["id"]),
                )
        # 5. conversation_mentions
        await db.execute(
            "UPDATE conversation_mentions SET entity_name=? WHERE entity_name=?",
            (new_name, name),
        )
        await db.commit()

        # 改名后索引重建用新名称
        await refresh_relation_index(new_name)
        return True

    # ── 普通更新（不改名）──
    is_root = int(new_is_root) if new_is_root is not None else row["is_root"]
    await db.execute(
        "UPDATE entities SET type=?, content=?, relations=?, properties=?, is_root=?, updated_at=? WHERE name=?",
        (etype, content, relations_json, props, is_root, now, name),
    )
    await db.commit()

    # 如果 relations 或 is_root 变了，刷新索引
    if new_relations is not None or new_is_root is not None:
        await refresh_relation_index(name)
    return True


async def delete_entity(name: str) -> bool:
    """
    硬删除已禁用！请使用 soft_delete_entity（软删除，可恢复）替代。
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


async def list_entity_types_summary() -> list[dict]:
    """按类型汇总实体数量，用于类型级概览。"""
    db = await _connect()
    cur = await db.execute(
        "SELECT type, COUNT(*) as cnt FROM entities WHERE deprecated_at IS NULL GROUP BY type ORDER BY cnt DESC"
    )
    return [{"type": r["type"], "count": r["cnt"]} for r in await cur.fetchall()]


async def list_entities_by_type(entity_type: str, limit: int = 50) -> list[dict]:
    """按类型列出实体+关系邻居数。"""
    db = await _connect()
    cur = await db.execute(
        """
        SELECT e.name, e.type, e.importance, e.pinned,
               COUNT(DISTINCT ri.entity_name || ri.target_name) AS child_count
        FROM entities e
        LEFT JOIN relation_index ri
            ON ri.deprecated_at IS NULL
            AND (ri.entity_name = e.name OR ri.target_name = e.name)
        WHERE e.deprecated_at IS NULL AND e.type = ?
        GROUP BY e.name
        ORDER BY e.importance DESC, e.name
        LIMIT ?
        """,
        (entity_type, limit),
    )
    return [
        {
            "name": r["name"],
            "type": r["type"],
            "importance": r["importance"],
            "pinned": bool(r["pinned"]),
            "child_count": r["child_count"],
        }
        for r in await cur.fetchall()
    ]


# ── 批量读取 ────────────────────────────────────────────


def _row_to_detail(row) -> dict:
    """实体行 → 完整字典（含 content 与时间戳）。"""
    return {
        "name": row["name"],
        "type": row["type"],
        "content": row["content"] or "",
        "relations": json.loads(row["relations"] or "[]"),
        "properties": json.loads(row["properties"] or "{}"),
        "importance": row["importance"],
        "pinned": bool(row["pinned"]),
        "is_root": bool(row["is_root"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "deprecated_at": row["deprecated_at"],
        "stale_marked_at": row["stale_marked_at"],
        "verified_until": row["verified_until"],
    }


_DETAIL_COLS = (
    "name, type, content, relations, properties, importance, pinned, is_root, "
    "created_at, updated_at, deprecated_at, stale_marked_at, verified_until"
)


async def get_entities_by_names(names: list[str], include_deprecated: bool = False) -> list[dict]:
    """按名字数组批量读取实体完整字段（含 content 与时间戳）。

    替代逐条 get_entity：641 个实体判断过时不再需要几百次调用。
    返回顺序与传入 names 一致，未找到的名字不出现在结果里；
    另有 missing 字段由调用方通过对比得到，这里保持纯列表。
    """
    if not names:
        return []
    db = await _connect()
    # 去重但保留首次出现的顺序
    ordered = list(dict.fromkeys(names))
    placeholders = ",".join("?" for _ in ordered)
    sql = f"SELECT {_DETAIL_COLS} FROM entities WHERE name IN ({placeholders})"
    if not include_deprecated:
        sql += " AND deprecated_at IS NULL"
    cur = await db.execute(sql, ordered)
    found = {row["name"]: _row_to_detail(row) for row in await cur.fetchall()}
    return [found[n] for n in ordered if n in found]


async def list_entities_paged(
    offset: int = 0,
    limit: int = 100,
    entity_type: str | None = None,
    include_content: bool = True,
) -> dict:
    """分页列出实体，可选带 content，用于大批量判断过时。

    返回 { "total": 总数, "offset":, "limit":, "items": [...] }
    """
    db = await _connect()
    offset = max(0, offset)
    limit = max(1, min(limit, 1000))

    where = "deprecated_at IS NULL"
    params: list = []
    if entity_type:
        where += " AND type = ?"
        params.append(entity_type)

    cur = await db.execute(f"SELECT COUNT(*) AS c FROM entities WHERE {where}", params)
    total = (await cur.fetchone())["c"]

    cols = _DETAIL_COLS if include_content else (
        "name, type, importance, pinned, is_root, created_at, updated_at, "
        "deprecated_at, stale_marked_at, verified_until"
    )
    cur = await db.execute(
        f"SELECT {cols} FROM entities WHERE {where} "
        "ORDER BY importance DESC, name LIMIT ? OFFSET ?",
        params + [limit, offset],
    )
    items = []
    for row in await cur.fetchall():
        item = {
            "name": row["name"],
            "type": row["type"],
            "importance": row["importance"],
            "pinned": bool(row["pinned"]),
            "is_root": bool(row["is_root"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "deprecated_at": row["deprecated_at"],
            "stale_marked_at": row["stale_marked_at"],
            "verified_until": row["verified_until"],
        }
        if include_content:
            item["content"] = row["content"] or ""
            item["relations"] = json.loads(row["relations"] or "[]")
            item["properties"] = json.loads(row["properties"] or "{}")
        items.append(item)
    return {"total": total, "offset": offset, "limit": limit, "items": items}


# ── 过时标注（结构化，不走正文文本匹配）─────────────────


async def set_stale_mark(
    name: str,
    stale_marked_at: str | None = None,
    verified_until: str | None = None,
    clear: bool = False,
) -> bool:
    """写入或清除实体的过时标注字段。

    stale_marked_at 记录「什么时候被打上待验证标注」，
    verified_until 记录「核实到哪个日期为止有效」。两者都是结构化列，
    体检、清理都直接查字段，不再靠 content LIKE '%待验证%' 匹配。
    clear=True 时两个字段一起清空（核实无误后去掉标注）。
    """
    db = await _connect()
    cur = await db.execute(
        "SELECT name FROM entities WHERE name=? AND deprecated_at IS NULL", (name,)
    )
    if not await cur.fetchone():
        return False
    if clear:
        await db.execute(
            "UPDATE entities SET stale_marked_at=NULL, verified_until=NULL, updated_at=? WHERE name=?",
            (now_ts(), name),
        )
    else:
        marked = stale_marked_at or now_ts()
        await db.execute(
            "UPDATE entities SET stale_marked_at=?, verified_until=?, updated_at=? WHERE name=?",
            (marked, verified_until, now_ts(), name),
        )
    await db.commit()
    return True


# ── 批量写入 ────────────────────────────────────────────


async def batch_update_entities(
    names: list[str],
    new_type: str | None = None,
    stale_marked_at: str | None = None,
    verified_until: str | None = None,
    clear_stale: bool = False,
    importance: int | None = None,
    soft_delete: bool = False,
) -> dict:
    """对一批实体执行同一项修改：重设类型 / 打标注 / 设重要度 / 软删除。

    整理天生是批量的，这里一次处理多个目标，避免逐条调 update_memory。
    返回逐条结果，未找到的记入 missing，不会静默跳过。
    """
    applied: list[str] = []
    missing: list[str] = []
    for name in dict.fromkeys(names):
        db = await _connect()
        cur = await db.execute(
            "SELECT name FROM entities WHERE name=? AND deprecated_at IS NULL", (name,)
        )
        if not await cur.fetchone():
            missing.append(name)
            continue

        if soft_delete:
            await soft_delete_entity(name)
            applied.append(name)
            continue

        sets: list[str] = []
        params: list = []
        if new_type is not None:
            sets.append("type=?")
            params.append(new_type)
        if clear_stale:
            sets.append("stale_marked_at=NULL")
            sets.append("verified_until=NULL")
        else:
            if stale_marked_at is not None:
                sets.append("stale_marked_at=?")
                params.append(stale_marked_at or now_ts())
            if verified_until is not None:
                sets.append("verified_until=?")
                params.append(verified_until)
        if importance is not None:
            sets.append("importance=?")
            params.append(max(1, min(int(importance), 10)))

        if not sets:
            missing.append(name)
            continue

        sets.append("updated_at=?")
        params.append(now_ts())
        params.append(name)
        await db.execute(f"UPDATE entities SET {', '.join(sets)} WHERE name=?", params)
        await db.commit()
        applied.append(name)

    return {
        "applied": applied,
        "applied_count": len(applied),
        "missing": missing,
        "missing_count": len(missing),
    }


# ── 关系索引维护 ──────────────────────────────────────


async def refresh_relation_index(entity_name: str):
    """增量更新——删掉该实体的旧出边，根据 entities.relations JSON 重建。"""
    db = await _connect()
    # 1. 删旧出边
    await db.execute(
        "DELETE FROM relation_index WHERE entity_name=?",
        (entity_name,),
    )
    # 2. 读当前 relations JSON + is_root
    cur = await db.execute(
        "SELECT relations, is_root FROM entities WHERE name=?",
        (entity_name,),
    )
    row = await cur.fetchone()
    if not row:
        await db.commit()
        return
    # 3. 根节点不能有出边，跳过
    if row["is_root"]:
        await db.commit()
        return
    # 4. 插入新索引
    relations_list = json.loads(row["relations"] or "[]")
    for rel in relations_list:
        await db.execute(
            "INSERT OR IGNORE INTO relation_index "
            "(entity_name, target_name, rel_type, deprecated_at) "
            "VALUES (?, ?, ?, NULL)",
            (entity_name, rel["name"], rel.get("rel", "")),
        )
    await db.commit()


async def rebuild_relation_index():
    """全量重建——遍历所有活跃实体，逐条重建出边索引。"""
    db = await _connect()
    await db.execute("DELETE FROM relation_index")
    cur = await db.execute(
        "SELECT name FROM entities WHERE deprecated_at IS NULL"
    )
    names = [r["name"] for r in await cur.fetchall()]
    await db.commit()
    for name in names:
        await refresh_relation_index(name)


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
    ts = now_ts()
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
    硬删除已禁用！请使用 soft_delete_fact（软删除，可恢复）替代。
    """
    raise RuntimeError(
        f"硬删除已禁用：delete_fact({fact_id}) 不可恢复。"
        "请使用 soft_delete_fact 进行软删除（可恢复）。"
    )


# ── 对话索引 ────────────────────────────────────────────


async def index_conversation(conv_id: str, file_path: str, title: str):
    db = await _connect()
    now = now_ts()
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


async def set_root(entity_name: str, is_root: bool):
    db = await _connect()
    await db.execute(
        "UPDATE entities SET is_root = ? WHERE name = ?",
        (1 if is_root else 0, entity_name),
    )
    if is_root:
        # 根节点不能有出边，删除所有它指向别人的关系索引
        await db.execute(
            "DELETE FROM relation_index WHERE entity_name=?",
            (entity_name,),
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
    """软删除实体：标记 deprecated_at=now，出边直接删，入边标记失效。返回是否找到。"""
    db = await _connect()
    cur = await db.execute("SELECT name FROM entities WHERE name=? AND deprecated_at IS NULL", (name,))
    if not await cur.fetchone():
        return False
    now = now_ts()
    # 1. 标记实体已删
    await db.execute("UPDATE entities SET deprecated_at=?, updated_at=? WHERE name=?", (now, now, name))
    # 2. 出边：直接删除（我的声明，我没了就作废）
    await db.execute("DELETE FROM relation_index WHERE entity_name=?", (name,))
    # 3. 入边：标记失效（别人的声明，目标没了）
    await db.execute(
        "UPDATE relation_index SET deprecated_at=? WHERE target_name=? AND deprecated_at IS NULL",
        (now, name),
    )
    # 4. 清理对话索引中的提及记录
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
    now = now_ts()
    await db.execute("UPDATE facts SET deprecated_at=? WHERE id=?", (now, fact_id))
    await db.commit()
    await cleanup_expired()
    return True


async def restore_entity(name: str) -> bool:
    """恢复软删除的实体：清除 deprecated_at，重建出边索引，恢复入边。返回是否找到。"""
    db = await _connect()
    cur = await db.execute("SELECT name FROM entities WHERE name=? AND deprecated_at IS NOT NULL", (name,))
    if not await cur.fetchone():
        return False
    await db.execute("UPDATE entities SET deprecated_at=NULL, updated_at=? WHERE name=?",
                     (now_ts(), name))
    await db.commit()
    # 出边：从 relations JSON 重建
    await refresh_relation_index(name)
    # 入边：重新生效
    db = await _connect()
    await db.execute(
        "UPDATE relation_index SET deprecated_at=NULL WHERE target_name=? AND deprecated_at IS NOT NULL",
        (name,),
    )
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
    """列出所有已软删除待清理的实体、关系索引和事实。"""
    db = await _connect()
    cur = await db.execute(
        "SELECT name, type, deprecated_at FROM entities WHERE deprecated_at IS NOT NULL ORDER BY deprecated_at DESC"
    )
    entities = [{"name": r["name"], "type": r["type"], "deprecated_at": r["deprecated_at"]} for r in await cur.fetchall()]
    cur = await db.execute(
        "SELECT id, content, type, deprecated_at FROM facts WHERE deprecated_at IS NOT NULL ORDER BY deprecated_at DESC"
    )
    facts = [{"id": r["id"], "content": r["content"][:80], "type": r["type"], "deprecated_at": r["deprecated_at"]} for r in await cur.fetchall()]
    cur = await db.execute(
        "SELECT DISTINCT entity_name, target_name, rel_type, deprecated_at FROM relation_index WHERE deprecated_at IS NOT NULL ORDER BY deprecated_at DESC"
    )
    relations_idx = [
        {"from": r["entity_name"], "to": r["target_name"], "rel_type": r["rel_type"], "deprecated_at": r["deprecated_at"]}
        for r in await cur.fetchall()
    ]
    return {"entities": entities, "facts": facts, "relation_index": relations_idx}


# ── 数据迁移（旧 relations → entities.relations） ──────


async def migrate_from_old_schema():
    """将旧 relations 表数据合并到 entities.relations JSON 中。幂等，可重复运行。"""
    db = await _connect()
    cur = await db.execute(
        "SELECT from_name, to_name, rel_type FROM relations WHERE deprecated_at IS NULL"
    )
    rows = await cur.fetchall()
    migrated = 0
    for row in rows:
        from_name, to_name, rel_type = row["from_name"], row["to_name"], row["rel_type"]
        # 读当前 relations JSON
        cur2 = await db.execute(
            "SELECT relations FROM entities WHERE name=? AND deprecated_at IS NULL",
            (from_name,),
        )
        ent = await cur2.fetchone()
        if not ent:
            continue
        rels = json.loads(ent["relations"] or "[]")
        # 检查是否已存在（幂等）
        if any(r["name"] == to_name and r.get("rel", "") == rel_type for r in rels):
            continue
        rels.append({"name": to_name, "rel": rel_type})
        await db.execute(
            "UPDATE entities SET relations=? WHERE name=?",
            (json.dumps(rels, ensure_ascii=False), from_name),
        )
        migrated += 1
    await db.commit()
    # 全量重建索引
    if migrated:
        await rebuild_relation_index()
    return f"迁移完成：处理 {migrated} 条旧关系"


# ── 合并 ────────────────────────────────────────────────


async def _plan_merge(source: str, target: str) -> dict:
    """计算合并会产生什么后果，但不写任何数据。

    返回结构同时给 preview 和实际执行使用，保证「预览看到的」就是「执行会做的」。
    """
    db = await _connect()

    src = await (await db.execute(
        "SELECT * FROM entities WHERE name=? AND deprecated_at IS NULL", (source,)
    )).fetchone()
    if not src:
        return {"ok": False, "error": f"未找到源实体「{source}」"}

    tgt = await (await db.execute(
        "SELECT * FROM entities WHERE name=? AND deprecated_at IS NULL", (target,)
    )).fetchone()
    if not tgt:
        return {"ok": False, "error": f"未找到目标实体「{target}」"}

    src_rels = json.loads(src["relations"] or "[]")
    tgt_rels = json.loads(tgt["relations"] or "[]")
    existing_pairs = {(r["name"], r.get("rel", "")) for r in tgt_rels}
    rels_to_add: list[dict] = []
    rels_duplicate: list[dict] = []
    for r in src_rels:
        pair = (r["name"], r.get("rel", ""))
        if pair in existing_pairs:
            rels_duplicate.append(r)
        else:
            rels_to_add.append(r)
            existing_pairs.add(pair)

    # 迁走的事实：列出具体是哪几条，光给数量没法判断该不该合
    facts_to_move: list[dict] = []
    for row in await (await db.execute(
        "SELECT id, content, about_entities FROM facts WHERE deprecated_at IS NULL"
    )).fetchall():
        about = json.loads(row["about_entities"])
        if source in about:
            facts_to_move.append({
                "id": row["id"],
                "content": row["content"],
                "about_entities": about,
            })

    src_props = json.loads(src["properties"] or "{}")
    tgt_props = json.loads(tgt["properties"] or "{}")
    props_add = {k: v for k, v in src_props.items() if k not in tgt_props}
    props_conflict = {
        k: {"source": src_props[k], "target": tgt_props[k]}
        for k in src_props.keys() & tgt_props.keys()
        if src_props[k] != tgt_props[k]
    }

    # 入边：谁指向了源实体，合并后会改指目标
    inbound = await (await db.execute(
        "SELECT entity_name, rel_type FROM relation_index "
        "WHERE target_name=? AND deprecated_at IS NULL",
        (source,),
    )).fetchall()

    importance = tgt["importance"] or 1
    importance_after = max(importance, src["importance"] or 1)

    return {
        "ok": True,
        "source": {
            "name": src["name"],
            "type": src["type"],
            "importance": src["importance"],
            "pinned": bool(src["pinned"]),
            "content_length": len(src["content"] or ""),
        },
        "target": {
            "name": tgt["name"],
            "type": tgt["type"],
            "importance": tgt["importance"],
            "pinned": bool(tgt["pinned"]),
            "content_length": len(tgt["content"] or ""),
        },
        "type_conflict": src["type"] != tgt["type"],
        "relations_to_move": rels_to_add,
        "relations_to_move_count": len(rels_to_add),
        "relations_already_present": rels_duplicate,
        "relations_already_present_count": len(rels_duplicate),
        "facts_to_move": facts_to_move,
        "facts_to_move_count": len(facts_to_move),
        "properties_to_add": props_add,
        "properties_to_add_count": len(props_add),
        "properties_conflict": props_conflict,
        "properties_conflict_count": len(props_conflict),
        "inbound_relations": [{"from": r["entity_name"], "rel_type": r["rel_type"]} for r in inbound],
        "inbound_relations_count": len(inbound),
        "importance_before": importance,
        "importance_after": importance_after,
        "pinned_after": bool(tgt["pinned"] or src["pinned"]),
        "source_content_lost": bool((src["content"] or "").strip()),
        "source_content_preview": (src["content"] or "")[:200],
    }


async def preview_merge(source: str, target: str) -> dict:
    """干跑：返回合并会迁走哪些关系、哪些事实、属性有无冲突，不落库。"""
    if source == target:
        return {"ok": False, "error": "源实体和目标实体相同，无需合并"}
    return await _plan_merge(source, target)


async def merge_entities(source: str, target: str) -> str:
    """将源实体合并到目标实体：合并 relations JSON + 事实 + 属性，然后软删除源实体。"""
    if source == target:
        return "源实体和目标实体相同，无需合并"

    plan = await _plan_merge(source, target)
    if not plan["ok"]:
        return plan["error"]

    db = await _connect()
    tgt_rels = json.loads((await (await db.execute(
        "SELECT relations FROM entities WHERE name=?", (target,)
    )).fetchone())["relations"] or "[]")
    tgt_rels.extend(plan["relations_to_move"])
    await db.execute(
        "UPDATE entities SET relations=? WHERE name=?",
        (json.dumps(tgt_rels, ensure_ascii=False), target),
    )
    await refresh_relation_index(target)

    # 2. 迁移事实
    facts_moved = 0
    for item in plan["facts_to_move"]:
        new_about = json.dumps(list(set(
            target if x == source else x for x in item["about_entities"]
        )), ensure_ascii=False)
        await db.execute("UPDATE facts SET about_entities=? WHERE id=?", (new_about, item["id"]))
        facts_moved += 1

    # 3. 合并属性（目标已有的键优先，冲突的保留目标值）
    if plan["properties_to_add_count"] > 0:
        tgt_props = json.loads((await (await db.execute(
            "SELECT properties FROM entities WHERE name=?", (target,)
        )).fetchone())["properties"] or "{}")
        merged = {**tgt_props, **plan["properties_to_add"]}
        await db.execute(
            "UPDATE entities SET properties=? WHERE name=?",
            (json.dumps(merged, ensure_ascii=False), target),
        )

    # 4. 重要度和固定状态取高值
    if plan["importance_after"] > plan["importance_before"]:
        await db.execute(
            "UPDATE entities SET importance=? WHERE name=?",
            (plan["importance_after"], target),
        )
    if plan["pinned_after"] and not plan["target"]["pinned"]:
        await db.execute("UPDATE entities SET pinned=1 WHERE name=?", (target,))

    await db.commit()

    # 5. 软删除源实体
    await soft_delete_entity(source)

    return (
        f"已合并实体「{source}」→「{target}」："
        f"合并关系 {plan['relations_to_move_count']} 条"
        f"（另有 {plan['relations_already_present_count']} 条目标已有，跳过）、"
        f"事实 {facts_moved} 条、属性 {plan['properties_to_add_count']} 项"
        f"（属性冲突 {plan['properties_conflict_count']} 项，保留目标值）。"
    )


# ── 邻域查询（BFS）──────────────────────────────────────


async def get_entity_neighborhood(entity_name: str, depth: int = 2) -> dict:
    """BFS 查询指定实体的 N 层关联子图（基于 relation_index）。

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

    # BFS 展开（基于 relation_index）
    visited = {entity_name}
    current_level = [entity_name]
    all_layers: list[list[dict]] = []
    all_entity_names = {entity_name}

    for _ in range(depth):
        if not current_level:
            break
        placeholders = ",".join("?" for _ in current_level)

        # 出边 + 入边 UNION（relation_index 无 type，需要 JOIN entities）
        cur = await db.execute(f"""
            SELECT ri.entity_name AS from_name, ri.target_name AS to_name,
                   ri.rel_type, e_from.type AS from_type, e_to.type AS to_type
            FROM relation_index ri
            JOIN entities e_from ON e_from.name = ri.entity_name
            JOIN entities e_to ON e_to.name = ri.target_name
            WHERE ri.deprecated_at IS NULL
              AND (ri.entity_name IN ({placeholders}) OR ri.target_name IN ({placeholders}))
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


# ── 实体详情查询 ─────────────────────────────────────────


async def get_entity_detail(name: str) -> dict | None:
    """精准匹配读取单个实体的完整字段。未找到返回 None。"""
    db = await _connect()
    cur = await db.execute(
        f"SELECT {_DETAIL_COLS} FROM entities WHERE name=?",
        (name,),
    )
    row = await cur.fetchone()
    if not row:
        return None
    return _row_to_detail(row)
