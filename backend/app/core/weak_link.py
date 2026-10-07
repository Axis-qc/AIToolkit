"""弱关联候选生成（干跑）—— 用实体向量补「相关」类弱关系。

本模块只读：不改 entities.relations、不写 relation_index、不建表。
唯一例外是正文指纹过期的实体会就地重算向量并写回 embedding 列，与
recall / semantic_search 的自愈同机制；不这样做的话，刚被改过正文的实体
会在本次计算里静默缺席，结果偏小且看不出原因。

不能把自动边写进 entities.relations，三个机制层面的原因：
  1. build_entity_text 会把 relations 的目标名编进向量文本，写入会连锁触发
     全库内容指纹失效与向量重算，检索结果随之整体漂移。
  2. update_memory 的 relations 是替换语义，一次人工写入即抹除全部自动边。
  3. refresh_relation_index 对 is_root=1 的实体直接跳过，其出边会被静默丢弃。

规则：取每个实体的前 top_k 个最近邻（并集）作候选，按余弦阈值过滤，剔除已有
手写关系，把疑似重复的实体对分流到重复清单，最后做度数上限截断。

余弦只能表达「像」，表达不了关系类型。实测手写「属于」边的对方在源实体余弦
榜里中位数排第 1（87.3% 进前 10），而「包含」边的对方中位数排第 283
（仅 10.3% 进前 10）。因此本模块只产出无语义的「相关」，不复用现有关系类型。
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections import Counter
from pathlib import Path

from . import embedding as emb
from .config import settings
from .db import _connect, now_ts
from .graph_health import _cosine, _n_grams, _name_similarity
from .recall import _fingerprint_of, _load_all_entities, _parse_embedding_cell, refresh_embeddings

logger = logging.getLogger(__name__)

BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
REPORT_PATH = BACKEND_DIR / "data" / "weak_link_report.json"

# 供选阈值扫描用的档位。低于 0.60 的档位不列：实测 0.55 档产出 75885 条边、
# 平均度 55.5、最大度 552，会把图冲垮，没有可用的可能。
SCAN_THRESHOLDS = (0.60, 0.65, 0.70, 0.75, 0.80)


def _pair(i: int, j: int) -> tuple[int, int]:
    """无向对的规范序，保证同一对只有一种表示。"""
    return (i, j) if i < j else (j, i)


def _scan(
    names: list[str],
    types: list[str],
    contents: list[str],
    importance: list[int],
    vectors: list[list[float]],
    manual_name_pairs: set[tuple[str, str]],
    top_k: int,
    min_cosine: float,
    auto_cosine: float,
    degree_cap: int,
    dup_content_cosine: float,
    dup_name_similarity: float,
) -> dict:
    """纯计算部分，不碰 SQLite，整体丢进 asyncio.to_thread 执行。

    手写边以名字对形式传入、在过滤之后再映射成下标：零向量实体被剔除后
    下标空间会整体错位，用下标对会在过滤时静默丢边。
    """
    import numpy as np

    n = len(names)
    matrix = np.asarray(vectors, dtype=np.float32)
    norms = np.linalg.norm(matrix, axis=1)
    keep = norms > 0
    if not keep.all():
        matrix = matrix[keep]
        names = [x for x, k in zip(names, keep) if k]
        types = [x for x, k in zip(types, keep) if k]
        contents = [x for x, k in zip(contents, keep) if k]
        importance = [x for x, k in zip(importance, keep) if k]
        n = len(names)

    index = {nm: i for i, nm in enumerate(names)}
    manual_pairs: set[tuple[int, int]] = set()
    for a, b in manual_name_pairs:
        ia, ib = index.get(a), index.get(b)
        if ia is not None and ib is not None and ia != ib:
            manual_pairs.add(_pair(ia, ib))

    unit = matrix / np.linalg.norm(matrix, axis=1, keepdims=True)
    sim = unit @ unit.T
    np.fill_diagonal(sim, -1.0)

    order = np.argsort(-sim, axis=1)[:, : max(1, top_k)]

    grams = [_n_grams(c) for c in contents]
    gram_norms = [float(sum(v * v for v in g.values()) ** 0.5) for g in grams]

    dup_cache: dict[tuple[int, int], dict] = {}

    def dup_signal(i: int, j: int) -> dict:
        key = _pair(i, j)
        hit = dup_cache.get(key)
        if hit is not None:
            return hit
        content_cos = _cosine(grams[i], grams[j], gram_norms[i], gram_norms[j]) if i != j else 0.0
        name_sim = _name_similarity(names[i], names[j])
        info = {
            "content_cosine": round(content_cos, 4),
            "name_similarity": round(name_sim, 4),
            "is_duplicate_like": content_cos >= dup_content_cosine or name_sim >= dup_name_similarity,
        }
        dup_cache[key] = info
        return info

    def candidates(th: float) -> set[tuple[int, int]]:
        found: set[tuple[int, int]] = set()
        for i in range(n):
            for j in order[i]:
                j = int(j)
                if sim[i, j] >= th:
                    found.add(_pair(i, j))
        return found

    def degree_of(pairs) -> "np.ndarray":
        deg = np.zeros(n, dtype=np.int32)
        for a, b in pairs:
            deg[a] += 1
            deg[b] += 1
        return deg

    def entry(a: int, b: int, extra: dict | None = None) -> dict:
        item = {
            "a": names[a],
            "type_a": types[a],
            "b": names[b],
            "type_b": types[b],
            "cosine": round(float(sim[a, b]), 4),
            "same_type": types[a] == types[b],
        }
        if extra:
            item.update(extra)
        return item

    scan: list[dict] = []
    for th in SCAN_THRESHOLDS:
        cand = candidates(th)
        novel = cand - manual_pairs
        dup_items, real_pairs = [], []
        for a, b in novel:
            signal = dup_signal(a, b)
            if signal["is_duplicate_like"]:
                dup_items.append(entry(a, b, signal))
            else:
                real_pairs.append((a, b))

        real_pairs.sort(key=lambda p: -float(sim[p[0], p[1]]))
        capped: list[tuple[int, int]] = []
        deg = np.zeros(n, dtype=np.int32)
        for a, b in real_pairs:
            if deg[a] >= degree_cap or deg[b] >= degree_cap:
                continue
            deg[a] += 1
            deg[b] += 1
            capped.append((a, b))

        auto = [p for p in capped if float(sim[p[0], p[1]]) >= auto_cosine]
        pending = [p for p in capped if float(sim[p[0], p[1]]) < auto_cosine]
        deg_final = degree_of(capped)

        scan.append({
            "threshold": th,
            "candidates": len(cand),
            "events": len(novel),
            "duplicate_routed": len(dup_items),
            "novel_real_pairs": len(real_pairs),
            "after_degree_cap": len(capped),
            "dropped_by_degree_cap": len(real_pairs) - len(capped),
            "auto_band": len(auto),
            "pending_band": len(pending),
            "recovers_manual_pct": round(
                100.0 * len(cand & manual_pairs) / max(1, len(manual_pairs)), 1),
            "nodes_with_new_edge": int((deg_final > 0).sum()),
            "nodes_still_isolated": int((deg_final == 0).sum()),
            "avg_degree": round(float(deg_final.sum()) / max(1, n), 2),
            "max_degree": int(deg_final.max()) if n else 0,
        })

    # 推荐档：取 min_cosine 这一档，产出完整清单
    cand = candidates(min_cosine)
    novel = cand - manual_pairs
    duplicates, real_pairs = [], []
    for a, b in novel:
        signal = dup_signal(a, b)
        if signal["is_duplicate_like"]:
            duplicates.append(entry(a, b, signal))
        else:
            real_pairs.append((a, b))

    real_pairs.sort(key=lambda p: -float(sim[p[0], p[1]]))
    capped = []
    deg = np.zeros(n, dtype=np.int32)
    for a, b in real_pairs:
        if deg[a] >= degree_cap or deg[b] >= degree_cap:
            continue
        deg[a] += 1
        deg[b] += 1
        capped.append((a, b))

    link_auto = [entry(a, b) for a, b in capped if float(sim[a, b]) >= auto_cosine]
    link_pending = [entry(a, b) for a, b in capped if float(sim[a, b]) < auto_cosine]

    link_auto.sort(key=lambda x: -x["cosine"])
    link_pending.sort(key=lambda x: -x["cosine"])
    duplicates.sort(key=lambda x: -x["cosine"])

    deg_final = degree_of(capped)
    ranked = np.argsort(-deg_final)[:15]
    hubs = [
        {
            "name": names[int(i)],
            "type": types[int(i)],
            "importance": int(importance[int(i)]),
            "degree": int(deg_final[int(i)]),
        }
        for i in ranked
        if deg_final[int(i)] > 0
    ]

    type_counter = Counter(types[int(a)] for a, _ in capped)
    return {
        "entities_scanned": n,
        "manual_undirected": len(manual_pairs),
        "threshold_scan": scan,
        "recommended": {
            "top_k": top_k,
            "min_cosine": min_cosine,
            "auto_cosine": auto_cosine,
            "degree_cap": degree_cap,
            "candidates": len(cand),
            "novel_after_excluding_manual": len(novel),
            "duplicate_routed": len(duplicates),
            "novel_real_pairs": len(real_pairs),
            "after_degree_cap": len(capped),
            "auto_band": len(link_auto),
            "pending_band": len(link_pending),
            "recovers_manual_pct": round(
                100.0 * len(cand & manual_pairs) / max(1, len(manual_pairs)), 1),
            "nodes_with_new_edge": int((deg_final > 0).sum()),
            "nodes_still_isolated": int((deg_final == 0).sum()),
            "avg_degree": round(float(deg_final.sum()) / max(1, n), 2),
            "max_degree": int(deg_final.max()) if n else 0,
            "same_type_pct": round(
                100.0 * sum(1 for x in link_auto + link_pending if x["same_type"])
                / max(1, len(link_auto) + len(link_pending)), 1),
            "node_type_of_new_edges": [
                {"type": t, "n": c} for t, c in type_counter.most_common()
            ],
        },
        "link_auto": link_auto,
        "link_pending": link_pending,
        "duplicate_candidates": duplicates,
        "hubs": hubs,
    }


async def build_report(
    top_k: int | None = None,
    min_cosine: float | None = None,
    auto_cosine: float | None = None,
    degree_cap: int | None = None,
) -> dict:
    """生成弱关联候选全量报告（不写库）。

    返回完整清单；调用方如需回给模型，走 summarize() 裁剪。
    """
    top_k = top_k if top_k is not None else settings.weak_link_top_k
    min_cosine = min_cosine if min_cosine is not None else settings.weak_link_min_cosine
    auto_cosine = auto_cosine if auto_cosine is not None else settings.weak_link_auto_cosine
    degree_cap = degree_cap if degree_cap is not None else settings.weak_link_degree_cap
    dup_content_cosine = settings.weak_link_dup_content_cosine
    dup_name_similarity = settings.weak_link_dup_name_similarity

    if not await asyncio.to_thread(emb.is_available):
        return {
            "error": emb.last_error() or "embedding model unavailable",
            "model_available": False,
        }

    # 自愈：指纹过期的实体就地重算，否则会在本次计算里静默缺席
    heal = await refresh_embeddings()
    rows = await _load_all_entities()

    names, types, contents, importance, vectors = [], [], [], [], []
    skipped: list[str] = []
    for row in rows:
        vector, stored_fp = _parse_embedding_cell(row["embedding"])
        if vector is None or stored_fp != _fingerprint_of(row):
            skipped.append(row["name"])
            continue
        names.append(row["name"])
        types.append(row["type"])
        contents.append(row["content"] or "")
        importance.append(row["importance"])
        vectors.append(vector)

    db = await _connect()
    cur = await db.execute(
        "SELECT entity_name, target_name, rel_type FROM relation_index "
        "WHERE deprecated_at IS NULL"
    )
    raw_edges = await cur.fetchall()
    manual_name_pairs: set[tuple[str, str]] = set()
    dropped_endpoint = 0
    rel_type_counter: Counter = Counter()
    scanned_names = set(names)
    for row in raw_edges:
        a, b = row["entity_name"], row["target_name"]
        if a not in scanned_names or b not in scanned_names or a == b:
            dropped_endpoint += 1
            continue
        manual_name_pairs.add((a, b) if a < b else (b, a))
        rel_type_counter[row["rel_type"]] += 1

    cur = await db.execute(
        "SELECT COUNT(*) AS c FROM entities WHERE deprecated_at IS NULL"
    )
    active_entities = (await cur.fetchone())["c"]

    scan = await asyncio.to_thread(
        _scan,
        names, types, contents, importance, vectors, manual_name_pairs,
        top_k, min_cosine, auto_cosine, degree_cap,
        dup_content_cosine, dup_name_similarity,
    )

    return {
        "mode": "dry_run",
        "generated_at": now_ts(),
        "config": {
            "top_k": top_k,
            "min_cosine": min_cosine,
            "auto_cosine": auto_cosine,
            "degree_cap": degree_cap,
            "dup_content_cosine": dup_content_cosine,
            "dup_name_similarity": dup_name_similarity,
        },
        "vector_status": {
            "active_entities": active_entities,
            "entities_scanned": scan["entities_scanned"],
            "stale_or_missing_after_heal": len(skipped),
            "stale_names": skipped[:50],
            "heal": {k: v for k, v in heal.items() if k != "error"},
            "heal_error": heal.get("error"),
        },
        "graph_now": {
            "active_entities": active_entities,
            "manual_edges_raw": len(raw_edges),
            "manual_edges_undirected": scan["manual_undirected"],
            "edges_with_endpoint_outside_vector_set": dropped_endpoint,
            "relation_types": [
                {"rel": r, "n": c} for r, c in rel_type_counter.most_common()
            ],
        },
        "threshold_scan": scan["threshold_scan"],
        "recommended": scan["recommended"],
        "link_auto": scan["link_auto"],
        "link_pending": scan["link_pending"],
        "duplicate_candidates": scan["duplicate_candidates"],
        "hubs": scan["hubs"],
    }


def summarize(report: dict, sample: int = 15) -> dict:
    """裁剪大清单，供直接回给调用方；完整内容在报告文件里。"""
    if report.get("error"):
        return report
    rec = report.get("recommended", {})
    trim = lambda items: items[:sample]
    return {
        "mode": report.get("mode"),
        "generated_at": report.get("generated_at"),
        "config": report.get("config"),
        "vector_status": report.get("vector_status"),
        "graph_now": report.get("graph_now"),
        "threshold_scan": report.get("threshold_scan"),
        "recommended": rec,
        "hubs": report.get("hubs"),
        "samples": {
            "link_auto_top": trim(report.get("link_auto", [])),
            "link_pending_top": trim(report.get("link_pending", [])),
            "duplicate_candidates_top": trim(report.get("duplicate_candidates", [])),
        },
        "report_path": str(REPORT_PATH),
    }


def write_report(report: dict, path: Path | None = None) -> str:
    """把完整报告写到 JSON 文件，返回路径。"""
    target = path or REPORT_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    return str(target)


async def weak_links(
    top_k: int | None = None,
    min_cosine: float | None = None,
    auto_cosine: float | None = None,
    degree_cap: int | None = None,
    write: bool = True,
    full: bool = False,
) -> dict:
    """干跑入口：算出弱关联候选并写报告文件。

    full=True 返回全量清单；否则只回摘要与样本。
    """
    report = await build_report(
        top_k=top_k, min_cosine=min_cosine,
        auto_cosine=auto_cosine, degree_cap=degree_cap,
    )
    if report.get("error"):
        return report

    if write:
        report["report_path"] = write_report(report)

    result = report if full else summarize(report)
    result["notes"] = [
        "本结果为干跑：未写 entities.relations、未写 relation_index、未建表。",
        "cosine 只表达「像」，表达不了关系类型；该通道只产出无语义的「相关」。",
        "疑似重复的实体对已分流到 duplicate_candidates，不进建边清单。",
        "同一对实体若两个方向的 top-k 命中，只计一次（无向）。",
    ]
    return result
