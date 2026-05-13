from pathlib import Path
from pydantic_settings import BaseSettings

ROOT = Path(__file__).resolve().parent.parent.parent  # backend/


class Settings(BaseSettings):
    # ── 当前激活的 provider ──
    chat_provider: str = "deepseek"

    # ── DeepSeek ──
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_chat_model: str = "deepseek-chat"

    # ── OpenAI ──
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_chat_model: str = "gpt-4o"

    # ── Anthropic ──
    anthropic_api_key: str = ""
    anthropic_base_url: str = "https://api.anthropic.com"
    anthropic_chat_model: str = "claude-3.5-sonnet"

    # ── 文件工具 ──
    file_tool_whitelist: str = ""
    PREHEAT_FILES: str = ""

    # ═══════════════════════════════════════════
    # 动态属性：指向当前激活的 provider
    # chat.py / graph.py / memory.py 无需改动
    # ═══════════════════════════════════════════

    @property
    def chat_api_key(self) -> str:
        return getattr(self, f"{self.chat_provider}_api_key", "")

    @property
    def chat_base_url(self) -> str:
        return getattr(self, f"{self.chat_provider}_base_url", "")

    @property
    def chat_model(self) -> str:
        return getattr(self, f"{self.chat_provider}_chat_model", "")

    @property
    def whitelist_paths(self) -> list[str]:
        paths = [str(ROOT.parent)]  # AIChat/ 项目根目录始终在白名单
        if self.file_tool_whitelist:
            for p in self.file_tool_whitelist.split(","):
                p = p.strip()
                if p:
                    paths.append(p)
        return paths

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


settings = Settings()
