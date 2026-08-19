# AIToolkit

AI 知识图谱工具集 —— 本地运行的监控与知识管理平台。

将知识图谱记忆系统通过 MCP（Model Context Protocol）暴露给 AI 客户端，同时提供手机负载监控、主题系统与 Vue3 可视化控制台。所有数据仅在本机处理，不依赖任何外部服务。

## 特性

- **知识图谱记忆系统**：SQLite 持久化的实体 / 关系 / 事实记忆，通过 MCP（SSE / Streamable HTTP）暴露给 Reasonix、Claude Desktop 等 AI 客户端，也提供完整 REST API。
- **手机负载监控**：通过 SSH 只读定期采样手机 `/proc` 数据（CPU / 内存 / 磁盘 / 进程 / 负载），**纯被动读取，绝不执行任何进程操作**。
- **主题系统**：前端主色一键切换并持久化到后端，全站配色由主色派生。
- **前端可视化控制台**：暗色主题、左侧栏悬停展开、无 emoji，内置总览 / 手机监控 / 知识图谱 / 网页工具 / 小游戏 / 系统设置等页面。

```
后端 (FastAPI + SQLite)  ──SSE──►  MCP 客户端 (Reasonix / Claude Desktop 等)
                            │
                            ├── /api/graph/*      知识图谱 REST 端点
                            ├── /api/phone/*      手机负载监控 REST 端点
                            ├── /api/theme/*      主题持久化 REST 端点
                            └── 前端 Vue3 可视化控制台
```

---

## 快速开始

### 1. 启动

双击 `start.bat`，或命令行：

```bash
python start.py
```

启动脚本会自动创建虚拟环境、安装依赖并读取 `backend/.env`（不存在则从 `.env.example` 复制）。后端启动在 `http://127.0.0.1:18000`，前端在 `http://127.0.0.1:15173`。

### 2. 验证

```bash
# 健康检查
curl http://127.0.0.1:18000/api/health

# 图谱数据
curl http://127.0.0.1:18000/api/graph/roots

# 手机负载（纯只读）
curl http://127.0.0.1:18000/api/phone/status

# 当前主题主色
curl http://127.0.0.1:18000/api/theme
```

### 3. 连接 MCP 客户端

在 AI 客户端（Reasonix、Claude Desktop 等）中注册 MCP 服务器：

| 配置项 | 值 |
|--------|-----|
| 名称 | `knowledge-graph` |
| 传输方式 | SSE |
| 地址 | `http://127.0.0.1:18000/mcp/sse` |

**Reasonix 中的注册命令：**

```bash
# 在 Reasonix 会话中执行
add_mcp_server \
  name=knowledge-graph \
  transport=sse \
  url=http://127.0.0.1:18000/mcp/sse
```

注册后重启 Reasonix 会话即可使用全部知识图谱工具（见下文"工具参考"）。也支持无状态直调：`POST /mcp/direct` 与 `POST /mcp-direct`。

### 4. 前端页面

打开 `http://127.0.0.1:15173`：

| 路由 | 页面 | 说明 |
|------|------|------|
| `/` | 总览 | 本地节点运行概览 |
| `/phone-monitor` | 手机运行态势 | 手机 CPU / 内存 / 磁盘 / 进程实时曲线 |
| `/graph` | 知识图谱 | 图谱可视化与浏览 |
| `/tools` | 网页工具 | 工具中心 |
| `/games` | 小游戏 | 休闲模块 |
| `/settings` | 系统设置 | 主题主色切换（持久化） |

---

## 功能模块

### 知识图谱记忆系统

AI 的长期记忆，由 AI 自行维护更新。支持：

- 实体（含关系声明）、事实的增删改查
- 软删除 + 24h 自动物理清理，期间可恢复
- CJK 分词 + 权重计分的语义搜索
- 固定注入（pinned）、根节点（is_root）、重要度排序
- 实体合并（去重）、邻域 BFS 查询

### 手机负载监控

通过 SSH 只读读取手机 `/proc` 数据：

- CPU 使用率（优先 `/proc/stat` 双采样差分，回退到 `ps` 占用和，标注数据来源）
- 内存 / 交换分区、磁盘占用、负载、运行时间、核心数
- 进程列表（CPU / 内存占用 Top）、历史曲线
- 连接失败自动重连，连不上时返回 `ok=false`，不影响任何进程

手机锁屏 / 后台时 Android 会限制读取全局 `/proc`（`stat` / `loadavg` / `net` 等），此时这些指标为空，内存 / 磁盘 / 进程表仍可用。

> SSH 凭据通过 `backend/.env` 的 `PHONE_*` 配置，见下文"配置"。

### 主题系统

前端提供 9 种预设主色 + 自定义 hex，切换后：

- 实时注入 CSS 变量（`--accent` / `--accent-bright` / `--accent-deep` / `--line` 等）派生全站配色
- 持久化到后端 `backend/data/theme.json`，重启后保留

---

## 项目结构

```
AIToolkit/
├── start.bat / start.py          # 一键启动（后端 18000 + 前端 15173）
├── AGENTS.md                     # 项目开发记忆
├── BUGLOG.md                     # 已知坑记录
├── backend/
│   ├── .env / .env.example       # 配置（.env 不入库）
│   ├── requirements.txt
│   └── app/
│       ├── main.py               # FastAPI 入口：CORS、lifespan、路由挂载、MCP 端点
│       ├── mcp_server.py         # MCP 工具定义（SSE / Streamable HTTP / direct）
│       ├── api/
│       │   ├── graph.py          # 图谱 REST 端点（直调 core/memory）
│       │   ├── phone_monitor.py  # 手机负载监控（SSH 只读轮询，凭据读 .env）
│       │   └── theme.py          # 主题主色持久化
│       ├── core/
│       │   ├── db.py             # SQLite 连接 + init_db + 自动清理
│       │   ├── graph.py          # Facade（re-export graph_crud/search/view）
│       │   ├── graph_crud.py     # 实体/关系/事实 CRUD、软删除、合并、邻域 BFS
│       │   ├── graph_search.py   # 搜索引擎（CJK 分词 + 权重计分）
│       │   ├── graph_view.py     # 前端可视化专用查询
│       │   ├── memory.py         # 业务编排（直调 graph 子模块）
│       │   ├── config.py         # pydantic-settings 配置（加载 .env）
│       │   └── logger.py         # 日志
│       └── models/
│           └── graph_tool.py     # 请求/响应 Pydantic 模型
└── web/aitoolkit/
    ├── vite.config.ts            # proxy /api → 127.0.0.1:18000
    └── src/
        ├── api/                  # graph.ts / phoneMonitor.ts（REST 客户端）
        ├── components/
        │   ├── AppShell.vue      # 左侧栏导航 + 顶栏布局
        │   └── graph/            # 图谱可视化组件
        ├── stores/theme.ts       # 主题状态（主色持久化）
        └── views/                # Home / PhoneMonitor / GraphPage / Tools / Games / Settings
```

---

## 数据表结构

知识图谱存储在三张 SQLite 表中：

### `entities` — 实体主表

| 列名 | 类型 | 说明 |
|------|------|------|
| `name` | TEXT PK | 实体名称（唯一标识） |
| `type` | TEXT NOT NULL | 实体类型，如 `User` / `AI` / `Project` |
| `content` | TEXT | 实体描述/备注，参与搜索 |
| `relations` | TEXT | JSON 数组，关系声明 `[{"name":"目标","rel":"关系类型"}]` |
| `properties` | TEXT | JSON 对象，自定义属性 |
| `importance` | INTEGER | 排序权重 1–10 |
| `pinned` | INTEGER | 是否固定注入（搜索时置顶） |
| `is_root` | INTEGER | 根节点标记 |
| `created_at` | TEXT | 创建时间 |
| `updated_at` | TEXT | 最后更新时间 |
| `deprecated_at` | TEXT | 软删除时间，NULL=有效 |

### `relation_index` — 关系索引（由 `entities.relations` 自动派生）

| 列名 | 类型 | 说明 |
|------|------|------|
| `entity_name` | TEXT | 关系发出者 |
| `target_name` | TEXT | 关系接收者 |
| `rel_type` | TEXT | 关系类型，如 "包含"、"了解"、"依赖" |
| `deprecated_at` | TEXT | NULL=有效，有值=目标实体已被软删 |

主键 `(entity_name, target_name, rel_type)`。索引 `idx_ri_target` / `idx_ri_dep`。

### `facts` — 独立事实（可关联多个实体）

| 列名 | 类型 | 说明 |
|------|------|------|
| `id` | INTEGER PK | 自增 ID |
| `content` | TEXT | 事实内容 |
| `type` | TEXT | 事实类型，默认 `"fact"` |
| `ts` | TEXT | 创建时间戳 |
| `about_entities` | TEXT | JSON 数组，关联的实体名称列表 |
| `deprecated_at` | TEXT | 软删除时间，NULL=有效 |

### 关系说明

- **关系由发起者维护**：每个实体在自己的 `relations` JSON 中声明指向谁、什么关系。删除实体时其出边自动作废。
- **自动索引**：写入实体时 `refresh_relation_index()` 自动将 `relations` JSON 同步到 `relation_index` 表，查询不走 JSON 解析。
- **软删除联动**：实体被软删时，出边直接删除，入边标记 `deprecated_at`；恢复时入边清标记、出边从 JSON 重建。
- **24h 自动清理**：`cleanup_expired()` 定时物理删除过期数据。

---

## MCP 工具参考

通过 MCP 暴露的 9 个知识图谱工具：

### 1. `get_entity`

精准匹配读取单个实体的完整字段。未找到返回 `null`。

| 参数 | 类型 | 说明 |
|------|------|------|
| `name` | string | 实体名称（精准匹配，必填） |

返回值：`dict`，包含 `name`、`type`、`content`、`relations`、`properties`、`importance`、`pinned`、`is_root`、`created_at`、`updated_at`、`deprecated_at`。

### 2. `search_memory`

搜索长期记忆。按自然语言查询匹配实体。

| 参数 | 类型 | 说明 |
|------|------|------|
| `query` | string | 搜索关键词（必填） |
| `top_k` | int | 返回结果数（默认 5） |

返回值：`list[dict]`，每个元素包含 `entity`、`type`、`importance`、`pinned`。

### 3. `save_to_graph`

保存实体（含关系声明）和事实到知识图谱。关系嵌入在 `GraphNode.relations` 中，无需单独传参。

| 参数 | 类型 | 说明 |
|------|------|------|
| `nodes` | GraphNode[] | 新增/更新的实体列表（可含 content/relations） |
| `facts` | GraphFact[] | 关联事实 |
| `importance` | int(1-10) | 重要度 |
| `pinned` | bool | 是否固定注入 |

### 4. `list_memory`

分层浏览知识图谱。无参数时返回类型概览；传入 type 列出该类型下所有实体。

| 参数 | 类型 | 说明 |
|------|------|------|
| `type` | string | （可选）实体类型，如 `User`/`AI`/`Project`。不传则返回类型概览 |

### 5. `delete_from_graph`

软删除实体或事实。标记为作废，24 小时后自动物理删除，期间可用 `restore_memory` 恢复。

| 参数 | 类型 | 说明 |
|------|------|------|
| `target_type` | string | `entity` / `fact` / `fact_by_content` |
| `target` | string | 目标标识 |

### 6. `update_memory`

**无需删除重建**，原地修改已有记忆。

| target_type | target | updates 可用字段 |
|-------------|--------|------------------|
| `fact` | 事实数字 ID | `content`, `type`, `about_entities` |
| `entity` | 实体名称 | `name`, `type`, `properties`, `content`, `relations` |
| `entity_importance` | 实体名称 | `importance`(1-10) |
| `entity_pinned` | 实体名称 | `pinned`(true/false) |

### 7. `merge_entities`

将源实体合并到目标实体：迁移所有关系、事实、属性，然后软删除源实体。

| 参数 | 类型 | 说明 |
|------|------|------|
| `source` | string | 源实体名称（合并后被删除） |
| `target` | string | 目标实体名称（接收所有数据） |

### 8. `restore_memory`

恢复已软删除的实体或事实。软删除后 24 小时内可恢复。

| 参数 | 类型 | 说明 |
|------|------|------|
| `target_type` | string | `entity`（按名称）/ `fact`（按 ID） |
| `target` | string | 实体名称或事实数字 ID |

### 9. `list_deprecated`

列出所有已软删除待清理的实体和事实（24 小时后自动物理删除）。无参数。

---

## REST API 端点

### 图谱查询（REST）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/graph` | 全量图谱（节点+边+事实） |
| GET | `/api/graph/roots` | 根实体列表（is_root=1） |
| GET | `/api/graph/children?type=&name=` | 子节点 |
| GET | `/api/graph/facts?type=&name=` | 实体关联事实 |
| GET | `/api/graph/orphans` | 无关联的孤岛实体 |

### 图谱工具调用（REST）

| 方法 | 路径 |
|------|------|
| POST | `/api/graph/tool/get_entity` |
| POST | `/api/graph/tool/search_memory` |
| POST | `/api/graph/tool/save_to_graph` |
| POST | `/api/graph/tool/list_memory` |
| POST | `/api/graph/tool/delete_from_graph` |
| POST | `/api/graph/tool/restore_memory` |
| POST | `/api/graph/tool/list_deprecated` |

### 手机监控（REST）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/phone/status` | 最新负载快照 + 历史曲线（纯只读，连不上返回 `ok=false`） |

### 主题（REST）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/theme` | 当前主题主色 `{"accent":"#d4af37"}` |
| PUT | `/api/theme` | 保存主色，body `{"accent":"#rgb|#rrggbb"}`，持久化到 `data/theme.json` |

### MCP 端点

| 路径 | 协议 | 说明 |
|------|------|------|
| `GET /mcp/sse` | SSE | 长连接入口 |
| `POST /mcp/messages/?session_id=xxx` | JSON-RPC | SSE 会话消息 |
| `POST /mcp/direct` | JSON-RPC | 无状态单次调用（无需 SSE 连接） |
| `/mcp/`（Streamable HTTP） | JSON-RPC | Codex 等客户端的自定义 MCP |
| `POST /mcp-direct` | JSON-RPC | 服务端直调封装（无 SSE 依赖） |

---

## 配置（backend/.env）

`.env` 不会提交到 Git，缺失时 `start.py` 会自动从 `.env.example` 复制。主要配置项：

| 变量 | 说明 |
|------|------|
| `CHAT_PROVIDER` | provider：`deepseek` / `openai` / `anthropic` |
| `DEEPSEEK_*` / `OPENAI_*` / `ANTHROPIC_*` | 各 provider 的 API Key / Base URL / 模型名 |
| `PHONE_HOST` | 手机 SSH 地址（默认空，未配置则手机监控无数据） |
| `PHONE_PORT` | SSH 端口（默认 8022） |
| `PHONE_USER` | SSH 用户名 |
| `PHONE_PASS` | SSH 密码 |
| `PHONE_POLL_INTERVAL` | 采样间隔秒数（默认 3.0） |
| `PREHEAT_*` | 预热白名单（空格分隔，格式 `path[:ext1,ext2,...]`） |
| `FILE_TOOL_WHITELIST` | 文件操作工具白名单（逗号分隔） |

---

## 数据流

```
AI 客户端 / 前端           后端                            SQLite
    │                      │                               │
    ├── save_to_graph ────►│ api/graph.py                  │
    │                      │──► memory.save ──────────────►│
    │                      │                               │
    ├── search_memory ────►│──► memory.search              │
    │                      │    └► graph_search ──────────►│
    │                      │◄─── 原始结果 ◄───────────────│
    │                      │                               │
    ├── list_memory ──────►│──► memory.list_memory ────────►│
    │                      │                               │
    └── delete_from_graph ►│──► memory.delete_memory ─────►│
```

- **MCP 路径**：`mcp_server.py` → `core/memory` → `core/graph.crud|search|view` → `core/db` → SQLite
- **REST 路径**：`api/graph.py` → `core/memory` → 同上
- **前端可视化**：`api/graph.py` → `core/graph_view` → `core/db` → SQLite
- **手机监控**：`phone_monitor.py` 后台线程 → SSH 只读 `/proc` → `_state` 内存快照 → `/api/phone/status`
- **主题**：`theme.ts` → `/api/theme` → `data/theme.json`

---

## 注意事项

- 所有地址用 `127.0.0.1`，不用 `localhost`（Windows IPv6 问题）
- 详情见 `BUGLOG.md`
