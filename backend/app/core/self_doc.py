"""自我文档：表达倾向、分歧、未解问题与用户认知四份 markdown 的读写与校验。

四份文件放在 `self/` 下，由 DSH 插件每轮直接读取注入系统提示词，因此这里是
唯一的写入通道。`style.md` 是模型对自己的定义，`user.md` 是模型对用户的理解，
两者都整段替换；另两份由模型在真实发生时追加。

写入一律先存历史快照再落盘，同一天内同一文档只保留最后一次快照，避免
反复调试刷出大量文件。单段超过字数上限直接拒绝，不静默截断——截断会让
调用方以为写成功，实际内容已经残缺。
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from .config import settings

# 四份文档的标识与文件名。user 不参与追加，见 DOC_APPENDABLE。
DOCS = {
    "style": "style.md",
    "user": "user.md",
    "disagreements": "disagreements.md",
    "open_questions": "open-questions.md",
}

# 可追加的文档。style 与 user 是整段替换的定稿，不参与追加。
DOC_APPENDABLE = ("disagreements", "open_questions")

# 单段字数上限（不含空白字符）。
MAX_CHARS = 1500


class SelfDocError(Exception):
    """自我文档操作失败，消息直接回给调用方。"""


def _root() -> Path:
    """自我文档目录。相对路径按 backend 的上级目录解析。"""
    raw = settings.self_docs_dir or "self"
    p = Path(raw)
    if not p.is_absolute():
        p = Path(__file__).resolve().parent.parent.parent.parent / raw
    return p


def _path(doc: str) -> Path:
    if doc not in DOCS:
        raise SelfDocError(f"未知文档: {doc}，可选 {', '.join(DOCS)}")
    return _root() / DOCS[doc]


def _history_dir() -> Path:
    return _root() / ".history"


def _count(text: str) -> int:
    """有效字数：不计空白。"""
    return len("".join(text.split()))


def _read(doc: str) -> str:
    p = _path(doc)
    if not p.exists():
        return ""
    try:
        return p.read_text(encoding="utf-8")
    except OSError as exc:
        raise SelfDocError(f"读取 {p.name} 失败: {exc}") from exc


def _snapshot(doc: str, old: str) -> None:
    """把旧内容存进历史。同一天同一文档只留最后一次快照。"""
    if not old.strip():
        return
    hist = _history_dir()
    hist.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%Y%m%d")
    for stale in hist.glob(f"{doc}.{today}-*.md"):
        try:
            stale.unlink()
        except OSError:
            pass
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = hist / f"{doc}.{stamp}.md"
    try:
        target.write_text(old, encoding="utf-8")
    except OSError as exc:
        raise SelfDocError(f"写历史快照失败: {exc}") from exc


def _write(doc: str, new: str) -> None:
    path = _path(doc)
    old = _read(doc)
    _snapshot(doc, old)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(new, encoding="utf-8")
    except OSError as exc:
        raise SelfDocError(f"写入 {path.name} 失败: {exc}") from exc


def read_docs(doc: str | None = None) -> dict:
    """读一份或全部。返回内容、字数与上限。"""
    if doc is not None:
        text = _read(doc)
        return {
            "doc": doc,
            "file": DOCS[doc],
            "content": text,
            "chars": _count(text),
            "max_chars": MAX_CHARS,
        }
    items = []
    total = 0
    for key in DOCS:
        text = _read(key)
        chars = _count(text)
        total += chars
        items.append(
            {
                "doc": key,
                "file": DOCS[key],
                "content": text,
                "chars": chars,
                "max_chars": MAX_CHARS,
            }
        )
    return {"docs": items, "total_chars": total, "root": str(_root())}


def append_entry(doc: str, entry: str) -> dict:
    """追加一条到可追加文档。"""
    if doc not in DOC_APPENDABLE:
        raise SelfDocError(
            f"{doc} 不可追加，只能整段替换。可追加的只有 {', '.join(DOC_APPENDABLE)}"
        )
    text = (entry or "").strip()
    if not text:
        raise SelfDocError("追加内容为空")
    if text.startswith("#"):
        raise SelfDocError("追加条目不能以 # 开头，条目内不要再套标题")

    old = _read(doc)
    joiner = "" if old.endswith("\n") or not old else "\n"
    new = f"{old}{joiner}{text}\n"

    chars = _count(new)
    if chars > MAX_CHARS:
        raise SelfDocError(
            f"{DOCS[doc]} 将达 {chars} 字，超过上限 {MAX_CHARS} 字"
            f"（当前 {_count(old)} 字，本条 {_count(text)} 字）。"
            f"请先精简或合并旧条目再追加。"
        )
    _write(doc, new)
    return {"doc": doc, "chars": chars, "max_chars": MAX_CHARS, "appended": text}


def update_doc(doc: str, content: str) -> dict:
    """整段替换。空内容表示清空该段。"""
    new = "" if content is None else content
    chars = _count(new)
    if chars > MAX_CHARS:
        raise SelfDocError(
            f"{DOCS[doc]} 将达 {chars} 字，超过上限 {MAX_CHARS} 字，拒绝写入。"
            f"请精简到上限以内。"
        )
    _write(doc, new)
    return {"doc": doc, "chars": chars, "max_chars": MAX_CHARS}


def list_history(doc: str | None = None) -> dict:
    """列出历史快照，按时间倒序。"""
    hist = _history_dir()
    if not hist.exists():
        return {"root": str(hist), "items": []}
    pattern = f"{doc}.*.md" if doc else "*.md"
    files = sorted(hist.glob(pattern), key=lambda p: p.name, reverse=True)
    return {
        "root": str(hist),
        "items": [
            {"file": p.name, "size": p.stat().st_size}
            for p in files[:50]
        ],
    }
