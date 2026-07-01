"""
数据库连接 + 初始化 + 自动清理。
从 core/graph.py 拆出，仅供内部模块 import。
"""
import asyncio
from datetime import datetime, timezone, timedelta
from pathlib import Path

import aiosqlite

DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "graph.db"
_db = None
_db_lock = asyncio.Lock()


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
    """物理删除 24 小时前软删除的实体、关系索引、关系和事实。"""
    db = await _connect()
    deadline = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime("%Y:%m:%d:%H:%M:%S")

    await db.execute("DELETE FROM entities WHERE deprecated_at IS NOT NULL AND deprecated_at <= ?", (deadline,))
    await db.execute("DELETE FROM relation_index WHERE deprecated_at IS NOT NULL AND deprecated_at <= ?", (deadline,))
    await db.execute("DELETE FROM relations WHERE deprecated_at IS NOT NULL AND deprecated_at <= ?", (deadline,))
    await db.execute("DELETE FROM facts WHERE deprecated_at IS NOT NULL AND deprecated_at <= ?", (deadline,))
    await db.commit()
