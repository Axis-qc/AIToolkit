"""
数据库连接 + 初始化 + 自动清理。
从 core/graph.py 拆出，仅供内部模块 import。
"""
import asyncio
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

import aiosqlite

from .config import settings

DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "graph.db"
_db = None
_db_lock = asyncio.Lock()

# ── 时间戳规范 ──────────────────────────────────────────
# 写入统一用冒号格式，取值一律走 now_ts()，不在各模块自己拼 now。
TS_FORMAT = "%Y:%m:%d:%H:%M:%S"
TS_FALLBACK_FORMAT = "%Y:%m:%d:%H:%M:%S.%f"


def now_ts() -> str:
    """当前 UTC 时间的规范时间戳字符串（冒号格式）。

    所有写入路径统一调这里，不再各模块自己 strftime。
    """
    return datetime.now(timezone.utc).strftime(TS_FORMAT)


def parse_ts(value: str | None) -> datetime | None:
    """解析时间戳，兼容历史遗留格式，失败返回 None。

    支持：冒号格式（现行规范）、带微秒的冒号格式、ISO 8601
    （旧数据里有 10 个实体的 created_at 和 285 条 facts.ts 是这种）。
    返回带 UTC 时区的 datetime，便于直接做日期比较。
    """
    if not value or not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    for fmt in (TS_FORMAT, TS_FALLBACK_FORMAT):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def retention_hours() -> float:
    """废弃内容的保留窗口（小时），读配置，默认 24。"""
    try:
        hours = float(settings.deprecated_retention_hours)
    except (TypeError, ValueError):
        return 24.0
    return hours if hours > 0 else 24.0


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
            content TEXT DEFAULT '',
            relations TEXT DEFAULT '[]',
            properties TEXT DEFAULT '{}',
            importance INTEGER NOT NULL DEFAULT 1,
            pinned INTEGER NOT NULL DEFAULT 0,
            is_root INTEGER NOT NULL DEFAULT 0,
            created_at TEXT DEFAULT NULL,
            updated_at TEXT DEFAULT NULL,
            deprecated_at TEXT DEFAULT NULL
        );
        CREATE TABLE IF NOT EXISTS relation_index (
            entity_name TEXT NOT NULL,
            target_name TEXT NOT NULL,
            rel_type TEXT NOT NULL,
            deprecated_at TEXT DEFAULT NULL,
            PRIMARY KEY (entity_name, target_name, rel_type)
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
            about_entities TEXT DEFAULT '[]',
            deprecated_at TEXT DEFAULT NULL
        );
        CREATE TABLE IF NOT EXISTS tombstones (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL,
            ref TEXT NOT NULL,
            type TEXT DEFAULT '',
            content TEXT DEFAULT '',
            detail TEXT DEFAULT '{}',
            deprecated_at TEXT DEFAULT NULL,
            purged_at TEXT DEFAULT NULL
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
    # ── 旧字段兼容（已有数据库升级） ──
    for col in ("content", "relations"):
        try:
            await db.execute(f"ALTER TABLE entities ADD COLUMN {col} TEXT DEFAULT ''")
        except Exception:
            pass
    for col in ("created_at", "updated_at"):
        try:
            await db.execute(f"ALTER TABLE entities ADD COLUMN {col} TEXT DEFAULT NULL")
        except Exception:
            pass
    try:
        await db.execute("ALTER TABLE relations ADD COLUMN to_type TEXT NOT NULL DEFAULT ''")
    except Exception:
        pass
    try:
        await db.execute("ALTER TABLE entities ADD COLUMN deprecated_at TEXT DEFAULT NULL")
    except Exception:
        pass
    try:
        await db.execute("ALTER TABLE facts ADD COLUMN deprecated_at TEXT DEFAULT NULL")
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
    try:
        await db.execute("ALTER TABLE relations ADD COLUMN deprecated_at TEXT DEFAULT NULL")
    except Exception:
        pass
    try:
        await db.execute("ALTER TABLE entities ADD COLUMN is_root INTEGER NOT NULL DEFAULT 0")
    except Exception:
        pass
    # ── 过时标注结构化（替代正文里写标注再文本匹配）──
    for col in ("stale_marked_at", "verified_until"):
        try:
            await db.execute(f"ALTER TABLE entities ADD COLUMN {col} TEXT DEFAULT NULL")
        except Exception:
            pass
    try:
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_entities_stale ON entities(stale_marked_at)
        """)
    except Exception:
        pass
    try:
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_tombstones_kind ON tombstones(kind)
        """)
    except Exception:
        pass
    try:
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_ri_target ON relation_index(target_name)
        """)
    except Exception:
        pass
    try:
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_ri_dep ON relation_index(deprecated_at)
        """)
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

    await db.commit()
    await cleanup_expired()


async def close():
    global _db
    if _db is not None:
        await _db.close()
        _db = None


async def cleanup_expired():
    """把超过保留窗口的软删除内容移入墓地，然后物理删除原行。

    窗口由 settings.deprecated_retention_hours 决定，默认 24 小时。
    实体和事实在删除前会把内容写进 tombstones，这样整理流程
    「我出清单、你确认、再执行」跨天回来也还能查到被清掉的东西。
    """
    db = await _connect()
    hours = retention_hours()
    deadline = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime(TS_FORMAT)
    purged_at = now_ts()

    # 实体：先入墓再删
    cur = await db.execute(
        "SELECT name, type, content, relations, properties, importance, pinned, "
        "is_root, created_at, updated_at, deprecated_at FROM entities "
        "WHERE deprecated_at IS NOT NULL AND deprecated_at <= ?",
        (deadline,),
    )
    for row in await cur.fetchall():
        detail = {
            "relations": row["relations"] or "[]",
            "properties": row["properties"] or "{}",
            "importance": row["importance"],
            "pinned": bool(row["pinned"]),
            "is_root": bool(row["is_root"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
        await db.execute(
            "INSERT INTO tombstones (kind, ref, type, content, detail, deprecated_at, purged_at) "
            "VALUES ('entity', ?, ?, ?, ?, ?, ?)",
            (row["name"], row["type"], row["content"] or "",
             json.dumps(detail, ensure_ascii=False), row["deprecated_at"], purged_at),
        )

    # 事实：先入墓再删
    cur = await db.execute(
        "SELECT id, content, type, about_entities, deprecated_at FROM facts "
        "WHERE deprecated_at IS NOT NULL AND deprecated_at <= ?",
        (deadline,),
    )
    for row in await cur.fetchall():
        detail = {"about_entities": row["about_entities"] or "[]"}
        await db.execute(
            "INSERT INTO tombstones (kind, ref, type, content, detail, deprecated_at, purged_at) "
            "VALUES ('fact', ?, ?, ?, ?, ?, ?)",
            (str(row["id"]), row["type"], row["content"] or "",
             json.dumps(detail, ensure_ascii=False), row["deprecated_at"], purged_at),
        )

    # 关系索引：入墓后删（保留来源与去向，便于事后追溯）
    cur = await db.execute(
        "SELECT entity_name, target_name, rel_type, deprecated_at FROM relation_index "
        "WHERE deprecated_at IS NOT NULL AND deprecated_at <= ?",
        (deadline,),
    )
    for row in await cur.fetchall():
        detail = {"from": row["entity_name"], "to": row["target_name"], "rel_type": row["rel_type"]}
        await db.execute(
            "INSERT INTO tombstones (kind, ref, type, content, detail, deprecated_at, purged_at) "
            "VALUES ('relation', ?, ?, '', ?, ?, ?)",
            (row["entity_name"], row["target_name"] or "",
             json.dumps(detail, ensure_ascii=False), row["deprecated_at"], purged_at),
        )

    await db.execute("DELETE FROM entities WHERE deprecated_at IS NOT NULL AND deprecated_at <= ?", (deadline,))
    await db.execute("DELETE FROM relation_index WHERE deprecated_at IS NOT NULL AND deprecated_at <= ?", (deadline,))
    await db.execute("DELETE FROM relations WHERE deprecated_at IS NOT NULL AND deprecated_at <= ?", (deadline,))
    await db.execute("DELETE FROM facts WHERE deprecated_at IS NOT NULL AND deprecated_at <= ?", (deadline,))
    await db.commit()


async def list_tombstones(kind: str | None = None, limit: int = 200) -> list[dict]:
    """查看已过保留窗口、被物理删除的内容（墓地）。

    只在 cleanup_expired 真正清库后才有数据，用于事后追溯。
    """
    db = await _connect()
    if kind:
        cur = await db.execute(
            "SELECT id, kind, ref, type, content, detail, deprecated_at, purged_at "
            "FROM tombstones WHERE kind=? ORDER BY purged_at DESC, id DESC LIMIT ?",
            (kind, limit),
        )
    else:
        cur = await db.execute(
            "SELECT id, kind, ref, type, content, detail, deprecated_at, purged_at "
            "FROM tombstones ORDER BY purged_at DESC, id DESC LIMIT ?",
            (limit,),
        )
    result = []
    for r in await cur.fetchall():
        try:
            detail = json.loads(r["detail"] or "{}")
        except (json.JSONDecodeError, TypeError):
            detail = {}
        result.append({
            "id": r["id"],
            "kind": r["kind"],
            "ref": r["ref"],
            "type": r["type"],
            "content": r["content"],
            "detail": detail,
            "deprecated_at": r["deprecated_at"],
            "purged_at": r["purged_at"],
        })
    return result
