"""
知识图谱 CRUD：实体/关系/事实增删改、软删除、恢复、合并、浏览。
从 core/graph.py 拆出。
"""
import json

from .db import _connect, cleanup_expired, history_versions, now_ts, prune_entity_history


# ── 实体历史快照 ───────────────────────────────────────
# 软删除有 deprecated_at + restore 可回退，合并也靠它。但改正文/改类型/
# 改关系/改重要度这类就地修改原本没有任何回退点，只能手动 cp 数据库。
# 这里在写入前存一份文本快照（不含 embedding），回滚走 rollback_entity。
#
# 快照在**改动生效前**存当前状态，语义是「被覆盖掉的那一版」；
# 这样 version 1 是初始版本，回滚到 version N 得到的是第 N 次改动前的样子。

_HISTORY_COLS = (
    "type, content, relations, properties, importance, pinned, is_root, "
    "stale_marked_at, verified_until, created_at, updated_at"
)

_HISTORY_TEXT_FIELDS = (
    "type", "content", "relations", "properties", "importance", "pinned",
    "is_root", "stale_marked_at", "verified_until", "created_at", "updated_at",
)


async def _snapshot_entity(db, name: str, kind: str, note: str = ""):
    """把实体当前状态存为一版历史。必须在该实体的任何写入之前调用。

    只存文本字段，不存 embedding：实测向量占库体积 27.8MB/35.9MB，
    带向量存历史会让 5 版快照膨胀到 140MB，比重建一遍图还贵。
    回滚后向量按需由 backfill_embeddings 重算。
    """
    row = await (await db.execute(
        f"SELECT {_HISTORY_COLS} FROM entities WHERE name=?", (name,)
    )).fetchone()
    if not row:
        return None

    cur = await db.execute(
        "SELECT COALESCE(MAX(version), 0) FROM entity_history WHERE name=?", (name,)
    )
    version = (await cur.fetchone())[0] + 1
    await db.execute(
        "INSERT INTO entity_history ("
        "name, version, changed_at, change_kind, change_note, name_at_snapshot, "
        + _HISTORY_COLS +
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            name, version, now_ts(), kind, note, name,
            row["type"], row["content"], row["relations"], row["properties"],
            row["importance"], row["pinned"], row["is_root"],
            row["stale_marked_at"], row["verified_until"],
            row["created_at"], row["updated_at"],
        ),
    )
    await prune_entity_history(db, name)
    return version


async def list_entity_history(name: str, limit: int | None = None) -> list[dict]:
    """列出实体的历史版本摘要（不含全文），新版本在前。

    正文只给长度与首行摘要，避免 5 版全文把上下文撑爆；
    要看全文用 get_entity_history_version。
    """
    db = await _connect()
    keep = history_versions()
    cap = min(limit or keep, 100)
    cur = await db.execute(
        "SELECT id, name, version, changed_at, change_kind, change_note, type, "
        "LENGTH(content) AS content_len, content, importance, pinned, is_root, "
        "created_at, updated_at "
        "FROM entity_history WHERE name=? ORDER BY version DESC LIMIT ?",
        (name, cap),
    )
    items = []
    for r in await cur.fetchall():
        content = r["content"] or ""
        first_line = content.strip().splitlines()[0] if content.strip() else ""
        items.append({
            "version": r["version"],
            "changed_at": r["changed_at"],
            "change_kind": r["change_kind"],
            "change_note": r["change_note"],
            "type": r["type"],
            "content_len": r["content_len"] or 0,
            "content_head": first_line[:80],
            "importance": r["importance"],
            "pinned": bool(r["pinned"]),
            "is_root": bool(r["is_root"]),
            "created_at": r["created_at"],
            "updated_at": r["updated_at"],
        })
    return items


async def get_entity_history_version(name: str, version: int) -> dict | None:
    """读取某一版历史快照的全文。"""
    db = await _connect()
    cur = await db.execute(
        f"SELECT id, name, version, changed_at, change_kind, change_note, "
        f"name_at_snapshot, {_HISTORY_COLS} FROM entity_history "
        f"WHERE name=? AND version=?",
        (name, version),
    )
    row = await cur.fetchone()
    if not row:
        return None
    item = {
        "name": row["name"],
        "version": row["version"],
        "changed_at": row["changed_at"],
        "change_kind": row["change_kind"],
        "change_note": row["change_note"],
        "name_at_snapshot": row["name_at_snapshot"] or row["name"],
    }
    for col in _HISTORY_TEXT_FIELDS:
        item[col] = row[col]
    item["relations"] = json.loads(item["relations"] or "[]")
    item["properties"] = json.loads(item["properties"] or "{}")
    item["pinned"] = bool(item["pinned"])
    item["is_root"] = bool(item["is_root"])
    return item


def _diff_entity(current: dict | None, snapshot: dict) -> dict:
    """对比当前状态与历史快照，列出回滚会改动的字段（只报有变化的）。"""
    def _norm_json(value, fallback):
        """DB 里存的是 JSON 字符串，快照已被解析成对象，比较前先拉平成同一种形态。"""
        if value is None:
            return fallback
        if isinstance(value, str):
            try:
                return json.loads(value)
            except (json.JSONDecodeError, TypeError):
                return fallback
        return value

    changes = []
    for field in _HISTORY_TEXT_FIELDS:
        now_val = None if current is None else current.get(field)
        old_val = snapshot.get(field)
        if field in ("pinned", "is_root"):
            # 布尔字段按真假比较：库里存 0/1，快照经 get_..._version 转成了
            # bool。不归一化的话 False 与 0 会被判成有差异，干跑虚报改动。
            if bool(now_val) != bool(old_val):
                changes.append({"field": field, "current": bool(now_val), "target": bool(old_val)})
            continue
        if field == "importance":
            if now_val != old_val:
                changes.append({"field": field, "current": now_val, "target": old_val})
            continue
        if field in ("relations", "properties"):
            # 两者都是 JSON 列，不解析就会因为「字符串 vs 对象」恒不相等而虚报改动
            if _norm_json(now_val, [] if field == "relations" else {}) != \
               _norm_json(old_val, [] if field == "relations" else {}):
                changes.append({
                    "field": field,
                    "current": now_val,
                    "target": old_val,
                    "current_len": len(now_val or ""),
                    "target_len": len(json.dumps(old_val, ensure_ascii=False)),
                })
            continue
        if (now_val or "") != (old_val or ""):
            changes.append({
                "field": field,
                "current": now_val,
                "target": old_val,
                "current_len": len(now_val or ""),
                "target_len": len(old_val or ""),
            })
    return {
        "exists": current is not None,
        "changes": changes,
        "changed_fields": [c["field"] for c in changes],
    }


def _same_json(a, b, fallback):
    """比较两个 JSON 列是否等价（解析后比，忽略 dumps 的空格差异）。"""
    def _p(v):
        if v is None:
            return fallback
        if isinstance(v, str):
            try:
                return json.loads(v)
            except (json.JSONDecodeError, TypeError):
                return v
        return v
    return _p(a) == _p(b)


def _validate_relations_payload(relations) -> tuple[str | None, list[str]]:
    """校验一份 relations 负载能否安全写回。

    返回 (致命错误, 警告列表)。致命错误表示快照结构已损坏、必须让用户先修；
    警告表示可以继续回滚，但有个别条目会被索引跳过。

    分两档的理由：回滚的价值就是「把正文救回来」，不能因为一条脏关系
    就把整个回滚拒掉。缺失 name 键属于结构损坏（历史上会让索引重建抛
    KeyError、留下半成品），要拦；name 为空只是产生一条无意义的悬空边，
    交给 refresh_relation_index 跳过并如实报出来即可。
    """
    warnings: list[str] = []
    if not isinstance(relations, list):
        return f"快照里的 relations 不是列表（{type(relations).__name__}），拒绝回滚以免写坏索引", warnings
    for i, rel in enumerate(relations):
        if not isinstance(rel, dict):
            return f"快照里的 relations[{i}] 不是对象，拒绝回滚以免写坏索引", warnings
        if "name" not in rel:
            return (f"快照里的 relations[{i}] 缺少 name 键，结构已损坏；"
                    f"拒绝回滚以免留下「正文已改、索引未重建」的半成品。"
                    f"可用 update_memory 先修好该条关系再回滚"), warnings
        if not rel.get("name"):
            warnings.append(
                f"relations[{i}] 的 name 为空（{rel.get('name')!r}），"
                f"该条不会写入关系索引（避免指向空名字的悬空边）")
    return None, warnings


async def rollback_entity(name: str, version: int, dry_run: bool = True,
                          new_name: str | None = None) -> dict:
    """把实体回滚到某一版历史快照。dry_run=True 时只出 diff，不写库。

    回滚前先把当前状态再存一版，所以回滚本身也可回溯，不会把历史冲掉。
    relations 变化后重建 relation_index，避免改名或改关系留下悬空边。
    注意：facts.about_entities 的归属还原不做（fan-out 太大），
    历史实体若改过名，事实关联停留在现名上，返回值里会说明。
    """
    db = await _connect()
    snapshot = await get_entity_history_version(name, version)
    if not snapshot:
        return {
            "ok": False,
            "error": f"实体「{name}」没有第 {version} 版历史（版本可能已被裁到 "
                     f"{history_versions()} 版以内）",
        }

    cur = await db.execute(
        f"SELECT name, {_HISTORY_COLS} FROM entities WHERE name=?", (name,)
    )
    row = await cur.fetchone()
    current = None
    if row:
        current = {col: row[col] for col in _HISTORY_TEXT_FIELDS}
        current["name"] = row["name"]

    # relations 的体检要在构造 result 之前做：干跑也要如实报出告警，
    # 且 result 里要带上 warnings 字段。
    rel_error, rel_warnings = _validate_relations_payload(snapshot["relations"])

    diff = _diff_entity(current, snapshot)
    # 改名也算一处待还原的差异：快照存时的名字与现名不同，说明改名发生在
    # 这次快照之后，回滚要连名字一起改回去。干跑时如实报出来。
    will_rename = (new_name or snapshot["name_at_snapshot"] or name) != name
    if will_rename:
        diff["changes"].insert(0, {
            "field": "name",
            "current": name,
            "target": new_name or snapshot["name_at_snapshot"] or name,
        })
        diff["changed_fields"] = ["name"] + diff["changed_fields"]
    result = {
        "ok": True,
        "name": name,
        "version": version,
        "snapshot_changed_at": snapshot["changed_at"],
        "snapshot_kind": snapshot["change_kind"],
        "dry_run": dry_run,
        "diff": diff["changes"],
        "changed_fields": diff["changed_fields"],
        "restored_name": new_name or snapshot["name_at_snapshot"] or name,
        "warnings": rel_warnings,
        "note": "facts.about_entities 的归属不随回滚还原；若曾改名，事实仍挂在现名上。",
    }
    if dry_run or not diff["changes"]:
        return result

    target_name = new_name or snapshot["name_at_snapshot"] or name

    # ── 写入前的全部前置校验，一个字节都不许先写 ──
    # 教训：早先占用检查只看 deprecated_at IS NULL，漏掉「软删除但未物理清理」
    # 的同名行，改名会撞 UNIQUE 约束抛 IntegrityError；而那时快照已写、未提交，
    # 会在下一次任意写入时被顺带提交成一条根本没发生过的幽灵版本。
    # 同理，relations 结构损坏会让索引重建在正文 update 之后才炸，留下半成品。
    if rel_error:
        result["ok"] = False
        result["error"] = rel_error
        return result

    if target_name != name:
        # 软删除行也占着主键（name 是 PRIMARY KEY，软删只是标记 deprecated_at），
        # 所以这里必须不带 deprecated_at 条件地查，否则改名照样撞唯一约束。
        check = await (await db.execute(
            "SELECT deprecated_at FROM entities WHERE name=?", (target_name,)
        )).fetchone()
        if check:
            if check["deprecated_at"] is None:
                result["ok"] = False
                result["error"] = (f"回滚需要把实体改回原名「{target_name}」，"
                                   f"但该名称已被占用；可先改名腾出，或传 new_name 指定别的名称")
            else:
                result["ok"] = False
                result["error"] = (f"回滚需要把实体改回原名「{target_name}」，但该名称被一条"
                                   f"已软删除的实体占着（作废于 {check['deprecated_at']}）；"
                                   f"name 是主键，软删行也占用它。先物理清理该行"
                                   f"（等保留窗口过）或传 new_name 指定别的名称")
            return result

    # 到这里才允许落库：先存回滚前状态，再改名，最后写快照字段，一次 commit。
    # 整段包在事务里，任何一步抛错都 rollback：_snapshot_entity 的 INSERT 在
    # 提交前是挂起状态，若留着不管，会被下一次任意写入顺带提交成一条
    # 「根本没发生过的回滚」幽灵历史（实测确认过这条路径）。
    renamed = False
    try:
        await _snapshot_entity(db, name, "rollback", f"回滚到第 {version} 版")
        if target_name != name:
            await _rename_entity(db, name, target_name)
            renamed = True

        await db.execute(
            "UPDATE entities SET type=?, content=?, relations=?, properties=?, "
            "importance=?, pinned=?, is_root=?, stale_marked_at=?, verified_until=?, "
            "updated_at=? WHERE name=?",
            (
                snapshot["type"], snapshot["content"],
                json.dumps(snapshot["relations"], ensure_ascii=False),
                json.dumps(snapshot["properties"], ensure_ascii=False),
                snapshot["importance"], int(snapshot["pinned"]),
                int(snapshot["is_root"]), snapshot["stale_marked_at"],
                snapshot["verified_until"], now_ts(), target_name,
            ),
        )
        await db.commit()
    except Exception as e:
        await db.rollback()
        result["ok"] = False
        result["error"] = f"回滚失败并已整体回退（未留下半成品）：{type(e).__name__}: {e}"
        return result

    # relations 或改名都会影响索引，统一重建，避免留下悬空边
    await refresh_relation_index(target_name)
    result["restored_name"] = target_name
    result["renamed"] = renamed
    return result


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
    cur = await db.execute(
        "SELECT type, content, relations, properties, is_root FROM entities WHERE name=?", (name,)
    )
    row = await cur.fetchone()
    exists = row is not None

    if exists:
        # 同值重放不存快照。save() 每次都调 upsert_entity，重放同一份内容
        # 若照样各存一版，N 版额度会被同一份内容吃满，把真正需要的那版
        # （含原始正文的）挤出窗口，回滚就再也回不到改动前了（实测确认）。
        if not (
            etype == row["type"]
            and (content or "") == (row["content"] or "")
            and _same_json(relations_json, row["relations"], [])
            and _same_json(props_json, row["properties"], {})
            and int(is_root) == int(row["is_root"] or 0)
        ):
            # 覆盖写入会丢掉旧正文，先存快照再改
            await _snapshot_entity(db, name, "upsert", "写入覆盖")
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


async def _rename_entity(db, old_name: str, new_name: str, overrides: dict | None = None):
    """把实体从 old_name 改名到 new_name，并同步所有按名字索引的关联数据。

    调用方负责占用检查与 commit。改名用 INSERT 新行 + DELETE 旧行，
    因为 name 是主键不能 UPDATE。列清单从表的实际列动态取，
    避免新增实体列时漏掉而静默清空（此前 embedding/stale 标注就被漏过）。
    """
    overrides = overrides or {}
    cur_cols = await db.execute("PRAGMA table_info(entities)")
    copied = [r[1] for r in await cur_cols.fetchall() if r[1] != "name"]
    select_parts: list[str] = []
    select_params: list = []
    for col in copied:
        if col in overrides:
            select_parts.append("?")
            select_params.append(overrides[col])
        else:
            select_parts.append(col)

    await db.execute(
        f"INSERT INTO entities (name, {', '.join(copied)}) "
        f"SELECT ?, {', '.join(select_parts)} FROM entities WHERE name=?",
        (new_name, *select_params, old_name),
    )
    await db.execute("DELETE FROM entities WHERE name=?", (old_name,))

    # relation_index：出边 + 入边
    await db.execute(
        "UPDATE relation_index SET entity_name=? WHERE entity_name=?", (new_name, old_name)
    )
    await db.execute(
        "UPDATE relation_index SET target_name=? WHERE target_name=?", (new_name, old_name)
    )

    # facts.about_entities JSON 替换
    cur_facts = await db.execute(
        "SELECT id, about_entities FROM facts WHERE deprecated_at IS NULL"
    )
    for frow in await cur_facts.fetchall():
        about = json.loads(frow["about_entities"])
        if old_name in about:
            new_about = json.dumps(list(set(
                new_name if x == old_name else x for x in about
            )), ensure_ascii=False)
            await db.execute(
                "UPDATE facts SET about_entities=? WHERE id=?", (new_about, frow["id"])
            )

    # conversation_mentions
    await db.execute(
        "UPDATE conversation_mentions SET entity_name=? WHERE entity_name=?",
        (new_name, old_name),
    )

    # 历史快照跟着改名走。entity_history 按 name 索引，不跟着改名的话
    # 旧实体的历史会变成无人认领的孤儿，回滚时查不到任何版本。
    # name_at_snapshot 保留存快照时的原名，回滚到改名之前才能把名字改回去。
    await db.execute(
        "UPDATE entity_history SET name=? WHERE name=?", (new_name, old_name)
    )


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

        # 改名前的旧状态存一版，改错了能回滚回来
        await _snapshot_entity(db, name, "rename", f"改名为「{new_name}」")

        # 1. 主表改名（INSERT 新行 + DELETE 旧行，因为 PRIMARY KEY 不可 UPDATE）
        # 注意：这里是显式列清单，新增实体列必须同步加进来。此前的清单漏了
        # stale_marked_at / verified_until / embedding，会导致改名时这些列被
        # 静默清空（实测确认：改名后带向量的实体 671→670）。改为按表的实际
        # 列动态复制，只覆盖确实要改的列，以后新增列不会再漏。
        rename_is_root = int(new_is_root) if new_is_root is not None else None
        overrides: dict[str, object] = {"updated_at": now}
        if rename_is_root is not None:
            overrides["is_root"] = rename_is_root

        # 改名本体 + 所有按名字索引的关联数据，统一走 _rename_entity，
        # 保证与回滚改名走的是同一套逻辑（含历史快照跟着改名）
        await _rename_entity(db, name, new_name, overrides)
        await db.commit()

        # 改名后索引重建用新名称
        await refresh_relation_index(new_name)
        return True

    # ── 普通更新（不改名）──
    is_root = int(new_is_root) if new_is_root is not None else row["is_root"]
    # 全传 None 的调用（memory.update 用它当存在性检查）不该占一格历史额度，
    # 只在真的有字段变化时才存快照。比较前先归一化，避免 None/'[]' 这类假差异。
    changed = (
        etype != row["type"]
        or (content or "") != (row["content"] or "")
        or (relations_json or "[]") != (row["relations"] or "[]")
        or (props or "{}") != (row["properties"] or "{}")
        or int(is_root) != int(row["is_root"] or 0)
    )
    if changed:
        await _snapshot_entity(db, name, "update", "就地修改")
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
            "SELECT type, importance, stale_marked_at, verified_until FROM entities "
            "WHERE name=? AND deprecated_at IS NULL", (name,)
        )
        row = await cur.fetchone()
        if not row:
            missing.append(name)
            continue

        if soft_delete:
            await soft_delete_entity(name)
            applied.append(name)
            continue

        sets: list[str] = []
        params: list = []
        requested: list[tuple[str, object]] = []   # 用于同值判定
        if new_type is not None:
            sets.append("type=?")
            params.append(new_type)
            requested.append(("type", new_type))
        if clear_stale:
            sets.append("stale_marked_at=NULL")
            sets.append("verified_until=NULL")
            requested.append(("stale_marked_at", None))
            requested.append(("verified_until", None))
        else:
            if stale_marked_at is not None:
                mark = stale_marked_at or now_ts()
                sets.append("stale_marked_at=?")
                params.append(mark)
                requested.append(("stale_marked_at", mark))
            if verified_until is not None:
                sets.append("verified_until=?")
                params.append(verified_until)
                requested.append(("verified_until", verified_until))
        if importance is not None:
            imp = max(1, min(int(importance), 10))
            sets.append("importance=?")
            params.append(imp)
            requested.append(("importance", imp))

        if not sets:
            missing.append(name)
            continue

        # 同值不写、不存快照。整理常对同一批反复下发同一条指令，每次都存快照
        # 会把 N 版额度吃满，把含原始正文的那版挤出窗口（实测确认）。
        if all(row[field] == value for field, value in requested):
            applied.append(name)      # 结果已符合要求，算执行成功但不占额度
            continue

        # 批量改类型/标注/重要度同样是就地覆盖，逐条存快照，改错了能按实体回滚
        note_parts = []
        if new_type is not None:
            note_parts.append(f"类型→{new_type}")
        if importance is not None:
            note_parts.append(f"重要度→{importance}")
        if clear_stale:
            note_parts.append("清过时标注")
        elif stale_marked_at is not None or verified_until is not None:
            note_parts.append("打过时标注")
        await _snapshot_entity(db, name, "batch", "批量整理：" + "、".join(note_parts))

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
        # 名字为空的关系写进去就是一条指向 '' 的悬空边，查询侧 JOIN 不到、
        # 前端也渲染不出来，只会污染体检的悬空清单。这里统一挡掉，
        # 让所有写入路径（含回滚）都不必各自防一遍。
        if not isinstance(rel, dict) or not rel.get("name"):
            continue
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


# ── 固定注入实体 ─────────────────────────────────────────


async def get_pinned_entities() -> list[dict]:
    """获取所有固定注入的实体（完整字段，按重要度降序）。

    原先放在 graph_search.py，随字面检索引擎一并删除后迁到这里：
    它是固定记忆注入通道的来源，与检索算法无关，不随检索方式改变。
    """
    db = await _connect()
    cur = await db.execute(
        "SELECT name, type, content, relations, properties, importance, pinned "
        "FROM entities WHERE pinned=1 AND deprecated_at IS NULL ORDER BY importance DESC"
    )
    result = []
    for r in await cur.fetchall():
        try:
            relations = json.loads(r["relations"]) if r["relations"] else []
        except (json.JSONDecodeError, TypeError):
            relations = []
        try:
            properties = json.loads(r["properties"]) if r["properties"] else {}
        except (json.JSONDecodeError, TypeError):
            properties = {}
        result.append({
            "entity": r["name"],
            "type": r["type"],
            "content": r["content"],
            "relations": relations,
            "properties": properties,
            "importance": r["importance"],
            "pinned": bool(r["pinned"]),
        })
    return result


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
