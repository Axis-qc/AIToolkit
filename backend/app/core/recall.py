"""实体向量的回填与自愈刷新。

实体正文会被改名、合并、批量修改等多条路径改写，逐个挂更新点既容易漏，
也会碰既有写入语义。这里在 embedding 列里同时存内容指纹，检索时比对，
不一致就地重算。semantic_search 与 weak_link 都从这里取指纹与刷新入口。
"""
from __future__ import annotations

import asyncio
import json
import logging

from . import embedding as emb
from .db import _connect

logger = logging.getLogger(__name__)

# 回填的分块大小。每块独立「编码 → 写回 → commit」：整批只在末尾提交一次的话，
# 进程中途被杀会把已算好的向量全部丢掉，重启后待算量分毫未减，永远追不平。
REFRESH_CHUNK_SIZE = 64

# 自愈式刷新的互斥锁。自愈是「读全表 → 编码 → 写回」三步，并发触发会重复编码同一批文本。
_refresh_lock = asyncio.Lock()


def _fingerprint_of(row) -> str:
    """算某一行的当前内容指纹。"""
    try:
        relations = json.loads(row["relations"] or "[]")
    except (json.JSONDecodeError, TypeError):
        relations = []
    return emb.content_fingerprint(row["name"], row["type"], row["content"] or "", relations)


def _parse_embedding_cell(cell: str | None):
    """解析 embedding 列。返回 (向量, 指纹)，任一缺失即为 None。

    新格式是 {"v": [...], "f": "指纹"}；兼容纯数组的旧格式（无指纹，
    视为必然过期，会被重算一次后写成新格式）。
    """
    if not cell:
        return None, None
    try:
        data = json.loads(cell)
    except (json.JSONDecodeError, TypeError):
        return None, None

    if isinstance(data, dict):
        vector = data.get("v")
        fingerprint = data.get("f")
        if isinstance(vector, list) and len(vector) == emb.EMBEDDING_DIM:
            try:
                return [float(x) for x in vector], (fingerprint if isinstance(fingerprint, str) else None)
            except (TypeError, ValueError):
                return None, None
        return None, None

    if isinstance(data, list):
        if len(data) == emb.EMBEDDING_DIM:
            try:
                return [float(x) for x in data], None
            except (TypeError, ValueError):
                return None, None
    return None, None


def _serialize_cell(vector: list[float], fingerprint: str) -> str:
    """打包成 embedding 列的存储格式。"""
    return json.dumps(
        {"v": [round(float(x), 6) for x in vector], "f": fingerprint},
        separators=(",", ":"),
    )


async def _load_all_entities():
    """取出所有活跃实体的向量相关字段。"""
    db = await _connect()
    cur = await db.execute(
        "SELECT name, type, content, relations, importance, pinned, embedding "
        "FROM entities WHERE deprecated_at IS NULL"
    )
    return await cur.fetchall()


async def refresh_embeddings(names: list[str] | None = None) -> dict:
    """回填/刷新向量。

    names 为空则处理全部活跃实体。只对「指纹不一致或缺失」的实体重算，
    因此重复调用是廉价的。返回统计信息。
    """
    async with _refresh_lock:
        return await _refresh_embeddings_locked(names)


async def _refresh_embeddings_locked(names: list[str] | None = None) -> dict:
    rows = await _load_all_entities()
    if names is not None:
        wanted = set(names)
        rows = [r for r in rows if r["name"] in wanted]

    pending = []  # (name, fingerprint, text)
    for row in rows:
        fingerprint = _fingerprint_of(row)
        _vector, stored_fp = _parse_embedding_cell(row["embedding"])
        if stored_fp == fingerprint:
            continue
        try:
            relations = json.loads(row["relations"] or "[]")
        except (json.JSONDecodeError, TypeError):
            relations = []
        text = emb.build_entity_text(row["name"], row["type"], row["content"] or "", relations)
        pending.append((row["name"], fingerprint, text))

    if not pending:
        return {"checked": len(rows), "updated": 0, "failed": 0, "skipped_empty": 0}

    db = await _connect()
    updated = 0
    failed = 0
    skipped = 0

    for start in range(0, len(pending), REFRESH_CHUNK_SIZE):
        chunk = pending[start:start + REFRESH_CHUNK_SIZE]
        chunk_texts = [text for _n, _f, text in chunk]
        vectors = await asyncio.to_thread(emb.encode_texts, chunk_texts)
        if not vectors:
            # 模型不可用要说清楚，不能报成 failed:0 让调用方以为一切正常
            reason = emb.last_error() or "embedding model unavailable"
            logger.warning("backfill stopped: %s", reason)
            return {
                "checked": len(rows),
                "updated": updated,
                "failed": failed,
                "skipped_empty": skipped,
                "available": False,
                "error": reason,
            }
        if len(vectors) != len(chunk):
            # 条数对不上说明这一批编码结果不可信，宁可整批算失败也不能错位写回
            failed += len(chunk)
            logger.warning(
                "backfill chunk size mismatch: got %d want %d", len(vectors), len(chunk)
            )
            continue

        for (name, fingerprint, _text), vector in zip(chunk, vectors):
            if not vector:
                failed += 1
                continue
            if not any(vector):
                # 全零向量说明文本为空得连 name 都没有，存了也没用
                skipped += 1
                continue
            cur = await db.execute(
                "UPDATE entities SET embedding=? WHERE name=?",
                (_serialize_cell(vector, fingerprint), name),
            )
            # 只按实际影响行数计数：命中 0 行（例如实体在读取后被并发改名或作废）
            # 不能算作已更新，否则统计会谎报成功，掩盖真实失败。
            if cur.rowcount > 0:
                updated += 1
            else:
                failed += 1
        await db.commit()

    return {"checked": len(rows), "updated": updated, "failed": failed, "skipped_empty": skipped}


async def backfill(verbose: bool = True) -> dict:
    """全量回填向量。供 CLI 与测试调用。"""
    result = await refresh_embeddings(None)
    if verbose:
        logger.info("backfill result: %s", result)
    return result


if __name__ == "__main__":
    # 用法：python -m app.core.recall [--check]
    # --check 只统计覆盖率，不回填
    import sys

    from .db import close, init_db

    async def _main() -> None:
        await init_db()
        try:
            if "--check" in sys.argv:
                rows = await _load_all_entities()
                total = len(rows)
                fresh = 0
                stale = 0
                empty = 0
                for row in rows:
                    _vector, stored_fp = _parse_embedding_cell(row["embedding"])
                    if _vector is None:
                        empty += 1
                    elif stored_fp == _fingerprint_of(row):
                        fresh += 1
                    else:
                        stale += 1
                print(f"total={total} fresh={fresh} stale={stale} missing={empty}")
            else:
                print(await backfill())
        finally:
            # aiosqlite 的连接带后台线程，不关会让进程一直不退出
            await close()

    asyncio.run(_main())
