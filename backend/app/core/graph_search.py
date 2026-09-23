"""
知识图谱搜索引擎：CJK 分词 + 权重计分 + 固定实体查询。
从 core/graph.py 拆出。
"""
import json
import re

from .db import _connect

# ── 关键词提取 ──────────────────────────────────────────

_IDENT_RE = re.compile(r"[A-Za-z][A-Za-z0-9_.:-]{1,}")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]+")
_CJK_STOPWORDS = {
    "一个", "一些", "一下", "不是", "不用", "不能", "什么", "他们", "以及", "但是",
    "你的", "里面", "关于", "其实", "刚才", "原来", "可以", "因为", "如果", "应该",
    "当前", "怎么", "我们", "所以", "换成", "是否", "现在", "然后", "看看", "这个",
    "这些", "这样", "那个", "那些", "需要", "还是", "这里",
}
_CJK_STOP_CHARS = set("的一是在了和就都而及或也很到把被给用")
_MIN_SEARCH_SCORE = 4


def _extract_search_keywords(query: str) -> dict[str, int]:
    """Extract conservative keywords for graph search.

    Weight 5: code-like identifiers, file names, event IDs.
    Weight 2: Chinese terms with at least 3 chars.
    Weight 1: short Chinese terms, only used as weak signals.
    """
    keywords: dict[str, int] = {}

    def add(raw: str, weight: int):
        kw = raw.strip().lower()
        if len(kw) < 2 or kw in _CJK_STOPWORDS:
            return
        keywords[kw] = max(keywords.get(kw, 0), weight)

    for ident in _IDENT_RE.findall(query):
        add(ident, 5)

    for chunk in _CJK_RE.findall(query):
        if chunk in _CJK_STOPWORDS:
            continue
        if 3 <= len(chunk) <= 8:
            add(chunk, 2)
        if len(chunk) >= 3:
            for n in (3, 4, 5, 6):
                if len(chunk) < n:
                    continue
                for i in range(len(chunk) - n + 1):
                    gram = chunk[i:i + n]
                    if gram in _CJK_STOPWORDS or any(ch in _CJK_STOP_CHARS for ch in gram):
                        continue
                    add(gram, 2)
        if len(chunk) >= 2:
            for i in range(len(chunk) - 1):
                gram = chunk[i:i + 2]
                if gram in _CJK_STOPWORDS or any(ch in _CJK_STOP_CHARS for ch in gram):
                    continue
                add(gram, 1)

    return keywords


# ── 搜索 ────────────────────────────────────────────────


async def search_entities(query: str, top_k: int, with_diagnostics: bool = False) -> list[dict]:
    """搜索实体，范围：name + type + content + relations JSON。不再遍历 facts。

    with_diagnostics=True 时额外返回 score 与 matched_fields，
    用于判断两个相似实体是否在互相抢位、以及整理后检索是否真的改善。
    分数在循环里本来就算好了，这里只是把它带出去，不重算。
    """
    db = await _connect()
    keywords = _extract_search_keywords(query)
    if not keywords:
        return []

    cursor = await db.execute(
        "SELECT name, type, content, relations, properties, importance, pinned "
        "FROM entities WHERE deprecated_at IS NULL"
    )
    rows = await cursor.fetchall()

    scored = []
    for row in rows:
        name_lower = row["name"].lower()
        type_lower = row["type"].lower()
        content_lower = (row["content"] or "").lower()
        rels_text = (row["relations"] or "[]").lower()
        props = json.loads(row["properties"])
        props_text = json.dumps(props, ensure_ascii=False).lower()

        score = 0
        # 逐字段累计，同时记录是哪个字段贡献的、贡献了多少、命中了哪些词
        fields = {
            "name": {"score": 0, "hits": []},
            "type": {"score": 0, "hits": []},
            "content": {"score": 0, "hits": []},
            "relations": {"score": 0, "hits": []},
            "properties": {"score": 0, "hits": []},
        }
        for kw, weight in keywords.items():
            # 名称匹配（权重最高）
            if kw == name_lower:
                score += 12
                fields["name"]["score"] += 12
                fields["name"]["hits"].append(kw)
            elif kw in name_lower or (weight >= 2 and name_lower in kw):
                gain = 10 if weight >= 5 else 6 if weight >= 2 else 3
                score += gain
                fields["name"]["score"] += gain
                fields["name"]["hits"].append(kw)
            # 类型匹配
            if kw == type_lower:
                score += 4
                fields["type"]["score"] += 4
                fields["type"]["hits"].append(kw)
            elif kw in type_lower:
                gain = 2 if weight >= 2 else 1
                score += gain
                fields["type"]["score"] += gain
                fields["type"]["hits"].append(kw)
            # 内容匹配（替代原来 facts 的职能）
            if kw in content_lower:
                gain = 4 if weight >= 5 else 2 if weight >= 2 else 1
                score += gain
                fields["content"]["score"] += gain
                fields["content"]["hits"].append(kw)
            # relations JSON 文本匹配
            if kw in rels_text:
                gain = 2 if weight >= 2 else 1
                score += gain
                fields["relations"]["score"] += gain
                fields["relations"]["hits"].append(kw)
            # 属性匹配
            if kw in props_text:
                gain = 2 if weight >= 2 else 1
                score += gain
                fields["properties"]["score"] += gain
                fields["properties"]["hits"].append(kw)

        if score >= _MIN_SEARCH_SCORE:
            scored.append((row["name"], row["type"], row["importance"], row["pinned"], score, fields))

    scored.sort(key=lambda x: x[4], reverse=True)

    if not scored:
        return []

    if not with_diagnostics:
        return [
            {"entity": name, "type": etype, "importance": imp, "pinned": bool(pin)}
            for name, etype, imp, pin, _, _ in scored[:top_k]
        ]

    results = []
    for name, etype, imp, pin, score, fields in scored[:top_k]:
        # 只保留真正得分的字段，避免噪声
        matched = {
            f: {"score": d["score"], "hits": d["hits"]}
            for f, d in fields.items() if d["score"] > 0
        }
        top_field = max(matched.items(), key=lambda kv: kv[1]["score"])[0] if matched else None
        results.append({
            "entity": name,
            "type": etype,
            "importance": imp,
            "pinned": bool(pin),
            "score": score,
            "matched_fields": matched,
            "top_field": top_field,
        })
    return results


async def get_pinned_entities() -> list[dict]:
    """获取所有固定注入的实体（完整字段，按重要度降序）。"""
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
