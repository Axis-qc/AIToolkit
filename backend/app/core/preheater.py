"""
独立缓存预热器
定时扫描白名单目录，全量读取文件写入缓存。
与工具层完全解耦，仅共享 Cache 实例和 key 约定。
"""
import asyncio
import os
from fnmatch import fnmatch

from app.core.cache import cache, hash_args


class CachePreheater:
    def __init__(self, interval: int = 300):
        self.interval = interval
        self._task: asyncio.Task | None = None
        self._running = False

    async def start(self) -> None:
        if self._running:
            return
        self._running = True

        file_list = self._resolve_config()
        if file_list:
            await asyncio.to_thread(self._preheat, file_list)

        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def preheat_once(self) -> None:
        file_list = self._resolve_config()
        await asyncio.to_thread(self._preheat, file_list)

    def _resolve_config(self) -> list[str]:
        from app.core.config import settings
        raw = settings.PREHEAT_FILES.strip()
        if not raw:
            return []

        result = []
        for entry in raw.split(" "):
            entry = entry.strip()
            if not entry:
                continue

            if ":" in entry:
                path_str, ext_str = entry.split(":", 1)
                exts = [e.strip() for e in ext_str.split(",") if e.strip()]
            else:
                path_str = entry
                exts = None

            abs_path = os.path.abspath(path_str)

            if os.path.isfile(abs_path):
                result.append(abs_path)
            elif os.path.isdir(abs_path):
                for root, dirs, files in os.walk(abs_path):
                    dirs[:] = [d for d in dirs
                               if not d.startswith('.')
                               and d != '__pycache__'
                               and d != 'node_modules']
                    for f in files:
                        if exts is None or any(fnmatch(f, ext) for ext in exts):
                            result.append(os.path.join(root, f))

        return result

    def _preheat(self, file_list: list[str]) -> None:
        for path in file_list:
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                self._purge(path)
                continue

            cache_key = f"tool:read_file:{hash_args({'path': path})}"
            mtime_key = f"preheat:mtime:{hash_args({'path': path})}"

            cached_mtime = cache.get(mtime_key)
            if cached_mtime is not None:
                try:
                    if float(cached_mtime) == mtime:
                        continue
                except (ValueError, TypeError):
                    pass

            try:
                with open(path, 'r', encoding='utf-8', errors='replace') as f:
                    content = f.read()
            except Exception:
                continue

            cache.set(cache_key, content)
            cache.set(mtime_key, str(mtime))

    def _purge(self, path: str) -> None:
        cache_key = f"tool:read_file:{hash_args({'path': path})}"
        mtime_key = f"preheat:mtime:{hash_args({'path': path})}"
        cache.delete(cache_key)
        cache.delete(mtime_key)

    async def _loop(self) -> None:
        while self._running:
            await asyncio.sleep(self.interval)
            if not self._running:
                break
            try:
                file_list = self._resolve_config()
                if file_list:
                    await asyncio.to_thread(self._preheat, file_list)
            except Exception:
                pass


preheater = CachePreheater()
