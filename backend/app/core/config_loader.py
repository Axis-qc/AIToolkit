from pathlib import Path
import yaml

_CONFIG_PATH = Path(__file__).resolve().parent / "graph_config.yaml"
_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"
_config = None


def _load():
    global _config
    if _config is None:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            _config = yaml.safe_load(f)
    return _config


def get_centers() -> list[dict]:
    return _load()["centers"]


def get_center(type_: str, name: str) -> dict | None:
    for c in _load()["centers"]:
        if c["type"] == type_ and c["name"] == name:
            return c
    return None


def get_center_ids() -> set[str]:
    return {f"{c['type']}|{c['name']}" for c in _load()["centers"]}


def get_root_node_ids() -> list[str]:
    return [f"{c['type']}|{c['name']}" for c in _load()["centers"] if c.get("is_root")]


def get_entity_types() -> dict:
    return _load().get("entity_types", {})


def get_entity_config(type_: str) -> dict | None:
    return _load().get("entity_types", {}).get(type_)


def get_render_config(type_: str) -> dict:
    cfg = _load()
    et = cfg.get("entity_types", {}).get(type_)
    if et and "render" in et:
        return et["render"]
    return cfg["default_render"]


def get_type_icon(type_: str) -> str:
    et = _load().get("entity_types", {}).get(type_)
    if et and "icon" in et:
        return et["icon"]
    return _load()["default_render"].get("default_icon", "\U0001F4CC")


def get_type_label_prefix(type_: str) -> str:
    et = _load().get("entity_types", {}).get(type_)
    if et and "label_prefix" in et:
        return et["label_prefix"]
    return type_


def get_injection_config() -> dict:
    return _load()["injection"]


def get_prompt_path(key: str) -> Path:
    rel = _load()["prompts"][key]
    return _PROMPTS_DIR / rel


def render_prompt(key: str, **variables) -> str:
    path = get_prompt_path(key)
    content = path.read_text(encoding="utf-8")
    for k, v in variables.items():
        content = content.replace(f"{{{{{k}}}}}", str(v))
    return content


def build_center_list_md() -> str:
    lines = []
    for c in _load()["centers"]:
        lines.append(f"- {c['icon']} **{c['type']}|{c['name']}** \u2014 {c['description']}")
    return "\n".join(lines)


def get_center_categories(type_: str, name: str) -> list[dict]:
    """获取某个中心的分类节点列表"""
    center = get_center(type_, name)
    return center.get("categories", []) if center else []


def get_all_categories() -> list[dict]:
    """获取所有中心的分类节点，附带父中心信息"""
    result = []
    for c in _load()["centers"]:
        for cat in c.get("categories", []):
            result.append({
                "center_type": c["type"],
                "center_name": c["name"],
                "entity_name": f"{c['name']}·{cat['name']}",
                "name": cat["name"],
                "icon": cat.get("icon", "\U0001F4C1"),
                "description": cat.get("description", ""),
            })
    return result


def build_category_list_md() -> str:
    """生成分类节点 Markdown 列表，用于注入提示词"""
    lines = []
    for c in _load()["centers"]:
        cats = c.get("categories", [])
        if cats:
            cat_names = "、".join(f"{c['name']}·{cat['name']}" for cat in cats)
            lines.append(f"- {c['icon']} **{c['type']}|{c['name']}** 的分类: {cat_names}")
    return "\n".join(lines)


def get_category_center_map() -> dict:
    """返回 {分类实体名: {center_type, center_name}} 映射"""
    result = {}
    for c in _load()["centers"]:
        for cat in c.get("categories", []):
            result[f"{c['name']}·{cat['name']}"] = {
                "center_type": c["type"],
                "center_name": c["name"],
            }
    return result


def get_root_relations() -> list[dict]:
    return _load().get("root_relations", [])


def get_all_config() -> dict:
    return _load()
