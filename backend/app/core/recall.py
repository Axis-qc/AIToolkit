"""意向检索编排：三步流程 + 向量降级。

    DSH 发原话 → 服务端从记忆里选取相关条目 → 直接返回这些条目

第 2 步（选取）把图谱现有条目名编号列给模型，让它挑编号。挑中的条目名
本身就是答案，第 3 步直接返回，不再把它们当查询词喂回检索算法。

为什么不做第二步检索：
  旧的字面引擎会对传入的查询重新做 n-gram 穷举切词。拿条目名
  「先确认再动手原则」当查询，会被切成「先确认」「确认再」「再动手」
  「原则」等 26 个碎片，其中「原则」二字会撞上名字含「原则」的泛实体，
  碎片还会撞上正文通篇讲元词汇的条目。实测 46 条评测集里，
  「记忆使用原则（合并）」与「固定记忆」各霸榜 8 次，而模型真正挑中的
  目标被挤出 top5。这一步是净损失。

选择式不可用（模型或网络故障）时退回「只用原话走向量臂」，保证抽词服务
挂掉时注入通道不至于整个失效。

字面检索引擎与生成式抽词链已于本次改造整体删除：字面匹配无法处理任务型
输入（「帮我把这个数值改一下」这类句子与规则实体名没有任何字符重叠），
它作为主检索臂的召回质量已被实测否定。需要按词精确定位时走
semantic_search（纯向量）或 get_entity（精准名字）。固定记忆注入不依赖
检索算法，其 get_pinned_entities 已迁至 graph_crud。

自愈式向量索引仍然保留：实体正文会被改名、合并、批量修改等多条路径改写，
逐个挂更新点既容易漏，也会碰既有写入语义。这里在 embedding 列里同时存
内容指纹，检索时比对，不一致就地重算。
"""
from __future__ import annotations

import asyncio
import json
import logging

from . import embedding as emb
from . import intent
from .config import settings
from .db import _connect

logger = logging.getLogger(__name__)

# 向量臂取多少条候选。
#
# 字面臂删除后向量臂是唯一来源，这个值不再是固定深度，而是「最低深度」：
# recall 取 max(top_k, VECTOR_ARM_DEPTH)，保证调用方要多少条就给足多少条。
# 保留这个下限是因为注入通道通常只要几条，但多取一点不额外花编码开销
# （全库余弦本来就一次算完），却能避免 top_k 与取数深度不一致造成的静默截断。
VECTOR_ARM_DEPTH = 10

# 向量臂的最低余弦相似度。
#
# 实测依据（2026-09-25，671 条真实实体，bge-small-zh-v1.5）：
#   * 无关输入对全库的最高余弦可达 0.5013（30 条无关输入实测的上界；
#     单条样本如「今天天气不错」只有 0.4058，不能拿单条当上界）。
#   * 真正相关的召回实测：「覆盖写入之前要不要先备份」对「数据变更前先
#     备份原则」是 0.6747，「把那个旧文件删掉吧」对「文件删除规则」是
#     0.6369，「帮我把这个数值改一下」对「先确认再动手原则」是 0.5092。
# 两条分布有重叠（0.50 附近），单靠阈值无法干净切分，所以另加下面两条
# 结构性约束：不再有字面证据一说，只能靠阈值档位区分输入性质。
VECTOR_MIN_COSINE = 0.45

# 无字面证据时的门槛：此时没有任何字符级证据，只靠向量最容易把无关
# 内容塞进上下文。取 0.52 是实测无关输入上界 0.5013 之上、相关召回
# 下界 0.5092 附近的位置——注意这两条本身有重叠，裕度只有约 0.02，
# 所以调用侧必须把「无需检索的输入」判成空关键词，不能全靠这里兜。
VECTOR_MIN_COSINE_LONELY = 0.52

# 调用方明确判定「该轮无需检索」（keywords 给了但为空）时的门槛。
# 这一档没有任何字符级证据，也不该有——取 0.60，只放行明显强相关的内容。
# 依据：实测无关输入上界 0.5013，相关召回下界 0.5092，两者分布重叠，
# 0.52 不足以干净切分；这一档宁愿少注入也不要注入无关记忆。
VECTOR_MIN_COSINE_DECLINED = 0.60

# 自愈式刷新的互斥锁。见 refresh_embeddings 的说明。
_refresh_lock = asyncio.Lock()


def _fingerprint_of(row) -> str:
    """算某一行的当前内容指纹。"""
    try:
        relations = json.loads(row["relations"] or "[]")
    except (json.JSONDecodeError, TypeError):
        relations = []
    return emb.content_fingerprint(row["name"], row["type"], row["content"] or "", relations)


async def _load_entity_names() -> list[str]:
    """取全部活跃实体名（按名字排序，保证编号稳定可复现）。"""
    db = await _connect()
    cur = await db.execute(
        "SELECT name FROM entities WHERE deprecated_at IS NULL ORDER BY name"
    )
    return [r["name"] for r in await cur.fetchall()]


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

    加锁串行化：自愈是「读全表 → 编码 → 写回」三步，若并发触发（多个
    请求同时发现同一批过期实体），会重复编码同一批文本。实测 671 条全过期
    时 5 个并发请求各跑一遍要 156 秒，串行化后只跑一遍。
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

    texts = [text for _n, _f, text in pending]
    vectors = emb.encode_texts(texts)
    if not vectors:
        # 模型不可用要说清楚，不能报成 failed:0 让调用方以为一切正常
        reason = emb.last_error() or "embedding model unavailable"
        logger.warning("backfill skipped: %s", reason)
        return {
            "checked": len(rows),
            "updated": 0,
            "failed": 0,
            "skipped_empty": 0,
            "available": False,
            "error": reason,
        }

    db = await _connect()
    updated = 0
    failed = 0
    skipped = 0
    for (name, fingerprint, _text), vector in zip(pending, vectors):
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


async def _vector_arm(query: str, limit: int, min_cosine: float = VECTOR_MIN_COSINE) -> list[tuple[str, float]]:
    """向量臂：编码查询，与全库向量算余弦。

    同时做自愈：发现指纹过期就地重算并写回，保证结果用的是当前正文。
    模型不可用或查询为空时返回空列表。

    整个「读全表 + 算余弦」放在线程里跑：实测纯 Python 逐条余弦要 30ms、
    全表读 12ms，跑在事件循环线程会卡住 uvicorn 单 worker（并发时心跳
    延迟可达 130ms，连带影响前端 API）。这里的读取与矩阵乘法都不碰
    SQLite 连接以外的共享状态，放线程是安全的。
    """
    if not query:
        return []
    query_vector = await asyncio.to_thread(emb.encode_one, query)
    if not query_vector:
        return []

    rows = await _load_all_entities()
    names: list[str] = []
    matrix: list[list[float]] = []
    stale: list[str] = []
    for row in rows:
        vector, stored_fp = _parse_embedding_cell(row["embedding"])
        if vector is None or stored_fp != _fingerprint_of(row):
            stale.append(row["name"])
            continue
        names.append(row["name"])
        matrix.append(vector)

    scored: list[tuple[str, float]] = []
    if matrix:
        sims = await asyncio.to_thread(emb.cosine_matrix, query_vector, matrix)
        for name, sim in zip(names, sims):
            if sim >= min_cosine:
                scored.append((name, float(sim)))

    # 过期的就地补算：只补这批，补完本次仍按已有结果返回，避免拖长单次延迟
    if stale:
        try:
            await refresh_embeddings(stale)
        except Exception as exc:
            logger.warning("lazy embedding refresh failed: %s", exc)

    scored.sort(key=lambda item: item[1], reverse=True)
    return scored[:limit]


async def recall(
    user_text: str,
    keywords: list[str] | None = None,
    top_k: int = 5,
    arm_depth: int | None = None,
    with_diagnostics: bool = True,
    auto_extract: bool = True,
) -> list[dict]:
    """意向检索主入口 —— 三步流程 + 向量降级。

    DSH 发原话 → 服务端从记忆里选取相关条目 → 直接返回这些条目

    keywords 传 None 时走主路径：服务端把全部条目名编号列给模型，让它挑
    编号；挑中的条目名**直接作为结果返回**，不再喂回检索算法。模型挑不出
    任何条目（判定与本轮无关）就返回空列表，这是合法结论，不再二次调模型。

    keywords 传空数组仍然表示「调用方明确判定本轮无需检索」，此时向量臂
    按 VECTOR_MIN_COSINE_DECLINED 这一档从严放行。

    keywords 传非空数组时不再有消费者：字面臂已随检索改造删除，单独传词
    进来没有可用的引擎。这种情况按正常向量检索处理，并把「关键词被忽略」
    写进返回的 intent 诊断里，让调用方看得见，不静默吞掉。

    arm_depth 为 None（默认）时取 max(top_k, VECTOR_ARM_DEPTH)。字面臂删除
    后向量臂是唯一来源，若仍固定按 10 取数，调用方传 top_k=15 只会拿回 10
    条，top_k 就变成了摆设；这里保证取数深度不小于请求条数。

    选择式不可用（模型或网络故障）时降级为「只用原话走向量臂」，保证
    抽词服务挂掉时注入通道不至于整个失效。
    """
    # 向量臂取数深度：不小于请求条数，也不低于 VECTOR_ARM_DEPTH
    depth = arm_depth if arm_depth is not None else max(int(top_k or 0), VECTOR_ARM_DEPTH)

    # ── 主路径：服务端选取 → 直接返回 ──
    # 只在调用方「没给关键词」时做（keywords is None）。若调用方明确给了
    # 空数组，那是「判定无需检索」的语义，不该被自动处理覆盖。
    extract_error = ""
    mode = ""
    if keywords is None and auto_extract and intent.is_enabled() and settings.intent_select_enabled:
        all_names = await _load_entity_names()
        picked, select_error = await intent.select_relevant(user_text, all_names)
        if picked is not None:
            logger.info("intent selection for %r -> %d 条", user_text[:40], len(picked))
            return await _results_from_selection(picked, top_k, with_diagnostics)
        # 选择式不可用才降级，且如实记录，不再静默
        extract_error = f"selection failed: {select_error}"
        mode = "vector"
        logger.warning("intent selection unavailable, falling back to vector arm: %s", select_error)

    # 非空关键词现在没有消费者，如实记录并带到返回值里，不静默丢掉
    ignored_keywords: list[str] = []
    if keywords:
        ignored_keywords = list(keywords)
        logger.info(
            "keywords ignored (literal arm removed), falling back to vector arm: %r",
            ignored_keywords[:5],
        )

    keyword_list = list(keywords or [])

    # 区分「调用方没给关键词」与「调用方明确说不用检索」：
    #   keywords 为 None（未给）      → 正常向量检索
    #   keywords 给了但为空            → 视为「该轮无需检索」，阈值提到最高档
    caller_declined = keywords is not None and not keyword_list

    # ── 向量臂：吃原始文本 ──
    # 调用方明确判定无需检索时要求更高门槛：实测无关输入的最高余弦可达
    # 0.5013，而 lonely 阈值 0.52 的裕度只有约 0.02，因此这一档宁缺勿滥。
    min_cosine = (
        VECTOR_MIN_COSINE_DECLINED
        if caller_declined
        else VECTOR_MIN_COSINE
    )
    vector_pairs = await _vector_arm(user_text, depth, min_cosine=min_cosine)
    vector_names = [name for name, _score in vector_pairs]
    vector_scores = {name: score for name, score in vector_pairs}

    if not vector_names:
        return []

    db = await _connect()
    cur = await db.execute(
        "SELECT name, type, importance, pinned FROM entities WHERE deprecated_at IS NULL"
    )
    meta = {r["name"]: r for r in await cur.fetchall()}

    results: list[dict] = []
    for name, score in vector_pairs[: max(top_k, 0)]:
        row = meta.get(name)
        if row is None:
            # 向量名单里出现了已作废的实体，跳过
            continue
        entry = {
            "entity": name,
            "type": row["type"],
            "importance": row["importance"],
            "pinned": bool(row["pinned"]),
            "score": round(score, 6),
        }
        if with_diagnostics:
            entry["arms"] = {
                "vector": {
                    "rank": vector_names.index(name) + 1,
                    "cosine": round(vector_scores.get(name, 0.0), 4),
                }
            }
        results.append(entry)

    # 降级原因随结果一起带出去，便于排查「为什么这次没走选择式」；
    # ignored_keywords 非空说明调用方传了词但字面臂已删除、这些词没被使用。
    # 只附加在首条，避免每条重复。零结果时返回空列表，不带诊断（列表为空
    # 时无处安放，调用方需要诊断可传 with_diagnostics 并自行记录请求）。
    if with_diagnostics and results:
        results[0]["intent"] = {
            "mode": mode or ("declined" if caller_declined else "vector"),
            "keywords_used": [],
            "ignored_keywords": ignored_keywords,
            "auto_extracted": False,
            "extract_error": extract_error,
        }
    return results


async def _results_from_selection(
    picked: list[str], top_k: int, with_diagnostics: bool
) -> list[dict]:
    """把模型挑中的条目名直接组装成返回结果（三步流程的第 3 步）。

    不做任何二次检索：挑中的条目名就是答案。顺序沿用模型给的顺序
    （提示词要求它按相关性降序输出）。

    只做两件必要的卫生处理：
      * 丢掉已作废的实体（模型看到的是当前快照，但期间可能被删）
      * 去重并截断到 top_k
    """
    if not picked:
        return []

    db = await _connect()
    cur = await db.execute(
        "SELECT name, type, importance, pinned FROM entities WHERE deprecated_at IS NULL"
    )
    meta = {r["name"]: r for r in await cur.fetchall()}

    results: list[dict] = []
    seen: set[str] = set()
    for rank, name in enumerate(picked, 1):
        if name in seen:
            continue
        row = meta.get(name)
        if row is None:
            # 模型挑了一个已作废/不存在的条目，跳过
            continue
        seen.add(name)
        entry = {
            "entity": name,
            "type": row["type"],
            "importance": row["importance"],
            "pinned": bool(row["pinned"]),
            # 名次即相关性：第 1 名 1.0，线性衰减到 0.5
            "score": round(1.0 - 0.5 * (rank - 1) / max(len(picked), 1), 6),
        }
        if with_diagnostics:
            entry["arms"] = {"selected": {"rank": rank}}
        results.append(entry)
        if len(results) >= max(top_k, 0):
            break

    if with_diagnostics and results:
        results[0]["intent"] = {
            "mode": "selection",
            "keywords_used": [r["entity"] for r in results],
            "picked_total": len(picked),
            "extract_error": "",
        }
    return results


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
