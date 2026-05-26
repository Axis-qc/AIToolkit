# AIToolkit

AI 知识图谱工具集 — 将知识图谱记忆系统通过 MCP（Model Context Protocol）暴露给 AI 客户端。

```
后端 (FastAPI + SQLite)  ──SSE──►  MCP 客户端 (Reasonix / Claude Desktop 等)
                            │
                            ├── /api/graph/* REST 端点
                            └── 前端 Vue3 可视化页面
```

---

## 快速开始

### 1. 启动

双击 `start.bat`，或命令行：

```bash
python start.py
```

后端启动在 `http://127.0.0.1:18000`，前端在 `http://127.0.0.1:15173`。

### 2. 验证

```bash
# 健康检查
curl http://127.0.0.1:18000/api/health

# 图谱数据
curl http://127.0.0.1:18000/api/graph/roots
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

注册后重启 Reasonix 会话即可使用全部知识图谱工具（见下文"工具参考"）。

---

## 项目结构

```
AIToolkit/
├── start.bat / start.py          # 一键启动
├── AGENTS.md                     # Reasonix 项目记忆
├── BUGLOG.md                     # 已知坑记录
├── backend/
│   ├── requirements.txt
│   └── app/
│       ├── main.py               # FastAPI 入口 + MCP SSE 挂载
│       ├── mcp_server.py         # MCP 工具定义（FastMCP）
│       ├── api/
│       │   └── graph.py          # 图谱 REST 端点（直调 core/memory）
│       ├── core/
│       │   ├── db.py             # SQLite 连接 + init_db + 自动清理
│       │   ├── graph.py          # Facade（re-export 下面 3 个模块）
│       │   ├── graph_crud.py     # 实体/关系/事实 CRUD、软删除、合并、邻域 BFS
│       │   ├── graph_search.py   # 搜索引擎（CJK 分词 + 权重计分）
│       │   ├── graph_view.py     # 前端可视化专用查询
│       │   ├── memory.py         # 业务编排（格式化 + 调 graph 子模块）
│       │   ├── config.py         # pydantic-settings 配置
│       │   ├── config_loader.py  # graph_config.yaml 加载器
│       │   ├── graph_config.yaml # 图谱根实体/类型/渲染配置
│       │   └── logger.py         # 日志
│       └── models/
│           └── graph_tool.py     # 请求/响应 Pydantic 模型
└── web/aitoolkit/
    └── src/
        ├── api/graph.ts          # 图谱 API 客户端
        └── components/graph/     # 图谱可视化组件
```

---

## 配置指南

### 图谱配置（`graph_config.yaml`）

定义知识图谱的根实体（中心）、类型体系、渲染规则：

```yaml
centers:
  - type: User
    name: AXIS
    is_root: true
    categories:
      - name: 偏好习惯
      - name: 计划目标
      - name: 事实经历

entity_types:
  User:    { icon: "👤", label_prefix: "关于你" }
  Project: { icon: "📁", label_prefix: "项目" }
  AI:      { icon: "🤖", label_prefix: "关于我" }
```

每次启动时 `init_db()` 自动创建/同步根实体和分类。

---

## 工具参考

通过 MCP 暴露的 7 个知识图谱工具：

### 1. `search_memory`

搜索长期记忆。按自然语言查询匹配实体和事实。

| 参数 | 类型 | 说明 |
|------|------|------|
| `query` | string | 搜索关键词（必填） |
| `top_k` | int | 返回结果数（默认 5） |

### 2. `save_to_graph`

保存实体、关系、事实到知识图谱。`INSERT OR REPLACE` 自动处理更新。

| 参数 | 类型 | 说明 |
|------|------|------|
| `nodes` | GraphNode[] | 新增/更新的实体列表 |
| `relations` | GraphRelation[] | 实体间关系 |
| `facts` | GraphFact[] | 关联事实 |
| `importance` | int(1-10) | 重要度 |
| `pinned` | bool | 是否固定注入 |

### 3. `list_memory`

浏览图谱。两种模式：轻量索引和关联子图展开。

| 参数 | 类型 | 说明 |
|------|------|------|
| `mode` | string | `keywords`（列出所有实体名称+类型）/ `neighborhood`（查看指定实体的 N 层关联子图） |
| `entity_name` | string | `neighborhood` 模式必填，指定实体名称 |
| `depth` | int | `neighborhood` 模式展开层数（默认 2，最大 3） |

### 4. `delete_from_graph`

删除实体、事实或关系。按实体名称或事实 ID 定位。

| 参数 | 类型 | 说明 |
|------|------|------|
| `target_type` | string | `entity` / `fact` / `fact_by_content` / `relation` |
| `target` | string | 目标标识 |
| `rel_type` | string | （可选）精确匹配关系类型 |

### 5. `update_memory`

**无需删除重建**，原地修改已有记忆。

| target_type | target | updates 可用字段 |
|-------------|--------|------------------|
| `fact` | 事实数字 ID | `content`, `type`, `about_entities` |
| `entity` | 实体名称 | `type`, `properties` |
| `entity_importance` | 实体名称 | `importance`(1-10) |
| `entity_pinned` | 实体名称 | `pinned`(true/false) |

### 6. `restore_memory`

恢复已软删除的实体或事实。软删除后 24 小时内可恢复。

| 参数 | 类型 | 说明 |
|------|------|------|
| `target_type` | string | `entity`（按名称）/ `fact`（按 ID） |
| `target` | string | 实体名称或事实数字 ID |

### 7. `list_deprecated`

列出所有已软删除待清理的实体和事实（24 小时后自动物理删除）。无参数。

---

## API 端点

### 图谱查询（REST）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/graph` | 全量图谱（节点+边+事实） |
| GET | `/api/graph/roots` | 根实体列表 |
| GET | `/api/graph/children?type=&name=` | 子节点 |
| GET | `/api/graph/facts?type=&name=` | 实体关联事实 |
| GET | `/api/graph/orphans` | 无关联的孤岛实体 |
| GET | `/api/graph/config` | 图谱配置 |
| POST | `/api/graph/search` | 关键词搜索记忆 |

### 工具调用（REST）

| 方法 | 路径 |
|------|------|
| POST | `/api/graph/tool/search_memory` |
| POST | `/api/graph/tool/save_to_graph` |
| POST | `/api/graph/tool/list_memory` |
| POST | `/api/graph/tool/delete_from_graph` |
| POST | `/api/graph/tool/restore_memory` |
| POST | `/api/graph/tool/list_deprecated` |

### MCP 端点

| 路径 | 协议 | 说明 |
|------|------|------|
| `GET /mcp/sse` | SSE | 长连接入口 |
| `POST /mcp/messages/?session_id=xxx` | JSON-RPC | SSE 会话消息 |
| `POST /mcp/direct` | JSON-RPC | 无状态单次调用（无需 SSE 连接） |

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
    │                      │◄─── 格式化结果 ◄─────────────│
    │                      │                               │
    ├── list_memory ──────►│──► memory.list_memory         │
    │                      │    ├► graph_crud (keywords)   │
    │                      │    └► graph_crud (neighborhood)│
    │                      │                               │
    └── delete_from_graph ►│──► memory.delete_memory ─────►│
```

- **MCP 路径**：`mcp_server.py` → `core/memory` → `core/graph.crud|search|view` → `core/db` → SQLite
- **REST 路径**：`api/graph.py` → `core/memory` → 同上
- **前端可视化**：`api/graph.py` → `core/graph_view` → `core/db` → SQLite

---

## 注意事项

- 所有地址用 `127.0.0.1`，不用 `localhost`（Windows IPv6 问题）
- 详情见 `BUGLOG.md`
