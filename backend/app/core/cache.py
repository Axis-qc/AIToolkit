"""
请求级缓存 + 工具结果缓存
LRU 淘汰 + TTL 过期 + pickle 持久化
"""
import hashlib
import json
import pickle
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# ── 配置 ──────────────────────────────────────────────

CACHE_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "cache.pkl"

MAX_MEMORY_BYTES = 1024 * 1024 * 1024   # 1GB 上限
MAX_ENTRIES = 10000                      # 条目数上限

TTL_TOOL_RESULT = 365 * 24 * 3600       # 1 年
TTL_REQUEST = 365 * 24 * 3600           # 1 年

# 写操作工具名集合
WRITE_TOOLS = {"write_file", "edit_file", "save_to_graph", "delete_from_graph"}


# ── 数据结构 ──────────────────────────────────────────

@dataclass
class CacheEntry:
    value: Any
    ttl: float
    created_at: float
    size: int = 0


# ── 缓存类 ────────────────────────────────────────────

class Cache:
    def __init__(self):
        self._lock = threading.Lock()
        self._store: OrderedDict[str, CacheEntry] = OrderedDict()
        self._total_size = 0

    # ── 基本存取 ──

    def get(self, key: str) -> Any | None:
        """获取缓存，命中时刷新 LRU 顺序。过期返回 None 并删除。"""
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            if time.time() - entry.created_at > entry.ttl:
                self._remove(key)
                return None
            self._store.move_to_end(key)
            return entry.value

    def set(self, key: str, value: Any, ttl: float = 365*24*3600) -> None:
        """写入缓存，自动触发淘汰检查。"""
        with self._lock:
            size = len(json.dumps(value, ensure_ascii=False, default=str))
            if key in self._store:
                self._total_size -= self._store[key].size
            entry = CacheEntry(value=value, ttl=ttl, created_at=time.time(), size=size)
            self._store[key] = entry
            self._store.move_to_end(key)
            self._total_size += size
            self._evict()

    # ── 失效 ──

    def invalidate_tool(self, tool_name: str) -> int:
        """失效指定工具的所有结果缓存。返回清除条数。"""
        return self._remove_by_prefix(f"tool:{tool_name}:")

    def invalidate_all_tools(self) -> int:
        return self._remove_by_prefix("tool:")

    def invalidate_all_requests(self) -> int:
        return self._remove_by_prefix("req:")

    def invalidate_write(self, tool_name: str) -> None:
        """写操作触发关联失效（全量策略）。"""
        if tool_name in ("write_file", "edit_file"):
            self.invalidate_tool("read_file")
            self.invalidate_tool("search_files")
            self.invalidate_tool("search_content")
        elif tool_name in ("save_to_graph", "delete_from_graph"):
            self.invalidate_tool("search_memory")

    # ── 持久化 ──

    def save_to_disk(self) -> None:
        """持久化到 pickle 文件，保存前清理过期条目。"""
        with self._lock:
            now = time.time()
            expired = [k for k, e in self._store.items() if now - e.created_at > e.ttl]
            for k in expired:
                self._remove(k)
            data = {k: e for k, e in self._store.items()}
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(CACHE_FILE, "wb") as f:
            pickle.dump(data, f)

    def load_from_disk(self) -> None:
        """从 pickle 文件恢复，自动丢弃已过期条目。"""
        if not CACHE_FILE.exists():
            return
        try:
            with open(CACHE_FILE, "rb") as f:
                data = pickle.load(f)
        except Exception:
            return
        now = time.time()
        with self._lock:
            loaded = 0
            for k, e in data.items():
                if now - e.created_at > e.ttl:
                    continue
                self._store[k] = e
                self._total_size += e.size
                loaded += 1
            self._store = OrderedDict(
                sorted(self._store.items(), key=lambda x: x[1].created_at)
            )
            self._evict()

    # ── 内部方法 ──

    def _remove(self, key: str) -> None:
        if key in self._store:
            self._total_size -= self._store[key].size
            del self._store[key]

    def _remove_by_prefix(self, prefix: str) -> int:
        keys = [k for k in self._store if k.startswith(prefix)]
        for k in keys:
            self._remove(k)
        return len(keys)

    def _evict(self) -> None:
        while self._store and (
            self._total_size > MAX_MEMORY_BYTES or len(self._store) > MAX_ENTRIES
        ):
            oldest = next(iter(self._store))
            self._remove(oldest)


# ── 全局单例 ──

cache = Cache()


# ── 工具函数 ──

def hash_messages(messages: list[dict]) -> str:
    """计算 messages 的稳定 hash，仅取 role + content，排除 tool_call_id 等变动字段。"""
    stable = json.dumps(
        [{"role": m.get("role", ""), "content": m.get("content", "")} for m in messages],
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(stable.encode()).hexdigest()[:16]


def hash_args(args: dict) -> str:
    """计算工具参数的稳定 hash。"""
    stable = json.dumps(args, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(stable.encode()).hexdigest()[:16]


def hash_content(text: str) -> str:
    """计算单条文本内容的稳定 hash。"""
    return hashlib.sha256(text.encode()).hexdigest()[:16]
