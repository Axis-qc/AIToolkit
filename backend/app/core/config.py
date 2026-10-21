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

    # ── 实体就地修改的历史版本数 ──
    # 软删除有 deprecated_at 可恢复，但改正文/改类型/改关系这类就地修改
    # 原本没有任何回退点，只能手动备份数据库。这里给每个实体留最近 N 版
    # 文本快照（不含 embedding），回滚走 rollback_entity。
    # 存储代价：实测实体文本字段合计约 1.1MB，N=5 时最坏约 10-16MB。
    entity_history_versions: int = 5

    # ── 图谱体检默认参数 ──
    dup_similarity_threshold: float = 0.85   # 高置信重复候选
    dup_watch_threshold: float = 0.80        # 待观察重复候选
    stale_days_warn: int = 90                # 过时预警阈值（天）
    stale_days_alert: int = 180              # 过时告警阈值（天）

    # ── 弱关联候选（用向量补「相关」类无向弱关系）──
    # 口径按 2772 条活跃实体的实测选定（2026-10-04）：
    # 互为 top-10 且余弦 >=0.70 时，产出 5098 条边、平均度 3.68、最大度 12，
    # 仍有 601 个节点孤立；0.60 档产出 35039 条、平均度 25.6，会把图冲垮。
    weak_link_top_k: int = 10
    weak_link_min_cosine: float = 0.70
    # 余弦 >= 此值直接标为可自动写入；低于它但在 min_cosine 之上进待确认。
    weak_link_auto_cosine: float = 0.75
    # 度数上限：向量给不出语义，索引/总览类条目（正文开头在罗列子条目名，
    # 而向量只吃正文前 400 字）会跟什么都像。不加上限时最高度可达数百。
    weak_link_degree_cap: int = 12
    # 疑似重复的判定口径（与 graph_health 的重复检测同源）：
    # 命中即分流到重复清单，只建边不合并会把重复节点留着。
    weak_link_dup_content_cosine: float = 0.75
    weak_link_dup_name_similarity: float = 0.80

    # ── 自我文档目录 ──
    # 表达倾向、用户认知、分歧、未解问题四份 markdown 所在目录。DSH 插件直接读
    # 这些文件注入系统提示词，MCP 只负责写入。相对路径按仓库根解析。
    self_docs_dir: str = "self"

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
