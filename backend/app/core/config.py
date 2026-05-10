from pathlib import Path
from pydantic_settings import BaseSettings

ROOT = Path(__file__).resolve().parent.parent.parent  # backend/


class Settings(BaseSettings):
    # DeepSeek (对话模型)
    deepseek_api_key: str
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_chat_model: str = "deepseek-v4-pro"

    # 文件工具白名单（逗号分隔路径）
    file_tool_whitelist: str = ""

    @property
    def chat_api_key(self) -> str:
        return self.deepseek_api_key

    @property
    def chat_base_url(self) -> str:
        return self.deepseek_base_url

    @property
    def chat_model(self) -> str:
        return self.deepseek_chat_model

    @property
    def whitelist_paths(self) -> list[str]:
        paths = [str(ROOT.parent)]  # AIChat/ 项目根目录始终在白名单
        if self.file_tool_whitelist:
            for p in self.file_tool_whitelist.split(","):
                p = p.strip()
                if p:
                    paths.append(p)
        return paths

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
