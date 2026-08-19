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


async def search_entities(query: str, top_k: int) -> list[dict]:
    """搜索实体，范围：name + type + content + relations JSON。不再遍历 facts。"""
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
        for kw, weight in keywords.items():
            # 名称匹配（权重最高）
            if kw == name_lower:
                score += 12
            elif kw in name_lower or (weight >= 2 and name_lower in kw):
                score += 10 if weight >= 5 else 6 if weight >= 2 else 3
            # 类型匹配
            if kw == type_lower:
                score += 4
            elif kw in type_lower:
                score += 2 if weight >= 2 else 1
            # 内容匹配（替代原来 facts 的职能）
            if kw in content_lower:
                score += 4 if weight >= 5 else 2 if weight >= 2 else 1
            # relations JSON 文本匹配
            if kw in rels_text:
                score += 2 if weight >= 2 else 1
            # 属性匹配
            if kw in props_text:
                score += 2 if weight >= 2 else 1

        if score >= _MIN_SEARCH_SCORE:
            scored.append((row["name"], row["type"], row["importance"], row["pinned"], score))

    scored.sort(key=lambda x: x[4], reverse=True)

    if not scored:
        return []

    return [
        {"entity": name, "type": etype, "importance": imp, "pinned": bool(pin)}
        for name, etype, imp, pin, _ in scored[:top_k]
    ]


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
