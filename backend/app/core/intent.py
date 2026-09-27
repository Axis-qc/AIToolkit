"""意向选取：调一次模型，把用户口语输入映射到图谱现有条目。

为什么放在服务端而不是插件侧：
  * 插件侧的模型选择受 DSH 配置与版本影响（依赖 ctx.llm / agentDefaultModel /
    BlockAssembler 等内部 API），DSH 升级可能破坏；放服务端只需改一个配置项。
  * 服务端是所有环境的公共后端，意向选取写在这里，WorkBuddy 等环境自动受益。
  * DSH 插件因此退回「只发用户原话」，代码大幅简化。

代价：每轮多一次服务端到模型的往返。走到本机代理时延迟可控。

配置项（backend/.env，改完重启后端生效）：
    INTENT_EXTRACT_ENABLED   是否启用（默认 true；false 则不调模型，直接走向量臂）
    INTENT_EXTRACT_BASE_URL  OpenAI 兼容端点，默认本机 workboddy 代理
    INTENT_EXTRACT_API_KEY   该端点的 key；本机代理不校验真伪，填任意非空值即可
    INTENT_EXTRACT_MODEL     模型 id，默认 cn:deepseek-v4.1-flash
    INTENT_EXTRACT_TIMEOUT_MS 超时，默认 6000
    INTENT_SELECT_MAX_TOKENS 选择式输出上限，默认 10240

关于输出上限的取值：实测推理型模型（如 cn:hy3）会把 max_tokens 全部烧在
reasoning_content 上，正文为空且 finish_reason=length。cn:deepseek-v4-flash
同样有间歇推理路径，给 2048 时 53 条里至少 4 条被截断静默降级，给 10240 才稳。

本模块只保留「选择式」这一条路径。曾经的「让模型自由生成关键词」写法已随
字面检索引擎一并删除：实测生成式真实链路 30/46，远低于选择式的 42/46，根因
是模型不知道本图谱里有哪些条目——它能猜中话题，猜不中条目措辞。
"""
from __future__ import annotations

import asyncio
import logging
import re

import httpx

from .config import settings
logger = logging.getLogger(__name__)


def is_enabled() -> bool:
    """模型选取是否启用。"""
    return bool(settings.intent_extract_enabled)


def endpoint() -> tuple[str, str, str]:
    """当前配置的 (base_url, api_key, model)。"""
    return (
        (settings.intent_extract_base_url or "").rstrip("/"),
        settings.intent_extract_api_key or "",
        settings.intent_extract_model or "",
    )


# 复用的 HTTP 客户端。
#
# 为什么不每次新建：实测每次 async with AsyncClient() 都要重连，单次往返
# 从 1.2-1.4 秒涨到 3-4 秒（本机代理本身往返就要 1.7 秒左右）。这一步在
# 每轮对话的关键路径上，必须复用连接。httpx.AsyncClient 是线程安全且可
# 跨事件循环复用连接池的，这里做成模块级单例，由 close_client() 收尾。
_client = None
_client_lock = asyncio.Lock()


async def _get_client(timeout: float):
    global _client
    if _client is not None and not _client.is_closed:
        return _client
    async with _client_lock:
        if _client is None or _client.is_closed:
            _client = httpx.AsyncClient(timeout=timeout)
    return _client


async def close_client() -> None:
    """关闭复用连接。后端 lifespan 收尾时调用。"""
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None


def _extract_text_from_response(payload: dict) -> tuple[str, str]:
    """从响应里取正文。返回 (正文, 说明)。

    推理型模型可能把内容放在 reasoning_content 而 content 为空，且
    finish_reason=length 表示被 max_tokens 截断。这里区分这两种情况并如实
    上报，便于排查「为什么一直降级」。
    """
    choice = (payload.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    content = (message.get("content") or "").strip()
    if content:
        return content, ""

    reasoning = message.get("reasoning_content") or ""
    finish = choice.get("finish_reason") or ""
    if finish == "length":
        return "", (
            f"输出被 max_tokens 截断（finish_reason=length，"
            f"reasoning 长度 {len(reasoning)}）；推理型模型请换非推理模型或调大 INTENT_SELECT_MAX_TOKENS"
        )
    if reasoning:
        return "", f"正文为空但 reasoning 有 {len(reasoning)} 字，疑似推理型模型"
    return "", "响应正文为空"


# ── 选择式选取（正式路径）────────────────────────────────
#
# 为什么是选择而不是生成：让模型「自由生成关键词」的做法，实测真实链路只有
# 30/46 命中（同一检索实现下，人工预写关键词 42/46）。根因是模型不知道本图谱
# 里有哪些条目——它能猜中话题，猜不中条目措辞。典型对照：
#   「把仓库扫一遍」期望「最小范围调查原则」：模型给「扫描仓库/无用代码/静态分析」
#   「拿不准要不要合并」期望「需要确认标记原则」：模型给「实体合并/去重/消歧」
#   「删了这个文件」期望「文件删除规则-回收站机制」：模型给「删除文件/数据丢失」
#
# 改法是把「生成」变成「选择」：把图谱现有条目名作为选项列给模型，让它挑编号。
# 实测（2026-09-25，53 条评测集）：选择式 42/46，与人工预写关键词的上限持平；
# 负例零召回 7/7；代价是单次约 1 万 token、2.8 秒。
SELECTION_PROMPT = """下面是长期记忆库的全部条目名（编号. 名称）。
读用户的一句话，从这些条目里挑出与该输入相关的条目，输出它们的编号数组。

规则：
1. 只输出一个 JSON 数组，元素是编号（数字），不要输出其他文字、解释或代码块标记。
2. 挑 1-10 个最相关的，宁准勿滥。
3. 优先挑**规则类、原则类、规范类**条目：用户要动手做事时，描述「该怎么做」的
   条目比描述「某个具体事物」的条目更该被选中。
4. 若这句话确实与库里任何条目都无关（纯闲聊、打招呼），输出空数组 []。

条目列表：
{listing}"""


def build_selection_prompt(names: list[str]) -> str:
    """把全部条目名编号列出，构成选择式提示词。"""
    listing = "\n".join(f"{i}. {n}" for i, n in enumerate(names, 1))
    return SELECTION_PROMPT.replace("{listing}", listing)


def parse_selection(content: str, names: list[str], limit: int = 10) -> list[str]:
    """解析模型挑出的编号，映射回条目名。越界编号与重复项都丢掉。"""
    if not isinstance(content, str) or not content:
        return []
    picked: list[str] = []
    for raw in re.findall(r"\d+", content):
        index = int(raw)
        if 1 <= index <= len(names):
            name = names[index - 1]
            if name not in picked:
                picked.append(name)
        if len(picked) >= limit:
            break
    return picked


async def _chat(payload: dict, timeout: float) -> tuple[dict | None, str]:
    """发一次 chat/completions，返回 (响应体, 错误说明)。复用连接池。"""
    base, key, _model = endpoint()
    if not base:
        return None, "未配置 INTENT_EXTRACT_BASE_URL"
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    try:
        client = await _get_client(timeout)
        resp = await client.post(f"{base}/chat/completions", json=payload, headers=headers)
        if resp.status_code != 200:
            return None, f"HTTP {resp.status_code}: {resp.text[:160]}"
        return resp.json(), ""
    except Exception as exc:  # 网络、超时、JSON 解析失败都归到这里
        return None, f"{type(exc).__name__}: {exc}"


async def select_relevant(
    user_text: str,
    names: list[str],
    max_tokens: int | None = None,
) -> tuple[list[str] | None, str]:
    """选择式选取：给全部条目名，让模型挑出相关的。

    返回 (条目名列表, 错误说明)。列表可能为空（模型判定无关）；错误说明非空时
    调用方应降级为「只用原话走向量臂」。
    """
    if not user_text or not user_text.strip():
        return None, "用户输入为空"
    if not is_enabled():
        return None, "未启用（INTENT_EXTRACT_ENABLED=false）"
    if not names:
        return None, "条目列表为空"
    base, _key, model = endpoint()
    if not base or not model:
        return None, "未配置 INTENT_EXTRACT_BASE_URL 或 INTENT_EXTRACT_MODEL"

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": build_selection_prompt(names)},
            {"role": "user", "content": user_text.strip()},
        ],
        "temperature": 0,
        # 选项很长（680 余条约 1 万 token），输出却很短（几十个编号）。
        # 给足输出空间是因为实测该模型偶尔走推理路径，会把 token 烧在
        # reasoning_content 上、正文为空且 finish_reason=length。
        "max_tokens": int(max_tokens or settings.intent_select_max_tokens),
        "stream": False,
    }

    timeout = max(int(settings.intent_select_timeout_ms), 100) / 1000.0
    data, err = await _chat(payload, timeout)
    if data is None:
        return None, err

    content, note = _extract_text_from_response(data)
    if not content:
        return None, note or "响应正文为空"
    return parse_selection(content, names), ""
