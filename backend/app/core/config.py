# 应用配置管理：使用 pydantic-settings 加载 .env 配置文件，提供文件工具白名单等功能
from pathlib import Path
from pydantic_settings import BaseSettings

ROOT = Path(__file__).resolve().parent.parent.parent  # backend/


class Settings(BaseSettings):
    # ── 文件工具白名单 ──
    file_tool_whitelist: str = ""

    # ── 图谱废弃内容保留窗口（小时）──
    # 软删除后的内容超过这个时长会被 cleanup_expired 移入墓地并物理删除。
    # 整理流程是「出清单、确认、再执行」，跨天回来时窗口可能已过，按需调大。
    deprecated_retention_hours: float = 24.0

    # ── 图谱体检默认参数 ──
    dup_similarity_threshold: float = 0.85   # 高置信重复候选
    dup_watch_threshold: float = 0.80        # 待观察重复候选
    stale_days_warn: int = 90                # 过时预警阈值（天）
    stale_days_alert: int = 180              # 过时告警阈值（天）

    @property
    def whitelist_paths(self) -> list[str]:
        paths = [str(ROOT.parent)]
        if self.file_tool_whitelist:
            for p in self.file_tool_whitelist.split(","):
                p = p.strip()
                if p:
                    paths.append(p)
        return paths

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


settings = Settings()
