import json
import os
import re
import httpx
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/settings", tags=["settings"])

ENV_PATH = os.path.join(os.path.dirname(__file__), "..", "..", ".env")
ENV_PATH = os.path.abspath(ENV_PATH)

FETCHED_MODELS_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "data", "fetched_models.json")
FETCHED_MODELS_PATH = os.path.abspath(FETCHED_MODELS_PATH)

PROVIDERS = ["deepseek", "openai", "anthropic"]

# 前端下拉框使用的模型列表（含 label）
AVAILABLE_MODELS = [
    {"value": "deepseek-chat",      "label": "DeepSeek V3",       "provider": "deepseek"},
    {"value": "deepseek-reasoner",  "label": "DeepSeek R1",       "provider": "deepseek"},
    {"value": "deepseek-v4-pro",    "label": "DeepSeek V4 Pro",   "provider": "deepseek"},
    {"value": "gpt-4o",             "label": "GPT-4o",            "provider": "openai"},
    {"value": "gpt-4o-mini",        "label": "GPT-4o Mini",       "provider": "openai"},
    {"value": "gpt-4-turbo",        "label": "GPT-4 Turbo",       "provider": "openai"},
    {"value": "claude-3.5-sonnet",  "label": "Claude 3.5 Sonnet", "provider": "anthropic"},
    {"value": "claude-3-opus",      "label": "Claude 3 Opus",     "provider": "anthropic"},
]

BUILTIN_MODELS = {
    "deepseek": [
        {"id": "deepseek-chat"},
        {"id": "deepseek-v4-pro"},
        {"id": "deepseek-reasoner"},
    ],
    "openai": [
        {"id": "gpt-4o"},
        {"id": "gpt-4o-mini"},
        {"id": "gpt-4-turbo"},
    ],
    "anthropic": [
        {"id": "claude-3.5-sonnet"},
        {"id": "claude-3-opus"},
    ],
}

ENV_KEY_MAP = {
    "chat_provider": "CHAT_PROVIDER",
    "preheat_files": "PREHEAT_FILES",
}
for p in PROVIDERS:
    ENV_KEY_MAP[f"{p}_api_key"] = f"{p.upper()}_API_KEY"
    ENV_KEY_MAP[f"{p}_base_url"] = f"{p.upper()}_BASE_URL"
    ENV_KEY_MAP[f"{p}_chat_model"] = f"{p.upper()}_CHAT_MODEL"


class ProviderConfig(BaseModel):
    api_key: str = ""
    base_url: str = ""
    chat_model: str = ""


class SettingsOut(BaseModel):
    chat_provider: str = "deepseek"
    providers: dict[str, ProviderConfig] = Field(default_factory=dict)
    preheat_files: str = ""
    available_models: list = Field(default_factory=lambda: AVAILABLE_MODELS)
    fetched_models: dict[str, list[str]] = Field(default_factory=dict)


class SettingsIn(BaseModel):
    chat_provider: str = "deepseek"
    providers: dict[str, ProviderConfig] = Field(default_factory=dict)
    preheat_files: str = ""


def _read_env() -> dict[str, str]:
    if not os.path.exists(ENV_PATH):
        return {}
    result = {}
    with open(ENV_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, value = line.partition("=")
                result[key.strip()] = value.strip()
    return result


def _write_env(updates: dict[str, str]) -> None:
    if not os.path.exists(ENV_PATH):
        with open(ENV_PATH, "w", encoding="utf-8") as f:
            for k, v in updates.items():
                f.write(f"{k}={v}\n")
        return

    with open(ENV_PATH, "r", encoding="utf-8") as f:
        lines = f.readlines()

    updated_keys = set()
    new_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in updates:
                new_lines.append(f"{key}={updates[key]}\n")
                updated_keys.add(key)
                continue
        new_lines.append(line)

    for k, v in updates.items():
        if k not in updated_keys:
            new_lines.append(f"{k}={v}\n")

    with open(ENV_PATH, "w", encoding="utf-8") as f:
        f.writelines(new_lines)


def _read_fetched_models() -> dict[str, list[str]]:
    if not os.path.exists(FETCHED_MODELS_PATH):
        return {}
    try:
        with open(FETCHED_MODELS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_fetched_models(provider: str, model_ids: list[str]) -> None:
    os.makedirs(os.path.dirname(FETCHED_MODELS_PATH), exist_ok=True)
    data = _read_fetched_models()
    data[provider] = model_ids
    with open(FETCHED_MODELS_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


def _mask_key(key: str) -> str:
    if len(key) <= 4:
        return key
    return key[:4] + "..." + key[-4:]


def _is_masked(key: str) -> bool:
    return "..." in key or key.startswith("*")


@router.get("", response_model=SettingsOut)
async def get_settings():
    env = _read_env()
    providers = {}
    for p in PROVIDERS:
        raw_key = env.get(f"{p.upper()}_API_KEY", "")
        providers[p] = ProviderConfig(
            api_key=_mask_key(raw_key) if raw_key else "",
            base_url=env.get(f"{p.upper()}_BASE_URL", ""),
            chat_model=env.get(f"{p.upper()}_CHAT_MODEL", ""),
        )
    fetched = _read_fetched_models()
    return SettingsOut(
        chat_provider=env.get("CHAT_PROVIDER", "deepseek"),
        providers=providers,
        preheat_files=env.get("PREHEAT_FILES", ""),
        fetched_models=fetched,
    )


@router.put("")
async def update_settings(body: SettingsIn):
    env = _read_env()
    updates = {}

    # 更新 chat_provider
    if body.chat_provider in PROVIDERS:
        updates["CHAT_PROVIDER"] = body.chat_provider

    # 更新 preheat_files
    if body.preheat_files:
        updates["PREHEAT_FILES"] = body.preheat_files

    # 更新各 provider 配置
    for p in PROVIDERS:
        cfg = body.providers.get(p)
        if not cfg:
            continue
        env_key = f"{p.upper()}_API_KEY"
        if cfg.api_key and not _is_masked(cfg.api_key):
            updates[env_key] = cfg.api_key
        if cfg.base_url:
            updates[f"{p.upper()}_BASE_URL"] = cfg.base_url
        if cfg.chat_model:
            updates[f"{p.upper()}_CHAT_MODEL"] = cfg.chat_model

    try:
        _write_env(updates)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"写入 .env 失败: {e}")

    provider_changed = "CHAT_PROVIDER" in updates
    msg = "配置已保存"
    if provider_changed:
        msg += "，请重启服务生效"

    env = _read_env()
    providers = {}
    for p in PROVIDERS:
        raw_key = env.get(f"{p.upper()}_API_KEY", "")
        providers[p] = ProviderConfig(
            api_key=_mask_key(raw_key) if raw_key else "",
            base_url=env.get(f"{p.upper()}_BASE_URL", ""),
            chat_model=env.get(f"{p.upper()}_CHAT_MODEL", ""),
        )

    fetched = _read_fetched_models()
    return {
        "status": "ok",
        "message": msg,
        "chat_provider": env.get("CHAT_PROVIDER", "deepseek"),
        "providers": {k: v.model_dump() for k, v in providers.items()},
        "preheat_files": env.get("PREHEAT_FILES", ""),
        "fetched_models": fetched,
    }


@router.get("/models")
async def fetch_models(provider: str = Query(..., description="deepseek / openai / anthropic")):
    if provider not in PROVIDERS:
        raise HTTPException(status_code=400, detail=f"不支持的 provider: {provider}，可选: {', '.join(PROVIDERS)}")

    env = _read_env()
    api_key = env.get(f"{provider.upper()}_API_KEY", "")
    base_url = env.get(f"{provider.upper()}_BASE_URL", "")

    if not api_key or not base_url:
        data = BUILTIN_MODELS.get(provider, [])
        data.sort(key=lambda m: m["id"])
        return {"data": data, "source": "builtin"}

    url = base_url.rstrip("/") + "/v1/models"
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        async with httpx.AsyncClient(trust_env=False) as client:
            resp = await client.get(url, headers=headers, timeout=5)
            resp.raise_for_status()
            body = resp.json()
            models = body.get("data", [])
            model_ids = sorted(
                [m["id"] for m in models if isinstance(m, dict) and m.get("id")],
            )
            _save_fetched_models(provider, model_ids)
            data = [{"id": mid} for mid in model_ids]
            return {"data": data, "source": "remote"}
    except Exception:
        data = BUILTIN_MODELS.get(provider, [])
        data.sort(key=lambda m: m["id"])
        return {"data": data, "source": "builtin"}
