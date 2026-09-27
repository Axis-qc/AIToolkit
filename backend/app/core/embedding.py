"""
实体向量化：本地 ONNX 嵌入模型（Qwen3-Embedding-0.6B，1024 维），纯 CPU 推理。

设计要点：
  * 模型与缓存全部落在工作区内（backend/data/models），不写用户目录、不碰系统路径。
  * 模型懒加载：首次调用才载入，避免拖慢后端启动；载入失败不影响检索（如实上报不可用）。
  * 实体文本构造：name 加权重复 + 正文截断，因为 name 是区分度最高的字段
    （682 条里 81% 的名字含文件名/函数名这类高权重标识符），长正文会把名字稀释。
  * 环境前缀（【DSH】【本机】【通用】等）是高频公共 token，对语义无贡献，编码前剥离。
  * 写入向量时用内容指纹判断是否需要重算，避免每次写入都跑模型。指纹并入模型标识
    （见 MODEL_TAG），否则换模型后旧向量会被误判为「仍然新鲜」。
  * 查询侧与文档侧编码方式不同：Qwen3 要求查询加 Instruct 前缀、文档不加
    （见 QUERY_INSTRUCT 与 encode_query）。这是实测结论——不加前缀时 Qwen3 的
    recall@200 反而低于旧的 bge-small-zh-v1.5。
  * 批量编码按 token 长度排序后小批处理（见 embed_texts）。默认的 batch_size=256
    对长度差异大的中文实体极慢：实测 200 条 196.7 秒，而长度排序 + batch=8 只要
    47.2 秒，差 4.2 倍。原因是变长 padding 把算力浪费在填充上。

历史：2026-09-26 之前用 BAAI/bge-small-zh-v1.5（512 维，24M 参数，输入上限 512 token，
655 条里有 3 条超长被 tokenizer 静默截断，其中含 pinned 的「固定记忆」）。换模型
依据见图谱实体「知识图谱·语义检索改造 2026-09-26」。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

# ── 模型与缓存位置：钉死在工作区内 ──────────────────────
BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
MODEL_CACHE_DIR = BACKEND_DIR / "data" / "models"
MODEL_NAME = "Qwen/Qwen3-Embedding-0.6B"
EMBEDDING_DIM = 1024

# 指纹里并入的模型标识。必须随模型或维度变化而变，否则换模型后所有旧向量
# 的指纹仍与当前计算一致，refresh_embeddings 会返回 updated=0 而实际留着的是
# 另一个模型的向量。实测（2026-09-26）：旧实现只对实体文本做 SHA1，改
# MODEL_NAME 或 EMBEDDING_DIM 后指纹完全不变，换模型时不会触发任何重算。
MODEL_TAG = f"{MODEL_NAME}|{EMBEDDING_DIM}"

# 查询侧指令前缀。Qwen3 是 instruction-aware 模型，官方要求的查询格式是
# 「Instruct: {任务描述}\nQuery:{查询}」，文档侧不加任何前缀。
# 为什么必须加：实测同一批 46 条正例，不加前缀 Hit@5 27/46、recall@200 41/46；
# 加前缀 Hit@5 29/46、recall@200 45/46。不加前缀时反而不如旧的 bge-small。
QUERY_INSTRUCT = "Instruct: 检索与用户输入相关的长期记忆条目\nQuery:"

# 文档侧前缀。Qwen3 文档侧不加前缀，保留常量是为了日后换模型时改一处即可。
DOCUMENT_PREFIX = ""

# 正文参与编码的最大长度。正文均值 647 字、最长 5038 字，
# 全量拼接会让 name 的高权重标识符被稀释，截断到 400 字覆盖结论段。
CONTENT_MAX_CHARS = 400

# name 重复次数：提高 name 在向量里的权重
NAME_REPEAT = 2

# 批量编码的批大小与是否按长度排序。
#
# 实测（2026-09-26，Qwen3-0.6B，200 条真实实体，CPU 20 线程）：
#   batch=256（fastembed 默认）196.7s / batch=1 51.7s / 长度排序+batch=8 47.2s
#   / 长度排序+batch=16 53.4s / 长度排序+batch=32 65.6s / parallel=0 110.2s
# 结论：批越大越慢（变长 padding 浪费），长度排序后小批最优，parallel 不可用。
ENCODE_BATCH_SIZE = 8
ENCODE_SORT_BY_LENGTH = True

# 环境前缀，规范要求写在 content 开头，但对语义检索是噪声
_ENV_PREFIX_RE = re.compile(r"^\s*【(?:DSH|本机|通用|WorkBuddy|Stellaris|需要确认)[^】]*】\s*")

_model = None
_model_lock = threading.Lock()
_model_failed = False
_last_error: str | None = None


def last_error() -> str | None:
    """模型不可用时的原因，供上层如实上报，避免降级被静默掩盖。"""
    return _last_error


def _ensure_cache_env() -> None:
    """把模型缓存与 HF 端点固定在进程环境里，防止落到用户目录。"""
    MODEL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("FASTEMBED_CACHE_PATH", str(MODEL_CACHE_DIR))
    # 国内直连 HF 容易超时，默认走镜像；已设置则不覆盖
    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
    # 该警告在无符号链接权限的 Windows 上必然出现，屏蔽掉避免刷日志
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")


def get_model():
    """懒加载嵌入模型。载入失败返回 None（调用方据此降级为纯字面检索）。"""
    global _model, _model_failed, _last_error
    if _model is not None:
        return _model
    if _model_failed:
        return None
    with _model_lock:
        if _model is not None:
            return _model
        if _model_failed:
            return None
        try:
            _ensure_cache_env()
            from fastembed import TextEmbedding

            _model = TextEmbedding(model_name=MODEL_NAME)
            _last_error = None
            logger.info("embedding model loaded: %s", MODEL_NAME)
        except Exception as exc:  # 缺依赖、无模型文件、下载失败都归到这里
            _model_failed = True
            _last_error = f"{type(exc).__name__}: {exc}"
            logger.warning("embedding model unavailable, vector arm disabled: %s", _last_error)
            return None
    return _model


def is_available() -> bool:
    """向量能力是否可用（不触发下载，只看已有模型能否载入）。"""
    return get_model() is not None


# ── 实体文本构造 ────────────────────────────────────────

def strip_env_prefix(text: str) -> str:
    """剥离开头的环境前缀。"""
    if not text:
        return ""
    return _ENV_PREFIX_RE.sub("", text, count=1).strip()


def build_entity_text(
    name: str,
    etype: str = "",
    content: str = "",
    relations: list | None = None,
) -> str:
    """把一条实体拼成用于编码的文本。

    name 重复 NAME_REPEAT 次以加权；type 作为弱信号附一次；
    正文剥前缀后截断；relations 只取关系名，且不展开（悬空关系较多，
    其中 61 条指向不存在的实体名，展开会把噪声编进向量）。
    """
    parts: list[str] = []
    clean_name = (name or "").strip()
    if clean_name:
        parts.extend([clean_name] * NAME_REPEAT)
    if etype:
        parts.append(etype)

    body = strip_env_prefix(content or "")
    if body:
        if len(body) > CONTENT_MAX_CHARS:
            body = body[:CONTENT_MAX_CHARS]
        parts.append(body)

    if relations:
        rel_names: list[str] = []
        for rel in relations:
            if isinstance(rel, dict):
                target = rel.get("name")
                rel_type = rel.get("rel")
                if target:
                    rel_names.append(f"{target}({rel_type})" if rel_type else str(target))
            elif isinstance(rel, str):
                rel_names.append(rel)
        if rel_names:
            parts.append(" ".join(rel_names))

    return "\n".join(parts).strip()


def content_fingerprint(
    name: str,
    etype: str = "",
    content: str = "",
    relations: list | None = None,
) -> str:
    """实体文本指纹：变了才需要重算向量。

    指纹包含 MODEL_TAG：换模型或改维度时全体指纹自动失效一次，从而触发重算。
    没有这一项的话，换模型后旧向量会被当成「仍然新鲜」而保留，检索结果将不可用
    （维度不符时 _parse_embedding_cell 才被动返回 None，等于把重算推迟到请求路径上）。
    """
    raw = build_entity_text(name, etype, content, relations)
    return hashlib.sha1(f"{MODEL_TAG}\x00{raw}".encode("utf-8")).hexdigest()[:16]


# ── 编码 ────────────────────────────────────────────────

def embed_documents(texts: list[str]) -> list[list[float]]:
    """批量编码文档（实体）侧。

    按 token 长度排序后小批处理：批越大越慢是实测结论，原因是变长 padding
    把算力花在填充上。编码结果按原顺序还原，调用方无感。

    模型不可用返回空列表，由调用方决定如何降级（不要在此静默吞掉错误）。
    """
    model = get_model()
    if model is None or not texts:
        return []

    order = list(range(len(texts)))
    payload = texts
    if ENCODE_SORT_BY_LENGTH:
        try:
            tok = model.model.tokenizer
            order.sort(key=lambda i: len(tok.encode(texts[i]).ids))
            payload = [texts[i] for i in order]
        except Exception as exc:  # 排序只是优化，失败就按原顺序编码
            logger.debug("length sort skipped: %s", exc)
            order = list(range(len(texts)))
            payload = texts

    try:
        encoded = [vec.tolist() for vec in model.embed(payload, batch_size=ENCODE_BATCH_SIZE)]
    except Exception as exc:
        logger.warning("embedding failed: %s", exc)
        return []

    if len(encoded) != len(texts):
        logger.warning("embedding count mismatch: got %d want %d", len(encoded), len(texts))
        return []

    restored: list[list[float]] = [[] for _ in texts]
    for pos, idx in enumerate(order):
        restored[idx] = encoded[pos]
    return restored


def encode_texts(texts: list[str]) -> list[list[float]]:
    """兼容旧名：批量编码文档侧。"""
    return embed_documents(texts)


def encode_query(text: str) -> list[float] | None:
    """编码查询侧。Qwen3 要求加 Instruct 前缀，文档侧不加。

    查询与文档必须走不同路径，否则向量不在同一个语义空间，余弦没有意义。
    """
    if not text:
        return None
    model = get_model()
    if model is None:
        return None
    try:
        vec = next(iter(model.query_embed(f"{QUERY_INSTRUCT}{text}")))
        return vec.tolist()
    except Exception as exc:
        logger.warning("query embed failed: %s", exc)
        return None


def encode_one(text: str) -> list[float] | None:
    """单条编码（查询侧用）。失败或不可用返回 None。"""
    return encode_query(text)


# ── 向量的存储格式 ──────────────────────────────────────
# 存 JSON 文本而非 BLOB：embedding 列在既有库里就是 TEXT，
# 且 JSON 便于人工排查与跨工具读取，512 维 float32 约 7KB/条，
# 671 条合计 4.7MB，对本库规模完全可接受。

def vector_to_text(vector: list[float]) -> str:
    """向量转成入库文本。"""
    return json.dumps([round(float(x), 6) for x in vector], separators=(",", ":"))


def text_to_vector(text: str | None) -> list[float] | None:
    """入库文本转回向量。空值或坏数据返回 None。"""
    if not text:
        return None
    try:
        value = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(value, list) or len(value) != EMBEDDING_DIM:
        return None
    try:
        return [float(x) for x in value]
    except (TypeError, ValueError):
        return None


def cosine(a: list[float], b: list[float]) -> float:
    """余弦相似度（单对）。批量场景请用 cosine_matrix，这个函数供自测与兜底。"""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    return dot / ((na ** 0.5) * (nb ** 0.5))


def cosine_matrix(query_vector: list[float], matrix) -> list[float]:
    """一次算 query 对整库的余弦，走 numpy 向量化。

    为什么需要这个：实测纯 Python 循环算 671×512 的余弦要 30ms 且跑在
    事件循环线程里，会把 uvicorn 单 worker 卡住（并发时心跳延迟可达 130ms），
    连带拖慢前端 /api/graph。numpy 版本把这一步降到亚毫秒级。
    """
    if not query_vector or matrix is None:
        return []
    try:
        import numpy as np
    except ImportError:
        # 没有 numpy 时退回逐条计算（仍是正确结果，只是慢）
        return [cosine(query_vector, row) for row in matrix]

    q = np.asarray(query_vector, dtype=np.float32)
    m = np.asarray(matrix, dtype=np.float32)
    if m.ndim != 2 or m.shape[1] != q.shape[0]:
        return []
    qn = np.linalg.norm(q)
    mn = np.linalg.norm(m, axis=1)
    denom = qn * mn
    # 零向量（空实体）单独处理，避免除零
    with np.errstate(divide="ignore", invalid="ignore"):
        sims = np.where(denom > 0, (m @ q) / denom, 0.0)
    return sims.tolist()
