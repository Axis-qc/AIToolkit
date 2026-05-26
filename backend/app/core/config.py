from pathlib import Path
from pydantic_settings import BaseSettings

ROOT = Path(__file__).resolve().parent.parent.parent  # backend/


class Settings(BaseSettings):
    # ── 文件工具白名单 ──
    file_tool_whitelist: str = ""

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
