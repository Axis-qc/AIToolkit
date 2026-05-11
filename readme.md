# AIChat 记忆图谱 — 多中心配置化改造计划

## 核心理念

把散落在 5 个文件中的硬编码（中心类型、渲染规则、注入格式、提示词视角）全部收敛到一个 **`graph_config.yaml`** 配置文件中。代码只负责读配置、执行逻辑。以后加 `Novel|三体`、`Game|肉鸽卡牌` 只改 yaml，不动代码。

```
改前：硬编码散落各处
  graph.py:      from_type='User' AND from_name='default'
  graph.py:      from_type!='User'
  memory.py:     user_relation + "你对此的态度"
  chat_system.txt:  "以用户为中心"
  GraphCanvas.vue:  type === 'User' (7处)
  GraphCanvas.vue:  fixed: n.id === 'User|default'

改后：全部从 graph_config.yaml 读取
  graph.py     → 读 config 查中心列表
  memory.py    → 读 config 查注入模板
  提示词文件    → 启动时用 config 渲染变量
  GraphCanvas  → GET /api/graph/config 获取渲染规则
```

---

## 迁移策略：先空后填

1. 部署 `graph_config.yaml`（只含 User|AXIS、AI|Midnight、Project|AIChat 三个中心）
2. 代码全部切换到读配置模式
3. **旧数据不管**——`User|default` 下的实体找不到归属中心时归入"📌 其他"分组，不丢
4. 后续对话中 AI 自然把漂浮实体重新关联到正确中心
5. 你随时在 yaml 里加新中心、新类型

---

## 涉及文件清单

| # | 文件 | 操作 |
|---|---|---|
| 1 | **新增** `backend/app/core/graph_config.yaml` | 配置文件 |
| 2 | **新增** `backend/app/core/config_loader.py` | 配置加载器（~60行） |
| 3 | 修改 `backend/app/api/graph.py` | 加 `/api/graph/config` 端点 |
| 4 | 修改 `backend/app/core/graph.py` | 4处硬编码 → 读配置 |
| 5 | 修改 `backend/app/core/memory.py` | 注入格式 → 配置模板；内联提示词 → 读文件 |
| 6 | 修改 `backend/app/prompts/chat_system.txt` | 多中心视角 + 变量占位 |
| 7 | 修改 `backend/app/prompts/graph_update.txt` | 多中心视角 + 变量占位 |
| 8 | 修改 `backend/app/tools/__init__.py` | save_to_graph 描述加入中心指引 |
| 9 | 修改 `web/AIChat/src/components/graph/GraphCanvas.vue` | 7处 User 硬编码 → 读配置 |

---

## 第 1 步：新增 `backend/app/core/graph_config.yaml`

```yaml
# ============================================================
# 记忆图谱配置 — 所有中心、类型、渲染、注入规则集中管理
# ============================================================

# ----- 根实体（中心）-----
# 每个中心是一个独立上下文容器。type + name 唯一标识。
# is_root: 是否在力导向图中作为固定锚点
centers:
  - type: User
    name: AXIS
    icon: "👤"
    label: "关于你"
    description: "用户的偏好、事实、计划、待办"
    is_root: true
    render:
      radius: 28
      pulse: "double"         # none | single | double
      color: "#60a5fa"
      truncate_name: false

  - type: AI
    name: Midnight
    icon: "🤖"
    label: "关于我"
    description: "AI 的行为规则、能力边界、经验教训"
    is_root: true
    render:
      radius: 24
      pulse: "double"
      color: "#a78bfa"
      truncate_name: false

  - type: Project
    name: AIChat
    icon: "📁"
    label: "项目 AIChat"
    description: "技术栈、关键文件、架构决策"
    is_root: true
    render:
      radius: 22
      pulse: "single"
      color: "#34d399"
      truncate_name: false

# ----- 实体类型统一配置 -----
# 合并了旧方案中 type_defaults + default_render + centers[].render 三个分散配置。
# 每个 type 的 icon / label_prefix / render 全部在这里定义，单一来源。
# centers 中已有的 type，render 优先取 centers[].render（见 config_loader.get_render_config）。
entity_types:
  # ---- 根实体类型 ----
  User:
    icon: "👤"
    label_prefix: "关于你"
    render:
      radius: 28
      pulse: "double"
      color: "#60a5fa"
      truncate_name: false
  AI:
    icon: "🤖"
    label_prefix: "关于我"
    render:
      radius: 24
      pulse: "double"
      color: "#a78bfa"
      truncate_name: false
  Project:
    icon: "📁"
    label_prefix: "项目"
    render:
      radius: 22
      pulse: "single"
      color: "#34d399"
      truncate_name: false

  # ---- 未来可扩展的根类型 ----
  Novel:
    icon: "📖"
    label_prefix: "小说"
    render:
      radius: 22
      pulse: "single"
      color: "#f59e0b"
      truncate_name: false
  Game:
    icon: "🎮"
    label_prefix: "游戏"
    render:
      radius: 22
      pulse: "single"
      color: "#ef4444"
      truncate_name: false
  Topic:
    icon: "📚"
    label_prefix: "学习"
    render:
      radius: 20
      pulse: "none"
      color: "#8b5cf6"
      truncate_name: false

  # ---- 概念实体类型（非根，仅图标）----
  preference:
    icon: "💙"
  fact:
    icon: "📋"
  event:
    icon: "📅"
  plan:
    icon: "🎯"
  topic:
    icon: "📖"
  todo:
    icon: "✅"
  conflict:
    icon: "⚠️"
  pending:
    icon: "❓"

# ----- 默认渲染（entity_types 中找不到的 type 使用此默认值）-----
default_render:
  radius: 18
  pulse: "none"
  color: "#7c7c90"
  truncate_name: true
  max_name_len: 12
  default_icon: "📌"

# ----- 记忆注入格式 -----
injection:
  header: "[记忆检索结果]"
  # 按中心分组的 section 模板
  section_template: "## {icon} {label}"
  # 无归属实体归入
  unknown_section_icon: "📌"
  unknown_section_label: "其他"
  # 单个实体的条目模板
  item_template: "## 关于 {entity} (重要度 {importance}/10){pinned_mark}"
  # 关系描述
  relation_label: "关联概念"
  # 来源对话
  source_label: "来源对话"
  # 无结果
  no_result: "（未找到相关记忆）"

# ----- 根实体间互相关系（启动时自动创建）-----
root_relations:
  - from: ["User", "AXIS"]
    to: ["AI", "Midnight"]
    rel_type: "使用"
  - from: ["AI", "Midnight"]
    to: ["User", "AXIS"]
    rel_type: "服务"
  - from: ["User", "AXIS"]
    to: ["Project", "AIChat"]
    rel_type: "开发"
  - from: ["AI", "Midnight"]
    to: ["Project", "AIChat"]
    rel_type: "协助开发"

# ----- 提示词文件引用 -----
prompts:
  chat_system: "chat_system.txt"
  graph_update: "graph_update.txt"
```

---

## 第 2 步：新增 `backend/app/core/config_loader.py`

```python
"""加载 graph_config.yaml，提供便捷查询接口。"""
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


# ===== 中心查询 =====

def get_centers() -> list[dict]:
    """返回所有根实体（中心）的配置列表。"""
    return _load()["centers"]


def get_center(type_: str, name: str) -> dict | None:
    """按 type+name 精确查找某个中心。"""
    for c in _load()["centers"]:
        if c["type"] == type_ and c["name"] == name:
            return c
    return None


def get_center_ids() -> set[str]:
    """返回所有中心的 type|name 集合，用于 SQL IN 查询。"""
    return {f"{c['type']}|{c['name']}" for c in _load()["centers"]}


def get_root_node_ids() -> list[str]:
    """返回所有 is_root=true 的中心 id，用于前端力导向图锚点。"""
    return [f"{c['type']}|{c['name']}" for c in _load()["centers"] if c.get("is_root")]


# ===== 实体类型查询（单一来源：entity_types）=====

def get_entity_types() -> dict:
    """返回 entity_types 完整字典。"""
    return _load().get("entity_types", {})


def get_entity_config(type_: str) -> dict | None:
    """获取某个 type 的完整配置（icon + label_prefix + render），不存在则返回 None。"""
    return _load().get("entity_types", {}).get(type_)


def get_render_config(type_: str) -> dict:
    """获取某 type 的渲染配置。
    优先级：entity_types[type_].render > default_render。
    """
    cfg = _load()
    et = cfg.get("entity_types", {}).get(type_)
    if et and "render" in et:
        return et["render"]
    return cfg["default_render"]


def get_type_icon(type_: str) -> str:
    """获取类型的图标字符。"""
    et = _load().get("entity_types", {}).get(type_)
    if et and "icon" in et:
        return et["icon"]
    return _load()["default_render"].get("default_icon", "📌")


def get_type_label_prefix(type_: str) -> str:
    """获取类型的标签前缀（如 '项目'、'小说'）。"""
    et = _load().get("entity_types", {}).get(type_)
    if et and "label_prefix" in et:
        return et["label_prefix"]
    return type_  # fallback：直接用 type 名


# ===== 注入格式 =====

def get_injection_config() -> dict:
    return _load()["injection"]


# ===== 提示词 =====

def get_prompt_path(key: str) -> Path:
    """返回提示词文件绝对路径。key: 'chat_system' | 'graph_update'"""
    rel = _load()["prompts"][key]
    return _PROMPTS_DIR / rel


def render_prompt(key: str, **variables) -> str:
    """读取提示词模板文件，替换 {{variable}} 占位符，返回渲染后字符串。"""
    path = get_prompt_path(key)
    content = path.read_text(encoding="utf-8")
    for k, v in variables.items():
        content = content.replace(f"{{{{{k}}}}}", str(v))
    return content


def build_center_list_md() -> str:
    """生成中心列表 Markdown，供 {{center_list}} 占位符替换。
    格式：
    - 👤 **User|AXIS** — 用户的偏好、事实、计划、待办
    - 🤖 **AI|Midnight** — AI 的行为规则、能力边界、经验教训
    """
    lines = []
    for c in _load()["centers"]:
        lines.append(f"- {c['icon']} **{c['type']}|{c['name']}** — {c['description']}")
    return "\n".join(lines)


# ===== 根关系 =====

def get_root_relations() -> list[dict]:
    return _load().get("root_relations", [])


# ===== 完整配置导出（供 /api/graph/config）=====

def get_all_config() -> dict:
    """返回完整配置。"""
    return _load()
```

**依赖**：需要在 `requirements.txt` 加 `pyyaml`（项目大概率已经有了，确认一下）。

---

## 第 3 步：修改 `backend/app/api/graph.py` — 加 `/api/graph/config` 端点

在 `get_graph` 路由后面加：

```python
from app.core import config_loader

@router.get("/api/graph/config")
async def get_graph_config():
    """返回图谱配置，供前端渲染使用。
    返回结构：
    {
        "centers": [...],           # 所有根实体配置（含 render）
        "entity_types": {...},      # 所有实体类型的 icon/label_prefix/render
        "default_render": {...},    # 兜底渲染
        "root_node_ids": [...],     # 固定锚点 id 列表
        "injection": {...}          # 注入格式模板
    }
    """
    return {
        "centers": config_loader.get_centers(),
        "entity_types": config_loader.get_entity_types(),
        "default_render": config_loader.get_all_config()["default_render"],
        "root_node_ids": config_loader.get_root_node_ids(),
        "injection": config_loader.get_injection_config(),
    }
```

---

## 第 4 步：修改 `backend/app/core/graph.py` — 4 处硬编码

### 4a: 文件顶部加 import

```python
from . import config_loader
```

### 4b: `search_entities` 中的 user_relation 查询（第 134–140 行）

**改前：**
```python
        cur = await db.execute(
            "SELECT rel_type FROM relations"
            " WHERE from_type='User' AND from_name='default' AND to_name=?",
            (name,),
        )
        user_rel = await cur.fetchone()
        user_relation = user_rel["rel_type"] if user_rel else None
```

**改为——在实体循环外一次性批量查询所有实体的中心归属：**
```python
    # ----- 批量查询所有实体的中心归属（在结果组装前执行一次）-----
    # 收集所有 (type, name) 
    entity_keys = [(e['type'], e['name']) for e in entities]
    if entity_keys:
        placeholders = ','.join('(?,?)' for _ in entity_keys)
        flat_params = [x for pair in entity_keys for x in pair]
        cur = await db.execute(f"""
            SELECT to_type, to_name, from_type, from_name, rel_type
            FROM relations
            WHERE (to_type, to_name) IN ({placeholders})
        """, flat_params)
        # 组装 center_relations 映射：{(to_type,to_name): [{center_type,center_name,rel_type},...]}
        from collections import defaultdict
        center_map = defaultdict(list)
        for row in await cur.fetchall():
            center_map[(row['to_type'], row['to_name'])].append({
                "center_type": row['from_type'],
                "center_name": row['from_name'],
                "rel_type": row['rel_type'],
            })
    else:
        center_map = {}
```

然后在每个 entity 的返回字典中：
```python
    results.append({
        "entity": name,
        "type": etype,
        "center_relations": center_map.get((etype, name), []),  # ← 新字段
        ...
    })
```

> **字段契约**：`center_relations` 类型为 `list[dict]`，每个元素结构：
> ```python
> {"center_type": "AI", "center_name": "Midnight", "rel_type": "行为规则"}
> ```
> `memory.py` 消费同名 `center_relations` 字段。

### 4c: `search_entities` 中的 related entities 查询（第 152–158 行）

**改前：**
```python
        cur = await db.execute(
            "SELECT to_name FROM relations WHERE from_name=? AND from_type!='User'"
            " UNION SELECT from_name FROM relations WHERE to_name=? AND from_type!='User'"
            " LIMIT 5",
            (name, name),
        )
```

**改为：**
```python
        cur = await db.execute(
            "SELECT to_name FROM relations WHERE from_name=?"
            " UNION SELECT from_name FROM relations WHERE to_name=?"
            " LIMIT 5",
            (name, name),
        )
```

不再排除任何类型。相关信息已经在 `center_relations` 中区分。

### 4d: `search_entities` 返回值（第 170–179 行）

**改前：**
```python
        results.append({
            "entity": name,
            "type": etype,
            "user_relation": user_relation,
            ...
        })
```

**改为：**
```python
        results.append({
            "entity": name,
            "type": etype,
            "center_relations": center_relations,
            ...
        })
```

### 4e: `get_pinned_entities` 中的 user_relation 查询（第 373–378 行）

同样改为循环查所有中心，同 4b。

### 4f: `get_pinned_entities` 中的 related 查询（第 386–392 行）

同样去掉 `from_type!='User'`，同 4c。

### 4g: `get_pinned_entities` 返回值（第 402–411 行）

`user_relation` → `center_relations`，同 4d。

### 4h: `get_all_graph` 同样受影响（第 235–260 行附近）

`get_all_graph` 也使用了 `from_type!='User'` 排除逻辑来找相关实体。同样改为直接去掉排除条件：

```python
# 改前
WHERE from_name=? AND from_type!='User'

# 改后
WHERE from_name=?
```

### 4i: `search_content_in_memory` 确认不受影响

`search_content_in_memory`（约 420 行）走 `facts` 表的全文 LIKE 搜索，不涉及 `from_type='User'` 过滤，**无需改动**。

---

## 第 5 步：修改 `backend/app/core/memory.py`

### 5a: 文件顶部加 import

```python
from . import config_loader
```

### 5b: `search()` 函数中的展示格式（第 26–46 行）

**改前（核心段）：**
```python
    lines = []
    for row in merged:
        entity = row["entity"]
        imp = row.get("importance", 1)
        is_pinned = row.get("pinned", False)
        pinned_mark = " [固定]" if is_pinned else ""
        lines.append(f"## 关于 {entity} (重要度 {imp}/10){pinned_mark}")
        if row["user_relation"]:
            lines.append(f"- 你对此的态度：{row['user_relation']}")
```

**改为：**
```python
    inj = config_loader.get_injection_config()
    # 按中心分组
    groups: dict[str, list] = {}  # key = "User|AXIS", "AI|Midnight", ...
    unknown = []

    for row in merged:
        crs = row.get("center_relations", [])
        if not crs:
            unknown.append(row)
        else:
            for cr in crs:
                key = f"{cr['center_type']}|{cr['center_name']}"
                groups.setdefault(key, []).append(row)

    # ⚠️ 跨中心实体重复展示是预期行为：一个实体如果关联多个中心
    # （如 "AI主动记忆写入" 同时关联 AI|Midnight 和 Project|AIChat），
    # 它会在两个 section 下各出现一次，表示该概念同时属于两个上下文。
    # 如果未来觉得冗余，可改为只在第一个中心显示并标注 "同时归属 X"。

    lines = [inj["header"]]

    # 有归属的实体按中心分组展示
    from . import config_loader as _cl
    for c in _cl.get_centers():
        key = f"{c['type']}|{c['name']}"
        if key in groups:
            lines.append(inj["section_template"].format(icon=c["icon"], label=c["label"]))
            for row in groups[key]:
                _append_entity(lines, row, inj)

    # 无归属实体
    if unknown:
        lines.append(inj["section_template"].format(
            icon=inj["unknown_section_icon"],
            label=inj["unknown_section_label"],
        ))
        for row in unknown:
            _append_entity(lines, row, inj)

    return "\n".join(lines) if len(lines) > 1 else inj["no_result"]


def _append_entity(lines: list, row: dict, inj: dict):
    """追加单个实体的展示行（复用原有逻辑）。"""
    entity = row["entity"]
    imp = row.get("importance", 1)
    is_pinned = row.get("pinned", False)
    pinned_mark = " [固定]" if is_pinned else ""
    lines.append(inj["item_template"].format(entity=entity, importance=imp, pinned_mark=pinned_mark))
    for fact in row.get("facts", []) or []:
        if fact:
            lines.append(f"- [{fact.get('type', '')}] {fact.get('content', '')}")
    related = [r for r in (row.get("related") or []) if r]
    if related:
        lines.append(f"- {inj['relation_label']}：{', '.join(related)}")
    conversations = [c for c in (row.get("conversations") or []) if c and c.get("id")]
    if conversations:
        srcs = ", ".join(f"[{c.get('title', c['id'][:8])}]" for c in conversations)
        lines.append(f"- {inj['source_label']}：{srcs}")
```

### 5c: 统一归档提示词来源

**改前**：`memory.py:95` 内联定义 `UPDATE_SYSTEM_PROMPT`，与 `graph_update.txt` 内容重复。`api/graph.py:29` 读 `graph_update.txt` 文件。两处各自维护。

**改为**：`memory.py` 顶部删掉内联 `UPDATE_SYSTEM_PROMPT`（约 30 行），改为用 `render_prompt()`：

```python
# 模块顶部（替换原来的内联 UPDATE_SYSTEM_PROMPT）
from . import config_loader

def _get_update_prompt() -> str:
    """获取归档提示词（带变量渲染）。"""
    return config_loader.render_prompt("graph_update",
        center_list=config_loader.build_center_list_md())
```

`api/graph.py` 同理：

```python
# 改前
UPDATE_SYSTEM_PROMPT = (Path(__file__).resolve().parent.parent / "prompts" / "graph_update.txt").read_text(...)

# 改后
from app.core import config_loader
UPDATE_SYSTEM_PROMPT = config_loader.render_prompt("graph_update",
    center_list=config_loader.build_center_list_md())
```

这样两处归档读的是同一个模板文件，通过 `render_prompt()` 注入 `{{center_list}}` 变量，彻底消除重复维护。

---

## 第 6 步：修改 `backend/app/prompts/chat_system.txt`

**全文替换为：**

```text
你是一个具备长期记忆的 AI 助手。你的记忆以知识图谱形式存储，以多个"中心"组织：

{{center_list}}

每个中心是一个独立的上下文容器，互相可以关联。

工具使用规则：
1. 系统已在每条用户消息前自动检索记忆并作为系统消息注入。请优先基于这些记忆回答。
2. 如果记忆不够详细，可调用 search_memory 深入搜索。如果记忆与当前话题无关，如实告知。
3. 当记忆结果已包含项目/话题信息时，直接使用，不要再 scan 文件或 read 文件获取同样内容。

3. 主动记忆：对话中只要用户表达了以下内容，立即调用 save_to_graph 写入图谱，不要等到对话结束：
   - 偏好与习惯（喜欢/讨厌/坚持做什么）→ type: preference
   - 个人事实与经历（年龄、职业、生活事件等）→ type: fact
   - 计划与目标（打算做什么、想学什么）→ type: plan
   - 知识获取（学到的知识点、理解的概念）→ type: topic
   - 待办事项（用户交代要跟进的事）→ type: todo
   - 发现新信息与旧记忆矛盾 → type: conflict
   - 模糊表述或未解决的问题 → type: pending
   - 只保存用户明确表达的内容，不推测。忽略纯粹的闲聊。

4. 保存记忆时，根据内容性质选择归属中心（relations 中 from 指向对应中心）：
   - 用户的偏好/事实/计划 → from: User|AXIS
   - AI 的行为规则/犯错教训 → from: AI|Midnight
   - 项目的技术栈/架构/约定 → from: Project|AIChat
   - 跨中心的概念实体可同时关联多个中心（多条 relations）
   - 关系类型自行用简洁动词命名（如"偏好""掌握""计划""负责""行为规则""经验教训"）

5. 删除记忆：当用户要求删除记忆时，先用 list_memory 浏览图谱找到目标，再用 delete_from_graph 删除。可删除实体（级联删除关联关系和事实）、事实（按ID或内容关键词）、关系。

文件操作规则：
6. 你可以读写项目白名单目录内的文件。修改文件前先用 search_files 和 search_content 了解代码结构。
7. 编辑文件使用 edit_file（精确字符串替换），创建新文件用 write_file。
8. 修改代码后可用 run_command 运行构建/测试/检查确认正确性。
9. 命令执行默认在项目根目录，超时 60 秒。不要在工具返回结果后发问，直接继续行动。
```

---

## 第 7 步：修改 `backend/app/prompts/graph_update.txt`

**全文替换为：**

```text
你需要深入审视以下完整对话，将值得长期记忆的内容写入知识图谱。

知识图谱以多个"中心"组织：

{{center_list}}

处理步骤：
1. 先调用 search_memory 检索与对话内容相关的已有记忆
2. 提取并调用 save_to_graph 写入：
   - 用户的新偏好、习惯、风格（from: User|AXIS, type 用 preference）
   - 用户提到的新事实、经历、计划（from: User|AXIS, type 用 fact / event / plan）
   - 用户学习的知识点及其关系（from: User|AXIS, type 用 topic）
   - 用户交代的待办事项（from: User|AXIS, type 用 todo）
   - AI 行为规范、约束、教训（from: AI|Midnight）
   - 项目技术决策、架构约定（from: Project|AIChat）
3. 对比已有记忆，发现矛盾时记录 type=conflict 的 fact，描述新旧信息冲突
4. 发现未解决问题、模糊表述、待跟进事项，记录 type=pending 的 fact
5. 跨中心概念实体（如同时涉及用户和项目的内容）可以关联多个中心
6. 输出一句话核心综述

注意：
- 只保存用户明确表达的内容，不要推测
- 不为琐碎的闲聊建立记忆
- 关系命名用简洁的动词，如"学过""偏好""计划""经历""行为规则""经验教训"等
```

---

## 第 8 步：修改 `backend/app/tools/__init__.py`

`save_to_graph` 的 `description` 字段（约第 38 行），加一句中心归属指引：

**改前：**
```python
"description": "将重要信息保存到知识图谱。当用户表达偏好、事实、经历、计划等值得记住的内容时调用。",
```

**改为：**
```python
"description": (
    "将重要信息保存到知识图谱。当用户表达偏好、事实、经历、计划等值得记住的内容时调用。"
    " relations 中 from 指向归属中心（from_type='User', from_name='AXIS' 表示用户内容，"
    " from_type='AI', from_name='Midnight' 表示 AI 规则，from_type='Project', from_name='AIChat' 表示项目内容），"
    "跨中心实体可多条 relations。"
),
```

---

## 第 9 步：修改 `web/AIChat/src/components/graph/GraphCanvas.vue`

### 9a: 新增配置获取

```typescript
// 在 <script setup> 顶部，现有 import 之后
interface GraphConfig {
  centers: Array<{
    type: string
    name: string
    icon: string
    label: string
    is_root: boolean
    render: {
      radius: number
      pulse: string       // "none" | "single" | "double"
      color: string
      truncate_name: boolean
    }
  }>
  entity_types: Record<string, {
    icon?: string
    label_prefix?: string
    render?: {
      radius?: number
      pulse?: string
      color?: string
      truncate_name?: boolean
    }
  }>
  default_render: {
    radius: number
    pulse: string
    color: string
    truncate_name: boolean
    max_name_len: number
  }
  root_node_ids: string[]
  injection: {
    header: string
    section_template: string
    unknown_section_icon: string
    unknown_section_label: string
    item_template: string
    relation_label: string
    source_label: string
    no_result: string
  }
}

let graphConfig = $ref<GraphConfig | null>(null)

async function fetchConfig() {
  try {
    const resp = await fetch('/api/graph/config')
    graphConfig = await resp.json()
  } catch {
    // 降级：使用硬编码默认值（与 yaml 默认值一致）
    graphConfig = {
      centers: [],
      entity_types: {},
      default_render: { radius: 18, pulse: 'none', color: '#7c7c90', truncate_name: true, max_name_len: 12 },
      root_node_ids: [],
      injection: { header: '', section_template: '', unknown_section_icon: '📌', unknown_section_label: '其他', item_template: '', relation_label: '', source_label: '', no_result: '' },
    }
  }
}
```

在 `onMounted` 中先调 `await fetchConfig()` 再调 `refresh()`。

### 9b: 改造 `nodeRadius()`（第 223–225 行）

**改前：**
```typescript
function nodeRadius(type: string): number {
  return type === 'User' ? 28 : 18
}
```

**改为：**
```typescript
function nodeRadius(type: string): number {
  // 先查 centers
  const c = graphConfig?.centers.find(c => c.type === type)
  if (c) return c.render.radius
  return graphConfig?.default_render.radius ?? 18
}
```

### 9c: 改造固定锚点（第 484 行）

**改前：**
```typescript
fixed: n.id === 'User|default',
```

**改为：**
```typescript
fixed: graphConfig?.root_node_ids?.includes(n.id) ?? false,
```

### 9d: 改造 SVG 模板中所有 `type === 'User'` 判断

共 5 处，全部改为读 `graphConfig`：

| 行 | 改前 | 改为 |
|---|---|---|
| 722 | `'node-user': node.type === 'User'` | `'node-user': isRootNode(node)` |
| 739 | `v-if="node.type !== 'User'"` | `v-if="!isRootNode(node)"` |
| 749 | `v-if="node.type === 'User'"` | `v-if="getPulseType(node) === 'double'"` |
| 756 | `v-if="node.type === 'User'"` | `v-if="getPulseType(node) === 'double'"` |
| 793 | `node.type === 'User' ? node.name : truncateName(node.name)` | `shouldTruncate(node.type) ? truncateName(node.name) : node.name` |

新增三个辅助函数：

```typescript
function isRootNode(node: { type: string, id: string }): boolean {
  return graphConfig?.root_node_ids?.includes(node.id) ?? false
}

function getPulseType(node: { type: string }): string {
  const c = graphConfig?.centers.find(c => c.type === node.type)
  return c?.render?.pulse ?? 'none'
}

function shouldTruncate(type: string): boolean {
  const c = graphConfig?.centers.find(c => c.type === type)
  if (c) return c.render.truncate_name ?? false
  return graphConfig?.default_render.truncate_name ?? true
}
```

### 9e: 力导向初始坐标

非根实体随机散布，根节点按 `centers` 数量均匀分布在圆上：

```typescript
// 在 refresh() 中，替换 nodes.value = data.nodes.map(...)
const rootIds = new Set(graphConfig?.root_node_ids ?? [])
const rootCount = rootIds.size
let rootIndex = 0
const radius = 120

nodes.value = data.nodes.map(n => {
  if (rootIds.has(n.id)) {
    // 根节点均匀分布在圆上
    const angle = (rootIndex / rootCount) * 2 * Math.PI
    rootIndex++
    return { ...n, x: radius * Math.cos(angle), y: radius * Math.sin(angle), vx: 0, vy: 0, fixed: true }
  }
  return { ...n, x: (Math.random() - 0.5) * 200, y: (Math.random() - 0.5) * 200, vx: 0, vy: 0, fixed: false }
})
```

---

## 第 10 步：数据库初始化与迁移

### 10a: 审计现有数据（先查再动）

```sql
-- 查 from 方向：User|default 指向了哪些实体
SELECT from_type, from_name, rel_type, to_type, to_name
FROM relations 
WHERE from_type='User' AND from_name='default';

-- 查 to 方向：哪些实体指向了 User|default（可能影响删除）
SELECT from_type, from_name, rel_type, to_type, to_name
FROM relations 
WHERE to_type='User' AND to_name='default';
```

> ⚠️ 如果 to 方向有边，删 `User|default` 前必须先删这些边，否则外键约束报错或产生孤立数据。

### 10b: 建三个根实体

```sql
INSERT OR IGNORE INTO entities (name, type, properties, importance, pinned)
VALUES ('AXIS', 'User', '{}', 10, 1);

INSERT OR IGNORE INTO entities (name, type, properties, importance, pinned)
VALUES ('Midnight', 'AI', '{}', 10, 1);

INSERT OR IGNORE INTO entities (name, type, properties, importance, pinned)
VALUES ('AIChat', 'Project', '{}', 10, 1);
```

### 10c: 创建根实体间关系

根据 yaml 中 `root_relations` 配置（也可在 `init_db()` 中自动创建）。

### 10d: 清理旧数据

```sql
-- 先删指向 User|default 的边（to 方向）
DELETE FROM relations WHERE to_type='User' AND to_name='default';

-- 再删 User|default 的边（from 方向）
DELETE FROM relations WHERE from_type='User' AND from_name='default';

-- 最后删实体
DELETE FROM entities WHERE name='default' AND type='User';
```

> ⚠️ 注意执行顺序：先删边，再删实体。如果使用外键 CASCADE，顺序可简化，但建议手动控制以免意外级联。

**如果不想立刻删旧数据**：可以保留 `User|default`，无归属的旧实体会在"📌 其他"分组显示，后续对话中 AI 自然把它们重新挂载到正确中心。`User|default` 本身因为不在 yaml 的 `centers` 列表中，不影响新逻辑。

---

## 执行顺序

| 步骤 | 内容 | 依赖 |
|---|---|---|
| 0 | **审计**：跑两条 SQL 查 `User\|default` 的 from/to 关联 | — |
| 1 | 确认 `pyyaml` 在 requirements.txt 中 | — |
| 2 | 新增 `graph_config.yaml`（含 `entity_types`） | — |
| 3 | 新增 `config_loader.py`（含 `render_prompt`、`build_center_list_md`） | 2 |
| 4 | 改 `graph.py`（`search_entities` + `get_pinned` + `get_all_graph` 共 5 处） | 3 |
| 5 | 改 `memory.py`（注入分组 + 统一提示词来源） | 3 |
| 6 | 改 `chat_system.txt`（用 `{{center_list}}`） | — |
| 7 | 改 `graph_update.txt`（用 `{{center_list}}`） | — |
| 8 | 改 `api/graph.py`（加 `/api/graph/config` + `render_prompt`） | 3 |
| 9 | 改 `tools/__init__.py`（`save_to_graph` 描述） | — |
| 10 | 改 `GraphCanvas.vue`（读配置 API + 3 个辅助函数） | 8 |
| 11 | 建三个根实体（SQL）+ 删 `User\|default`（先删边） | — |
| 12 | 重启测试 | 全部 |

---

## 与旧计划对比

| 旧方案 | 新方案 |
|---|---|
| 4 处 SQL 手动改写 | graph.py 读配置，批量 IN 查询 center_relations |
| User 硬编码替换为 User/AI/Project 白名单 | 从 yaml 动态读取，加 Novel 不改代码 |
| `type_defaults` + `default_render` 分两处 | 合并为 `entity_types`，单一来源 |
| memory.py 手动改分段逻辑 | 配置驱动模板 + `center_relations` 自动分组 |
| 提示词手动改写，两处各自维护 | 统一读 `graph_update.txt`，`render_prompt()` 注入 `{{center_list}}` |
| GraphCanvas 7 处逐一改 | 读 `/api/graph/config`，3 个辅助函数替代 |
| SQL 迁移只查 from 方向 | 审计 from + to 双方向，先删边再删实体 |
| `memory.py` 和 `api/graph.py` 各自维护提示词 | 统一读模板 + `render_prompt()` |
| `user_relation` 字段名模糊 | 明确定义 `center_relations: [{center_type, center_name, rel_type}]` |
| `save_to_graph` 描述简略 | 精确给出 from_type/from_name 示例 |
| 未处理 `get_all_graph` | 确认同样受影响并给出改动 |
