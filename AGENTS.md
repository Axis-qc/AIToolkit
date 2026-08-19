# AGENTS.md
## Important
用中文思维完成接下来的所有任务
## 启动
- 双击 `start.bat`（后端 18000 + 前端 15173 一起启动）

## 开发命令
- 类型检查：`cmd /c "npx vue-tsc --build --noEmit"`（workdir: `web\aitoolkit`）
- 构建检查：`cmd /c "npx vite build"`（workdir: `web\aitoolkit`）
- 后端测试：`python -c "import app"`（workdir: `backend`）
- 安装依赖：pip 用 `backend\.venv\Scripts\pip`，npm 用 `cmd /c npm`

## 项目结构
```
AIToolkit/
├── start.bat / start.py       # 单终端启动
├── AGENTS.md                  # Reasonix 项目记忆
├── BUGLOG.md                  # 已知坑记录
├── backend/
│   ├── .env                   # 配置
│   ├── app/
│   │   ├── main.py            # FastAPI 入口，CORS，lifespan
│   │   ├── mcp_server.py      # MCP SSE 端点，直调 core/memory
│   │   ├── api/graph.py       # 图谱 REST 端点，直调 core/memory
│   │   ├── core/
│   │   │   ├── db.py          # SQLite 连接 + init_db + 自动清理
│   │   │   ├── graph.py       # Facade（re-export graph_crud/search/view）
│   │   │   ├── graph_crud.py  # 实体/关系/事实 CRUD、软删除、合并、邻域 BFS
│   │   │   ├── graph_search.py # 搜索引擎（CJK 分词 + 权重计分）
│   │   │   ├── graph_view.py  # 前端可视化专用查询
│   │   │   ├── memory.py      # 业务编排（格式化 + 调 graph 子模块）
│   │   │   ├── config.py      # pydantic-settings 配置
│   │   │   ├── config_loader.py # graph_config.yaml 加载器
│   │   │   ├── graph_config.yaml # 图谱配置
│   │   │   └── logger.py      # 日志
│   │   └── models/
│   │       └── graph_tool.py  # 请求/响应 Pydantic 模型
│   └── requirements.txt
└── web/aitoolkit/
    ├── vite.config.ts          # proxy /api → 127.0.0.1:18000
    └── src/
        ├── api/graph.ts        # 图谱 API 客户端
        └── components/graph/   # 图谱可视化组件
```

## 架构约定
- 知识图谱记忆 = MCP 工具（function call），非外部 pipeline
- MCP 端点直调 `core/memory`，REST 端点直调 `core/memory`
- `core/graph.py` 是 facade，实际逻辑拆分在 `graph_crud/search/view/db` 四个模块
- 前端仅用于知识图谱可视化，无 LLM 聊天功能
- 所有地址用 `127.0.0.1`，不用 `localhost`（Windows IPv6 问题）
- 前端无 emoji，暗色主题，左侧栏悬停展开，`v-text` 不用 `v-html`
- 前端无 emoji，暗色主题，左侧栏悬停展开，`v-text` 不用 `v-html`

## 数据流原则
- **点到点直通**：MCP/REST 端点 → `core/memory` 编排 → `core/graph.crud|search|view` → `core/db` → SQLite
- 前端仅用于知识图谱可视化，直接消费 `/api/graph/*` REST 数据

## 工作约定
- **执行授权**：只有用户明确说出"开始实施"四个字才视为执行授权。任何其他表述（"开始"、"干吧"、"执行"、"直接修改"等）均归为讨论修改范畴，不可进行文件写入。
- **无授权时只读**：未收到"开始实施"授权时，保持沟通与研究状态，只读不写。
- **遇歧义先询问**：遇到多种可行方案或不明确的需求时，必须询问用户确认，禁止自行推测。
- **先计划后实施**：非简单修改必须先制定计划，等待用户回复"开始实施"后再动手。

## 已知坑
见 BUGLOG.md，重点：
- Windows subprocess 调 npm/npx 必须走 `cmd /c`
- Vite proxy target 写 `127.0.0.1` 不写 `localhost`
