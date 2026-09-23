"""
知识图谱体检引擎 —— 一次调用收掉重复、过时、类型碎片、悬空关系、lint 五类问题。

只读模块：不修改任何数据，只输出带证据字段的清单供人工确认。
重复检测以 content 为主信号（字符二元组余弦相似度），名字相似度只作辅助证据，
因为实测名字相似度会把「AIToolkit前端架构 / AIToolkit后端架构」这类
完全不同的事判成 0.92 相似。
"""
import json
import math
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone

from .config import settings
from .db import _connect, parse_ts, retention_hours

# ── lint 规则 ──────────────────────────────────────────

ENV_PREFIXES = ("【WorkBuddy】", "【DSH】", "【本机】", "【通用】")

# 正文里不该出现的装饰符号。分两级：
# hard = 规范明确禁止且无技术含义的装饰，直接算违规
# review = 可能是技术记号（如事件链 950→951），需逐条人工判断
_HARD_DECOR = {
    "markdown粗体": re.compile(r"\*\*"),
    "markdown标题": re.compile(r"^#{1,6}\s", re.M),
    "emoji": re.compile("[\U0001F300-\U0001FAFF\uFE0F]"),
    "警告三角": re.compile("[\u26A0\u2757\u2753\u203C]"),
    "星标勾叉": re.compile("[\u2605\u2606\u2705\u274C\u2714\u2716\u2726\u2727]"),
    "几何装饰": re.compile("[\u25A0-\u25FF\u2B1B\u2B1C]"),
}
_REVIEW_DECOR = {
    "箭头": re.compile("[\u2192\u2190\u2191\u2193\u21D2\u2794]"),
}

# 开头的【…】标注：内文不限长度（实测存在【DSH stellaris-mod 仓库】这类长标注），
# 但不跨行、设一个合理上限避免把正文里的括号误判成前缀
_ANY_BRACKET_MARK = re.compile(r"^\s*【[^】\n]{1,60}】")


def _n_grams(text: str, n: int = 2) -> Counter:
    """字符 n-gram 频次向量（小写归一）。"""
    text = text.lower()
    if len(text) < n:
        return Counter()
    return Counter(text[i:i + n] for i in range(len(text) - n + 1))


def _cosine(a: Counter, b: Counter, norm_a: float, norm_b: float) -> float:
    """两个 n-gram 频次向量的余弦相似度。"""
    if norm_a == 0 or norm_b == 0:
        return 0.0
    small, big = (a, b) if len(a) < len(b) else (b, a)
    dot = sum(cnt * big[gram] for gram, cnt in small.items() if gram in big)
    return dot / (norm_a * norm_b)


def _name_similarity(a: str, b: str) -> float:
    """名字相似度（辅助信号，仅用于给重复候选补充证据）。"""
    import difflib
    return round(difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio(), 4)


# ── 重复候选 ───────────────────────────────────────────


async def find_duplicate_candidates(limit: int | None = None) -> dict:
    """以 content 为主信号找重复候选，输出高置信与待观察两档。

    度量口径：字符二元组余弦相似度。选它是因为实测该口径下
    三对真候选（航母核心技能对、AIToolkit 架构壳对、火力/防御核心对）
    正好排在全局前三，而名字相似度会在同样阈值下带出大量无关项。
    """
    db = await _connect()
    cur = await db.execute(
        "SELECT name, type, content, importance, pinned FROM entities "
        "WHERE deprecated_at IS NULL"
    )
    rows = await cur.fetchall()

    docs = []
    for r in rows:
        content = (r["content"] or "").strip()
        if content:
            docs.append((r["name"], r["type"], content, r["importance"], bool(r["pinned"])))

    high_th = settings.dup_similarity_threshold
    watch_th = settings.dup_watch_threshold

    vectors = [_n_grams(c) for _, _, c, _, _ in docs]
    norms = [math.sqrt(sum(v * v for v in vec.values())) for vec in vectors]

    high: list[dict] = []
    watch: list[dict] = []
    for i in range(len(docs)):
        for j in range(i + 1, len(docs)):
            sim = _cosine(vectors[i], vectors[j], norms[i], norms[j])
            if sim < watch_th:
                continue
            name_i, type_i, content_i, imp_i, pin_i = docs[i]
            name_j, type_j, content_j, imp_j, pin_j = docs[j]
            item = {
                "entity_a": name_i,
                "entity_b": name_j,
                "type_a": type_i,
                "type_b": type_j,
                "type_match": type_i == type_j,
                "similarity": round(sim, 4),
                "name_similarity": _name_similarity(name_i, name_j),
                "length_a": len(content_i),
                "length_b": len(content_j),
                "importance_a": imp_i,
                "importance_b": imp_j,
                "pinned_a": pin_i,
                "pinned_b": pin_j,
                "content_preview_a": content_i[:160],
                "content_preview_b": content_j[:160],
                "evidence": (
                    f"content 余弦相似度 {sim:.3f}（阈值 {watch_th}），"
                    f"名字相似度 {_name_similarity(name_i, name_j):.3f}"
                ),
            }
            if sim >= high_th:
                item["tier"] = "high"
                high.append(item)
            else:
                item["tier"] = "watch"
                watch.append(item)

    high.sort(key=lambda x: x["similarity"], reverse=True)
    watch.sort(key=lambda x: x["similarity"], reverse=True)

    result = {
        "metric": "char_bigram_cosine",
        "high_threshold": high_th,
        "watch_threshold": watch_th,
        "compared_entities": len(docs),
        "entities_without_content": len(rows) - len(docs),
        "high_confidence": high,
        "high_confidence_count": len(high),
        "watch": watch,
        "watch_count": len(watch),
    }
    if limit:
        result["high_confidence"] = high[:limit]
        result["watch"] = watch[:limit]
    return result


# ── 过时台账 ───────────────────────────────────────────


async def find_stale_entries() -> dict:
    """过时台账：按 90/180 两档分组，并单独列出时间戳缺失与格式非法的实体。

    日期一律走 parse_ts 解析（兼容冒号格式与历史 ISO 格式），
    不直接做字符串比较，因为库里的时间戳格式已经分裂。
    有结构化 verified_until 的按它判定，否则取 updated_at，再回退 created_at。
    """
    db = await _connect()
    cur = await db.execute(
        "SELECT name, type, importance, pinned, content, created_at, updated_at, "
        "stale_marked_at, verified_until FROM entities WHERE deprecated_at IS NULL"
    )
    rows = await cur.fetchall()

    now = datetime.now(timezone.utc)
    warn_days = settings.stale_days_warn
    alert_days = settings.stale_days_alert

    missing_ts: list[dict] = []
    invalid_ts: list[dict] = []
    over_alert: list[dict] = []
    over_warn: list[dict] = []
    fresh = 0

    for r in rows:
        created, updated = r["created_at"], r["updated_at"]
        dt_created, dt_updated = parse_ts(created), parse_ts(updated)

        # 两个字段都空 → 时间戳缺失，任何日期规则都覆盖不到
        if not (created or "").strip() and not (updated or "").strip():
            missing_ts.append({
                "name": r["name"], "type": r["type"], "content_preview": (r["content"] or "")[:80],
                "evidence": "created_at 与 updated_at 均为空，无法判定新旧",
            })
            continue

        # 有值但解析不了 → 格式非法，单列，不混进过时数
        if (created or "").strip() and dt_created is None:
            invalid_ts.append({
                "name": r["name"], "type": r["type"], "field": "created_at", "value": created,
                "evidence": f"created_at 取值 {created!r} 不符合任何已知时间戳格式",
            })
        if (updated or "").strip() and dt_updated is None:
            invalid_ts.append({
                "name": r["name"], "type": r["type"], "field": "updated_at", "value": updated,
                "evidence": f"updated_at 取值 {updated!r} 不符合任何已知时间戳格式",
            })

        verified_dt = parse_ts(r["verified_until"])
        if verified_dt:
            reference, source_field, reference_value = verified_dt, "verified_until", r["verified_until"]
        elif dt_updated:
            reference, source_field, reference_value = dt_updated, "updated_at", updated
        else:
            reference, source_field, reference_value = dt_created, "created_at", created
        age_days = (now - reference).days

        entry = {
            "name": r["name"],
            "type": r["type"],
            "importance": r["importance"],
            "pinned": bool(r["pinned"]),
            "reference_field": source_field,
            "reference_value": reference_value,
            "age_days": age_days,
            "stale_marked_at": r["stale_marked_at"],
            "verified_until": r["verified_until"],
            "content_preview": (r["content"] or "")[:120],
            "evidence": f"{source_field}={reference_value}，距今 {age_days} 天",
        }

        if age_days > alert_days:
            over_alert.append(entry)
        elif age_days > warn_days:
            over_warn.append(entry)
        else:
            fresh += 1

    over_alert.sort(key=lambda x: x["age_days"], reverse=True)
    over_warn.sort(key=lambda x: x["age_days"], reverse=True)

    return {
        "warn_days": warn_days,
        "alert_days": alert_days,
        "total_entities": len(rows),
        "over_alert": over_alert,
        "over_alert_count": len(over_alert),
        "over_warn": over_warn,
        "over_warn_count": len(over_warn),
        "fresh_count": fresh,
        "missing_timestamp": missing_ts,
        "missing_timestamp_count": len(missing_ts),
        "invalid_timestamp": invalid_ts,
        "invalid_timestamp_count": len(invalid_ts),
        "note": (
            "时间戳缺失的实体不参与日期判定，需先补时间戳；"
            "带 verified_until 的按核实截止日判定，优先级高于 updated_at。"
        ),
    }


# ── 类型碎片 ───────────────────────────────────────────


async def find_type_fragments() -> dict:
    """类型碎片：近似类型对、单例类型、仅大小写差异的变体。"""
    db = await _connect()
    cur = await db.execute(
        "SELECT type, COUNT(*) AS cnt FROM entities WHERE deprecated_at IS NULL "
        "GROUP BY type ORDER BY cnt DESC"
    )
    rows = await cur.fetchall()
    counts = {r["type"]: r["cnt"] for r in rows}

    # 仅大小写差异
    by_lower: dict[str, list[str]] = defaultdict(list)
    for t in counts:
        by_lower[t.lower()].append(t)
    case_variants = [
        {
            "variants": sorted(v, key=lambda x: -counts[x]),
            "counts": {x: counts[x] for x in sorted(v, key=lambda x: -counts[x])},
            "suggested_keep": max(v, key=lambda x: counts[x]),
            "total": sum(counts[x] for x in v),
            "evidence": "类型名仅大小写不同，建议统一为 " + max(v, key=lambda x: counts[x]),
        }
        for v in by_lower.values() if len(v) > 1
    ]

    # 近似类型（中英混用 / 单复数 / 子串关系）
    aliases = _KNOWN_TYPE_ALIASES(counts)

    singletons = [
        {"type": r["type"], "count": r["cnt"]}
        for r in rows if r["cnt"] == 1
    ]

    return {
        "type_count": len(counts),
        "types": [{"type": r["type"], "count": r["cnt"]} for r in rows],
        "case_variants": case_variants,
        "case_variant_count": len(case_variants),
        "known_alias_groups": aliases,
        "singleton_types": singletons,
        "singleton_count": len(singletons),
        "note": "归并类型属于结构性变更，需人工确认后走批量写入口执行。",
    }


def _KNOWN_TYPE_ALIASES(counts: dict[str, int]) -> list[dict]:
    """已知的类型碎片组（中英对照与同义写法），只在两侧都存在时报出。"""
    groups = [
        ("技术知识点", "技术知识"),
        ("rule", "规则"),
        ("Project", "项目"),
        ("ProjectDecision", "Decision"),
        ("document", "Document"),
        ("环境事实", "Environment", "环境坑"),
        ("technical", "技术知识点"),
        ("Feature", "功能"),
    ]
    result = []
    for group in groups:
        present = [t for t in group if t in counts]
        if len(present) > 1:
            result.append({
                "variants": present,
                "counts": {t: counts[t] for t in present},
                "suggested_keep": max(present, key=lambda x: counts[x]),
                "total": sum(counts[t] for t in present),
                "evidence": "同一概念的类型写法分裂，建议合并为 "
                            + max(present, key=lambda x: counts[x]),
            })
    return result


# ── 悬空关系 ───────────────────────────────────────────


async def find_dangling_relations() -> dict:
    """悬空关系：指向不存在（或已软删）实体的关系索引。

    这些边被 get_full_graph 的 JOIN 静默丢弃，导致 REST 与库里条数不一致。
    """
    db = await _connect()
    cur = await db.execute("""
        SELECT ri.entity_name, ri.target_name, ri.rel_type
        FROM relation_index ri
        WHERE ri.deprecated_at IS NULL
          AND (
            NOT EXISTS (
                SELECT 1 FROM entities e
                WHERE e.name = ri.target_name AND e.deprecated_at IS NULL
            )
            OR NOT EXISTS (
                SELECT 1 FROM entities e
                WHERE e.name = ri.entity_name AND e.deprecated_at IS NULL
            )
          )
    """)
    rows = await cur.fetchall()

    cur = await db.execute("SELECT name, deprecated_at FROM entities")
    entity_state = {r["name"]: r["deprecated_at"] for r in await cur.fetchall()}

    items = []
    for r in rows:
        target_exists = r["target_name"] in entity_state
        source_exists = r["entity_name"] in entity_state
        if not target_exists:
            missing, reason = r["target_name"], "目标实体在实体表中完全不存在"
        elif not source_exists:
            missing, reason = r["entity_name"], "源实体在实体表中完全不存在"
        elif entity_state[r["target_name"]] is not None:
            missing, reason = r["target_name"], "目标实体已软删除，关系未同步失效"
        else:
            missing, reason = r["entity_name"], "源实体已软删除，关系未同步失效"
        items.append({
            "from": r["entity_name"],
            "to": r["target_name"],
            "rel_type": r["rel_type"],
            "missing_side": missing,
            "reason": reason,
            "evidence": f"{r['entity_name']} → {r['target_name']}（{r['rel_type']}）：{reason}",
        })

    cur = await db.execute(
        "SELECT COUNT(*) AS c FROM relation_index WHERE deprecated_at IS NULL"
    )
    total_active = (await cur.fetchone())["c"]

    return {
        "active_relations": total_active,
        "visible_in_rest": total_active - len(items),
        "dangling": items,
        "dangling_count": len(items),
        "involved_names": sorted({x["missing_side"] for x in items}),
        "involved_name_count": len({x["missing_side"] for x in items}),
        "note": "悬空边不会出现在 /api/graph，需决定是补建实体还是清掉索引。",
    }


# ── lint ───────────────────────────────────────────────


async def run_lint() -> dict:
    """lint 违规：content 必填、环境前缀、装饰符号。

    前缀分两类报：完全没有任何【】标注的算违规；
    以【Stellaris】等其他标注开头的归为「非规范前缀」，不混进违规数。
    """
    db = await _connect()
    cur = await db.execute(
        "SELECT name, type, content, importance, pinned FROM entities WHERE deprecated_at IS NULL"
    )
    rows = await cur.fetchall()

    empty_content: list[dict] = []
    missing_prefix: list[dict] = []
    nonstandard_prefix: list[dict] = []
    decoration: list[dict] = []
    decoration_review: list[dict] = []

    for r in rows:
        content = r["content"] or ""
        stripped = content.strip()

        if not stripped:
            empty_content.append({
                "name": r["name"], "type": r["type"],
                "evidence": "content 为空或仅空白，违反 content 必填",
            })
            continue

        if not stripped.startswith(ENV_PREFIXES):
            other = _ANY_BRACKET_MARK.match(stripped)
            if other:
                nonstandard_prefix.append({
                    "name": r["name"], "type": r["type"],
                    "prefix": other.group(0).strip(),
                    "evidence": f"开头是 {other.group(0).strip()}，不在四个规范前缀内",
                })
            else:
                missing_prefix.append({
                    "name": r["name"], "type": r["type"],
                    "head": stripped[:40],
                    "evidence": f"content 开头 40 字为 {stripped[:40]!r}，缺环境前缀",
                })

        hard_hits = []
        for label, pattern in _HARD_DECOR.items():
            m = pattern.search(content)
            if m:
                hard_hits.append({"kind": label, "matched": m.group(0), "position": m.start()})
        if hard_hits:
            decoration.append({
                "name": r["name"], "type": r["type"], "violations": hard_hits,
                "evidence": "命中装饰符号：" + "、".join(
                    f"{h['kind']}({h['matched']!r}@{h['position']})" for h in hard_hits
                ),
            })

        soft_hits = []
        for label, pattern in _REVIEW_DECOR.items():
            m = pattern.search(content)
            if m:
                soft_hits.append({"kind": label, "matched": m.group(0), "position": m.start()})
        if soft_hits:
            decoration_review.append({
                "name": r["name"], "type": r["type"], "violations": soft_hits,
                "evidence": "命中箭头，需判断是装饰还是链路记法（如 950→951）：" + "、".join(
                    f"{h['matched']!r}@{h['position']}" for h in soft_hits
                ),
            })

    return {
        "total_entities": len(rows),
        "empty_content": empty_content,
        "empty_content_count": len(empty_content),
        "missing_prefix": missing_prefix,
        "missing_prefix_count": len(missing_prefix),
        "nonstandard_prefix": nonstandard_prefix,
        "nonstandard_prefix_count": len(nonstandard_prefix),
        "decoration": decoration,
        "decoration_count": len(decoration),
        "decoration_review": decoration_review,
        "decoration_review_count": len(decoration_review),
        "rules": {
            "content_required": "content 必填",
            "env_prefix": list(ENV_PREFIXES),
            "no_decoration": list(_HARD_DECOR.keys()),
            "decoration_needs_review": list(_REVIEW_DECOR.keys()),
        },
        "notes": [
            "前缀分两类：完全没有【】标注的计入 missing_prefix；"
            "以【Stellaris】等其他标注开头的计入 nonstandard_prefix，不混进违规数。",
            "decoration 是硬违规（emoji、星号加粗、星标勾叉等）；"
            "decoration_review 里的箭头包含「950→951」这类事件链/字段链记法，"
            "属于技术记号而非装饰，需逐条判断。",
        ],
    }


# ── 体检入口 ───────────────────────────────────────────


ALL_CHECKS = ("duplicates", "stale", "types", "dangling", "lint")


async def health_check(checks: list[str] | None = None) -> dict:
    """图谱体检：一次调用返回五类问题的清单，每条都带可判定证据。

    checks 为空则全部执行。可选值：duplicates / stale / types / dangling / lint。
    只读，不修改任何数据。
    """
    requested = [c for c in (checks or ALL_CHECKS) if c in ALL_CHECKS]
    unknown = [c for c in (checks or []) if c not in ALL_CHECKS]

    db = await _connect()
    cur = await db.execute("SELECT COUNT(*) AS c FROM entities WHERE deprecated_at IS NULL")
    active_entities = (await cur.fetchone())["c"]
    cur = await db.execute("SELECT COUNT(*) AS c FROM relation_index WHERE deprecated_at IS NULL")
    active_relations = (await cur.fetchone())["c"]
    cur = await db.execute("SELECT COUNT(*) AS c FROM facts WHERE deprecated_at IS NULL")
    active_facts = (await cur.fetchone())["c"]
    cur = await db.execute("SELECT COUNT(*) AS c FROM entities WHERE deprecated_at IS NOT NULL")
    pending_entities = (await cur.fetchone())["c"]
    cur = await db.execute("SELECT COUNT(*) AS c FROM tombstones")
    tombstone_count = (await cur.fetchone())["c"]

    report: dict = {
        "requested_checks": requested,
        "summary": {
            "active_entities": active_entities,
            "active_relations": active_relations,
            "active_facts": active_facts,
            "pending_deprecated_entities": pending_entities,
            "tombstones": tombstone_count,
            "retention_hours": retention_hours(),
        },
    }
    if unknown:
        report["unknown_checks"] = unknown

    if "duplicates" in requested:
        report["duplicates"] = await find_duplicate_candidates()
    if "stale" in requested:
        report["stale"] = await find_stale_entries()
    if "types" in requested:
        report["types"] = await find_type_fragments()
    if "dangling" in requested:
        report["dangling"] = await find_dangling_relations()
    if "lint" in requested:
        report["lint"] = await run_lint()

    return report
