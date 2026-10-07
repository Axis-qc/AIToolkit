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

    # ── 意向抽词（把用户口语输入转成检索关键词，供意向检索的字面臂使用）──
    # 默认走本机 workboddy 代理，零额外费用。换模型只需改 .env 这三项。
    intent_extract_enabled: bool = True
    intent_extract_base_url: str = "http://127.0.0.1:7863/v1"
    # 本机代理不校验 key 真伪，填任意非空值即可；换云端端点时填真实 key。
    intent_extract_api_key: str = "my_secret_key_123"
    # 实测（2026-09-25）：cn:deepseek-v4.1-flash 抽词质量最好（4/4 正确、闲聊返回空数组），
    # 但该模型的本机代理账号池会间歇性耗尽（HTTP 503 no_healthy_account）。
    # 默认改用同系列、实测稳定的 cn:deepseek-v4-flash。
    # 其余实测：cn:hy3 是推理型会烧光 max_tokens 导致正文为空；cn:auto 与 cn:glm-5.3 也返回空正文。
    intent_extract_model: str = "cn:deepseek-v4-flash"
    intent_extract_timeout_ms: int = 6000
    # 推理型模型会把 token 全用在 reasoning_content 上导致正文为空，
    # 因此不能给小值（给 256 时 cn:hy3 抽词成功率 1/4）。
    intent_extract_max_tokens: int = 1024

    # ── 选择式抽词（把图谱条目名当选项让模型挑，优于自由生成）──
    # 实测：自由生成关键词真实链路 30/46，选择式 42/46（与人工预写上限持平）。
    # 因为模型猜不中本图谱的条目措辞，但能在选项里认出它们。
    intent_select_enabled: bool = True
    # 选项很长（673 条约 1 万 token），给足输出空间防止推理型模型把 token
    # 烧在思考上导致正文为空。
    #
    # 实测（2026-09-25）2048 不够：cn:deepseek-v4-flash 走推理路径时 reasoning
    # 单次可吃 3500-3900 token，正文还没开始写就 finish_reason=length，
    # 53 条评测集里至少 4 条因此静默降级到生成式抽词（那条路径只有 30/46），
    # 把整体成绩从 42/46 拖到 35/46。调到 10240 留出充裕余量。
    intent_select_max_tokens: int = 10240
    # 选择式比抽词慢（多一次长上下文往返），超时给宽一些。
    intent_select_timeout_ms: int = 20000

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
