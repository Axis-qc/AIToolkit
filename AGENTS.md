# AGENTS.md
## Important
用中文思维完成接下来的所有任务
## 启动
- 双击 `start.bat`（后端 18000 + 前端 15173 一起启动）

## 开发命令
- 类型检查：`cmd /c "npx vue-tsc --build --noEmit"`（workdir: `web\AIChat`）
- 构建检查：`cmd /c "npx vite build"`（workdir: `web\AIChat`）
- 后端测试：`python -c "import app"`（workdir: `backend`）
- 安装依赖：pip 用 `backend\.venv\Scripts\pip`，npm 用 `cmd /c npm`

## 项目结构
```
AIChat/
├── start.bat / start.py       # 单终端启动
├── BUGLOG.md                  # 已知坑记录
├── DESIGN-FRONTEND.md         # 前端架构设计
├── DESIGN-BACKEND.md          # 后端架构设计
├── backend/
│   ├── .env                   # API key 配置
│   ├── app/
│   │   ├── main.py            # FastAPI 入口，CORS，lifespan
│   │   ├── api/chat.py        # POST /api/chat SSE 流 + function call 循环
│   │   ├── api/graph.py       # 图谱更新接口
│   │   ├── tools/             # AI 工具定义 + dispatch
│   │   ├── core/config.py     # pydantic-settings 从 .env 加载
│   │   ├── core/graph.py       # SQLite 图存储 + 向量搜索
│   │   ├── core/storage.py     # 对话文件存档 (JSON)
│   │   ├── core/memory.py     # 图谱 CRUD + embedding + 向量搜索
│   │   └── models/            # Pydantic 请求/响应模型
│   └── requirements.txt
└── web/AIChat/
    ├── vite.config.ts          # proxy /api → 127.0.0.1:18000
    └── src/
        ├── api/chat.ts         # SSE 客户端 (POST /api/chat, ReadableStream)
        ├── stores/chat.ts      # 模块级 reactive 状态 (messages/streaming/error)
        ├── views/ChatView.vue  # 聊天页：左侧栏(悬停展开) + 对话面板 + 主区域
        ├── components/chat/ChatWindow.vue  # 消息列表 + 思考过程折叠 + 工具调用
        └── components/chat/ChatInput.vue   # 输入框 + 停止按钮
```

## 架构约定
- 知识图谱记忆 = AI 工具（function call），非外部 pipeline
- `search_memory` + `save_to_graph` 两个工具，`save_to_graph` 用 INSERT OR REPLACE 自动处理更新
- 3 层：api → tools → core（无 services 层）
- 前端用模块级 reactive 做状态管理（无 Pinia）
- SSE 事件类型：`token` | `reasoning` | `tool_call` | `tool_result` | `done` | `error`
- DeepSeek base_url 写到域名即可，不加 `/v1`（SDK 自动追加）
- 所有地址用 `127.0.0.1`，不用 `localhost`（Windows IPv6 问题）
- 前端无 emoji，暗色主题，左侧栏悬停展开，`v-text` 不用 `v-html`

## 数据流原则
- **点到点直通**：后端产出终态数据 → 中间层仅转发（不改形、不加工、不插入等待/缓冲操作） → 前端直接消费
- SSE 客户端 (`api/chat.ts`) 只做网络读取 + 行分割 + JSON 解析，yield 原始 event，不插入 setTimeout / nextTick
- Store 从 `reactive` 数组取引用（非 raw 对象），直接修改触发响应式更新，不另建中间状态
- 渲染层直接绑定 store 数据，不做额外 transform

## 工作约定
- **执行授权**：只有用户明确说出"开始实施"四个字才视为执行授权。任何其他表述（"开始"、"干吧"、"执行"、"直接修改"等）均归为讨论修改范畴，不可进行文件写入。
- **无授权时只读**：未收到"开始实施"授权时，保持沟通与研究状态，只读不写。
- **语言与结尾**：使用中文思考和回复，回复末尾加"喵"。
- **遇歧义先询问**：遇到多种可行方案或不明确的需求时，必须询问用户确认，禁止自行推测。
- **先计划后实施**：非简单修改必须先制定计划，等待用户回复"开始实施"后再动手。

## 已知坑
见 BUGLOG.md，重点：
- DeepSeek reasoning_content 必须透传
- Windows subprocess 调 npm/npx 必须走 `cmd /c`
- Vite proxy target 写 `127.0.0.1` 不写 `localhost`
