# 后端设计 — AI 知识图谱记忆系统

## 一、核心思路

**知识图谱 = 以用户为中心向外辐射的记忆网络。AI 自主检索，AI 自主更新。**

每轮对话强制检索记忆。上下文过长或用户空闲时，同一 AI 审视对话 → 写入图谱 → 截断上下文。

---

## 二、目录结构

按功能分文件，每个功能独立维护，同时避免无意义的中间层。

```
backend/
├── app/
│   ├── main.py                  # FastAPI 入口：CORS、路由注册、启动事件
│   │
│   ├── api/                     # HTTP 接口（按功能分文件）
│   │   ├── __init__.py
│   │   ├── chat.py              # POST /api/chat（SSE 流式对话 + function call 循环）
│   │   └── graph.py             # POST /api/chat/update-graph（图谱更新）
│   │   // 未来扩展: analytics.py / auth.py / files.py
│   │
│   ├── tools/                   # AI Function Call 工具（每工具一个文件）
│   │   ├── __init__.py          # TOOL_DEFINITIONS 列表 + dispatch() 分发
│   │   ├── search_memory.py     # search_memory
│   │   └── save_to_graph.py     # save_to_graph
│   │   // 未来扩展: read_file.py / exec_code.py / web_search.py
│   │
│   ├── core/                    # 基础设施（不按功能分，全局共用）
│   │   ├── __init__.py
│   │   ├── config.py            # .env 配置 + Settings 单例
│   │   ├── neo4j.py             # Neo4j Async Driver + 连接池 + 向量索引
│   │   └── memory.py            # 图谱 CRUD + 向量检索 + Embedding（核心）
│   │
│   └── models/                  # Pydantic 类型（按功能分文件）
│       ├── __init__.py
│       ├── chat.py              # ChatRequest / SSEChunk / ToolCall
│       └── graph.py             # Entity / Fact / Relation / GraphNode
│       // 未来扩展: analytics.py / auth.py / user.py
│
├── requirements.txt
├── .env
├── .env.example
└── Dockerfile
```

### 分层依赖规则（3 层）

```
api/ ──→ tools/ ──→ core/
  │        │         │
  └────────┴─────→ models/
```

| 层 | 职责 | 依赖 |
|----|------|------|
| `api/` | HTTP 请求/响应、SSE 流、对话编排 | `tools/` `core/` `models/` |
| `tools/` | Function Call 工具定义 + 执行 | `core/` `models/` |
| `core/` | Neo4j、Embedding、图谱操作、配置 | `models/` |
| `models/` | Pydantic 类型定义 | 无 |

### 各文件职责

| 文件 | 职责 |
|------|------|
| `main.py` | 创建 FastAPI app、CORS、注册路由 |
| `api/chat.py` | `/api/chat` — SSE 流式对话，构建 messages + 调 OpenAI + function call 循环 |
| `api/graph.py` | `/api/chat/update-graph` — 图谱更新，调 AI 审视对话 → 写入图谱 |
| `tools/__init__.py` | `TOOL_DEFINITIONS` 列表 + `dispatch(name, args)` 分发函数 |
| `tools/search_memory.py` | query → `core/memory.search()` → 文本化子图 |
| `tools/save_to_graph.py` | nodes/relations/facts → `core/memory.save()` → MERGE 写入 |
| `core/config.py` | 读 `.env`，导出 `Settings` 单例 |
| `core/neo4j.py` | Async Driver + 连接管理 + 向量索引初始化 |
| `core/memory.py` | 图谱 CRUD、向量检索、子图遍历、Embedding 调用 |
| `models/chat.py` | `ChatRequest` / `SSEChunk` / `ToolCall` 等 |
| `models/graph.py` | `Entity` / `Fact` / `Relation` 等 |

### 调用链

```
用户发消息
  → api/chat.py
    → 构建 messages + System Prompt
    → 调 OpenAI（tools: search_memory, save_to_graph）
      → OpenAI 返回 tool_call: search_memory
        → tools/__init__.dispatch("search_memory", args)
          → tools/search_memory.py
            → core/memory.py.search() 向量检索 Neo4j
              → core/neo4j.py 执行 Cypher
          ← 返回子图文本
        ← 工具结果追加到 messages
      → 再次调 OpenAI，得到文本
    ← SSE 流式输出
  ← 前端渲染
```

---

## 三、配置管理

所有敏感信息通过 `.env` 文件注入，不写死在代码中。

### 3.1 .env 文件

```
# backend/.env

# DeepSeek (对话模型，兼容 OpenAI SDK)
DEEPSEEK_API_KEY=sk-your-deepseek-key
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_CHAT_MODEL=deepseek-v4-pro

# Embedding 模型 (DeepSeek 不支持，单独配置)
EMBED_API_KEY=sk-your-openai-key
EMBED_BASE_URL=https://api.openai.com/v1
EMBED_MODEL=text-embedding-3-small

# Neo4j
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=your_password
```

### 3.2 core/config.py

```python
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    # DeepSeek 对话模型
    deepseek_api_key: str
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_chat_model: str = "deepseek-v4-pro"

    # Embedding 模型（独立配置）
    embed_api_key: str = ""
    embed_base_url: str = "https://api.openai.com/v1"
    embed_model: str = "text-embedding-3-small"

    @property
    def chat_api_key(self) -> str:
        return self.deepseek_api_key

    @property
    def embed_api_key_effective(self) -> str:
        return self.embed_api_key or self.deepseek_api_key

    # Neo4j
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

settings = Settings()
```

### 3.3 使用方式

所有模块统一从 `core.config` 导入 `settings`：

```python
# core/memory.py
from openai import AsyncOpenAI
from core.neo4j import driver
from .config import settings

client = AsyncOpenAI(
    api_key=settings.openai_api_key,
    base_url=settings.openai_base_url,
)
```

### 3.4 安全规则

- `.env` 加入 `.gitignore`，不提交到版本库
- 提供 `backend/.env.example` 作为模板（不含真实密钥）
- 代码中永远不硬编码密钥字符串

---

## 四、图谱 Schema

### 4.1 核心原则

- 以 `User` 节点为根，所有知识围绕用户展开
- 节点类型和关系类型由 AI 自行定义，后端不做枚举约束
- 所有节点带时间戳和 embedding 向量

### 4.2 节点

```
(:User {id, name, created_at})

(:Entity {name, type, properties, embedding, ts})
    type: AI 自行命名（person / technology / concept / event / preference / todo ...）
    properties: JSON，存放额外属性

(:Fact {content, type, embedding, ts, source})
    content: 事实陈述文本
    type: AI 自行定义
    source: 来源对话 ID（可选）
```

### 4.3 关系

```
(:User)-[:HAS_RELATION {type, properties, ts}]->(:Entity)
    type: AI 自行命名（学过 / 偏好 / 计划 / 经历 / ...）

(:Entity)-[:RELATED {type, properties, ts}]->(:Entity)
    type: AI 自行命名（包含 / 依赖 / 关联 / ...）

(:Fact)-[:ABOUT {ts}]->(:Entity)
    事实关于哪些实体
```

### 4.4 示例

```cypher
// 以用户为中心
(u:User {id: "u1"})

// 实体
(rust:Entity {name: "Rust", type: "technology"})
(owner:Entity {name: "所有权", type: "concept"})
(func:Entity {name: "函数式编程", type: "paradigm"})

// 用户 → 实体
(u)-[:HAS_RELATION {type: "学过", ts: ...}]->(rust)
(u)-[:HAS_RELATION {type: "偏好", ts: ...}]->(func)

// 实体间关系
(rust)-[:RELATED {type: "包含概念", ts: ...}]->(owner)

// 事实
(f:Fact {content: "用户学习了 Rust 所有权", type: "学习记录"})
(f)-[:ABOUT]->(rust)
(f)-[:ABOUT]->(owner)
```

---

## 五、工具定义

两个工具以 Function Call 形式暴露给 AI。

### 5.1 search_memory

```
描述: 在知识图谱中检索与用户话题相关的记忆。每轮对话必须调用。
参数:
  - query (string, required): 自然语言描述要检索的内容
  - scope (string, optional): "recent" | "all" | "{N}d" 如 "7d"
  - top_k (int, optional, default=5): 返回结果数量
返回: 文本化的子图描述
```

**后端实现流程：**
1. `query` → OpenAI Embedding → 向量
2. Neo4j 向量索引匹配相似 Entity 节点
3. 以匹配实体为锚点 → 1~2 hop 遍历
4. 收集关联的 Entity / Fact / HAS_RELATION / RELATED
5. 组装为结构化文本返回

### 5.2 save_to_graph

```
描述: 将新知识写入知识图谱。同名实体自动合并（MERGE）。
用于图谱更新阶段。
参数:
  - nodes (array, required): [{name, type, properties}]
  - relations (array, required): [
      {from_type, from_name, to_type, to_name, rel_type, properties}
    ]
  - facts (array, optional): [{content, type, about_entities}]
返回: 写入结果摘要
```

**后端实现流程：**
1. 遍历 nodes → MERGE Entity（同名自动合并，无需单独的 update 工具）
2. 遍历 relations → MERGE 关系
3. 遍历 facts → CREATE Fact → 建立 ABOUT 关系
4. 为新增/更新节点生成 embedding

---

## 六、API 设计

两个接口，分别放在 `api/chat.py` 和 `api/graph.py`。

### 6.1 对话接口

```
POST /api/chat
Content-Type: application/json
Response: text/event-stream (SSE)

请求:
{
  "conversation_id": "conv_xxx" | null,
  "messages": [
    {"role": "user", "content": "..."},
    {"role": "assistant", "content": "...", "tool_calls": [...]},
    {"role": "tool", "tool_call_id": "...", "content": "..."}
  ]
}
```

**处理流程：**

```
                    接收 messages[]
                         │
                         ▼
              构建完整 messages + System Prompt
              调 OpenAI（带上 search_memory / save_to_graph 工具定义）
                         │
                         ▼
              OpenAI 返回: 文本 或 tool_call
                         │
              ┌──────────┴──────────┐
              ▼                     ▼
        有 tool_call           纯文本回复
              │                     │
              ▼                     ▼
     dispatch 到对应工具      SSE 流式输出 token
    (search_memory /              │
     save_to_graph)               ▼
              │               返回 done
              ▼
    工具结果追加到 messages
              │
              ▼
    再次调 OpenAI（循环）
    直到不再有 tool_call
```

**SSE 事件格式：**

```
data: {"type": "token", "content": "根据"}

data: {"type": "tool_call", "name": "search_memory", "arguments": {"query": "Rust"}}

data: {"type": "tool_result", "tool_call_id": "call_xxx", "content": "用户学过Rust..."}

data: {"type": "token", "content": "记忆"}

data: {"type": "done", "conversation_id": "conv_xxx"}
```

### 6.2 图谱更新接口

前端检测上下文过长或用户空闲时调用。

```
POST /api/chat/update-graph
Content-Type: application/json

请求:
{
  "conversation_id": "conv_xxx",
  "messages": [...]          // 完整对话历史
}

处理:
  1. 构建 System Prompt：要求 AI 审视对话，提取长期记忆
  2. AI 调用 save_to_graph 工具写入图谱
  3. AI 生成对话摘要

响应:
{
  "success": true,
  "summary": "本次对话讨论了Rust trait..."  // AI 生成的摘要
}
```

---

## 七、System Prompt 设计

### 7.1 对话 System Prompt

```
你是一个具备长期记忆的 AI 助手。你的记忆以知识图谱形式存储，
以用户为中心展开。

工具使用规则：
1. 每收到用户消息，必须先调用 search_memory 检索相关记忆。
   即使你认为不需要，也必须调用。
2. 基于检索到的记忆和当前对话，给出个性化回复。
   如果记忆与当前话题无关，如实告知并正常回复。
3. 图谱中所有信息以用户为中心。关系类型自行用简洁动词命名。
4. 发现值得长期记忆的内容时，调用 save_to_graph 写入图谱。
```

### 7.2 图谱更新 System Prompt

```
现在需要你将本次完整对话中值得长期记忆的内容写入知识图谱。

请做以下事情：
1. 审视整个对话，提取：
   - 用户的新偏好、习惯、风格
   - 用户提到的新事实、经历、计划
   - 用户学习的知识点及其关系
   - 用户交代的待办事项
2. 调用 save_to_graph 工具，写入图谱
3. 最后用一句话概括本次对话的核心内容

注意：
- 只保存用户明确表达的内容，不要推测
- 不为琐碎的闲聊建立记忆
- 关系命名用简洁的动词，如"学过""偏好""计划""经历"等
```

---

## 八、图谱检索实现

### 8.1 检索流程

```
query "用户学 Rust 的进度"
    │
    ▼
Embedding → [0.12, -0.34, ...] (1536 维)
    │
    ▼
Neo4j 向量索引查询
  CALL db.index.vector.queryNodes('entity_embedding', 5, $vec)
  YIELD node, score WHERE score > 0.65
    │
    ▼
匹配到: Entity("Rust", score=0.92), Entity("所有权", score=0.78), ...
    │
    ▼
以匹配节点为锚点，1-hop 遍历:
  - User-[HAS_RELATION]->Entity  (用户与该实体的关系)
  - Entity-[RELATED]->Entity     (关联实体)
  - Fact-[ABOUT]->Entity         (相关事实)
    │
    ▼
组装文本:
  "## 关于 Rust
   - 你学过 Rust [偏好]
   - 你学了所有权、生命周期、Trait [相关概念]
   - 你计划用 Rust 重构项目 [最近事件]
   - 你对 borrow checker 感到困惑 [3天前]"
```

### 8.2 索引

```cypher
// 向量索引（Neo4j 5.x 原生支持）
CREATE VECTOR INDEX entity_embedding IF NOT EXISTS
FOR (e:Entity) ON (e.embedding)
OPTIONS {
  indexConfig: {
    `vector.dimensions`: 1536,
    `vector.similarity_function`: 'cosine'
  }
}

// 全文索引
CREATE FULLTEXT INDEX entity_name IF NOT EXISTS
FOR (e:Entity) ON EACH [e.name]
```

---

## 九、技术栈

| 项 | 选择 |
|----|------|
| 框架 | FastAPI (Python 3.11+) |
| 图谱 | Neo4j Community Edition |
| 对话模型 | DeepSeek (deepseek-v4-pro)，兼容 OpenAI SDK |
| 向量模型 | OpenAI text-embedding-3-small（可替换） |
| LLM SDK | openai Python SDK |
| Neo4j 驱动 | neo4j (async) |
| 部署 | Docker Compose |

---

## 十、依赖

```
# requirements.txt
fastapi
uvicorn[standard]
openai
neo4j
pydantic
pydantic-settings
```
