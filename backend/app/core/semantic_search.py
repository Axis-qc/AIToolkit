"""纯向量语义检索：把用户原始文本直接交给嵌入模型，与全库实体向量算余弦。

与 recall._vector_arm 的关系：

  * 同样自愈。发现指纹过期（正文在向量之后被改过）或缺向量，就地重算并写回，
    然后让这些实体参与本次检索。这一点与 _vector_arm 一致。
  * 默认不设阈值、不截断。调用方能拿到完整余弦排序，阈值只是可选参数。若默认
    就按线上阈值过滤，量到的会是阈值的效果，而不是模型本身的区分力——换嵌入
    模型时就没有干净基线可比。

自愈这一步是必需的，不是可选优化：早期版本只统计过期条数、把它们排除在计算外
（只读探针形态），后果是任何刚被改过正文的记忆会在语义检索里静默消失。实测
「知识图谱_检索引擎」与「知识图谱·语义检索改造 2026-09-26」两条因正文被更新
而被跳过，导致评测用例 desc-009（「记忆检索怎么老是找不到我真正要的东西」期望
「知识图谱_检索引擎」）在所有查询写法下都进不了前 200 名——那是一次假失败，
把模型的实际能力测低了。宁可让首次调用多花一次编码时间，也不能让记忆失效。

不经过切词、不经过字面匹配、不与其他臂融合：语义检索单独可用，才谈得上与
字面/融合链路做对照。
"""
from __future__ import annotations

import asyncio
import json
import logging

from . import embedding as emb
from .db import _connect
from .recall import _fingerprint_of, _load_all_entities, _parse_embedding_cell, refresh_embeddings

logger = logging.getLogger(__name__)


async def _reload(names: list[str]) -> dict[str, list[float]]:
    """重算指定实体的向量并写回，返回成功重算的 {name: vector}。

    复用 recall.refresh_embeddings（带锁、只算指纹不符的），避免两套自愈逻辑
    各写各的。返回的向量直接用于本次检索，省去再读一次库。
    """
    if not names:
        return {}
    try:
        stats = await refresh_embeddings(names)
    except Exception as exc:  # 自愈失败不能影响本次检索，如实记日志即可
        logger.warning("lazy embedding refresh failed: %s", exc)
        return {}
    if stats.get("available") is False:
        logger.warning("lazy embedding refresh skipped: %s", stats.get("error"))

    # 重算后从库里取回这批的向量
    db = await _connect()
    placeholders = ",".join("?" for _ in names)
    cur = await db.execute(
        f"SELECT name, type, content, relations, importance, pinned, embedding "
        f"FROM entities WHERE deprecated_at IS NULL AND name IN ({placeholders})",
        tuple(names),
    )
    refreshed: dict[str, list[float]] = {}
    for row in await cur.fetchall():
        vector, stored_fp = _parse_embedding_cell(row["embedding"])
        if vector is None or stored_fp != _fingerprint_of(row):
            continue
        refreshed[row["name"]] = vector
    return refreshed


async def semantic_search(
    query: str,
    top_k: int = 10,
    min_cosine: float = 0.0,
    with_diagnostics: bool = True,
) -> list[dict]:
    """语义检索：原始文本 → 全库余弦降序。

    query 不做任何预处理，直接进模型（模型自带 tokenizer）。
    min_cosine 默认 0.0 表示不过滤；线上三档为 0.45 / 0.52 / 0.60，
    需要复现线上行为时显式传入。

    模型不可用时返回 [{"error": ..., "model_available": False}]，不返回空数组：
    空数组会被调用方误读成「确实没有相关记忆」，那正是要避免的静默降级。
    """
    if not query or not query.strip():
        return []

    if not await asyncio.to_thread(emb.is_available):
        reason = emb.last_error() or "embedding model unavailable"
        logger.warning("semantic_search unavailable: %s", reason)
        return [{"error": reason, "model_available": False}]

    query_vector = await asyncio.to_thread(emb.encode_one, query.strip())
    if not query_vector:
        reason = emb.last_error() or "查询编码失败"
        return [{"error": reason, "model_available": False}]

    rows = await _load_all_entities()
    meta: dict[str, dict] = {}
    needs_refresh: list[str] = []
    refreshed: dict[str, list[float]] = {}
    failed: list[str] = []

    for row in rows:
        vector, stored_fp = _parse_embedding_cell(row["embedding"])
        if vector is not None and stored_fp == _fingerprint_of(row):
            meta[row["name"]] = {"row": row, "vector": vector}
            continue
        # 向量缺失或指纹过期（正文被改过）：标记自愈，本次一并纳入
        needs_refresh.append(row["name"])

    if needs_refresh:
        refreshed = await _reload(needs_refresh)
        by_name = {r["name"]: r for r in rows}
        for name, vector in refreshed.items():
            row = by_name.get(name)
            if row is not None:
                meta[name] = {"row": row, "vector": vector}
        failed = [n for n in needs_refresh if n not in refreshed]
        if failed:
            logger.warning("semantic_search: %d 条自愈失败被跳过: %s", len(failed), failed[:5])

    names = list(meta.keys())
    matrix = [meta[n]["vector"] for n in names]

    scored: list[tuple[str, float]] = []
    if matrix:
        sims = await asyncio.to_thread(emb.cosine_matrix, query_vector, matrix)
        for name, sim in zip(names, sims):
            if sim >= min_cosine:
                scored.append((name, float(sim)))
    scored.sort(key=lambda item: item[1], reverse=True)

    results: list[dict] = []
    for rank, (name, sim) in enumerate(scored[: max(top_k, 0)], 1):
        row = meta[name]["row"]
        entry: dict = {
            "entity": name,
            "type": row["type"],
            "importance": row["importance"],
            "pinned": bool(row["pinned"]),
            "cosine": round(sim, 4),
        }
        if with_diagnostics:
            entry["arms"] = {"vector": {"rank": rank, "cosine": round(sim, 4)}}
        results.append(entry)

    if with_diagnostics and results:
        results[0]["vector_arm"] = {
            "model": emb.MODEL_NAME,
            "dim": emb.EMBEDDING_DIM,
            "scored": len(names),
            "refreshed": len(refreshed),
            "refresh_needed": len(needs_refresh),
            "refresh_failed": len(failed),
            "filtered_out": len(names) - len(scored),
            "min_cosine": min_cosine,
            "model_available": True,
            "last_error": None,
        }
    return results
