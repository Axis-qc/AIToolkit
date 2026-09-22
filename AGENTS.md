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
        └── components/graph/   # 图谱可视化（力导向关系网，Canvas 单层渲染）
            ├── GraphView.vue        # 编排：视口状态、交互、面板开关
            ├── GraphDetailPanel.vue # 实体详情（双击节点打开）
            ├── GraphFilterPanel.vue # 类型与重要度筛选
            ├── GraphSearchBox.vue   # 搜索定位（Ctrl+F）
            ├── graphTheme.ts        # 类型配色、图例汇总、层级关系判定
            └── layout/              # 渲染引擎，零第三方依赖
                ├── forceSim.ts      # 力导向内核（斥力/弹簧/向心 + 退火）
                ├── quadtree.ts      # Barnes-Hut 四叉树，斥力 O(n log n)
                ├── labels.ts        # 标签分级与矩形避让
                └── renderer.ts      # Canvas 绘制与视口裁剪
```

## 图谱可视化约定
- 交互分层：悬停只改光标形状，不做任何高亮或淡化；单击用于拖拽；双击才进入重点显示（高亮一跳邻域与关联边、打开详情面板）
- 布局参数经真实数据（622 节点 / 898 边）实测选定，调参须复验：节点视觉重叠为 0、各筛选阈值下弹簧力方向正常、单帧耗时远低于 16.7ms
- 弹簧力的距离上限只与理想边长挂钩，绝不与节点数挂钩，否则筛选到少量节点时弹簧力会整体反转成推远（详见 BUGLOG.md 第 10 条）
- 力导向图按度数衰减弹簧强度、按度数加权向心力，孤立节点自然落在外围，不做单独处理
- `/api/graph` 只返回 nodes 与 edges；事实详情由 `/api/graph/facts` 按实体查询
- 筛选后重建布局沿用已有坐标，避免整张图跳变

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

## 已知坑
见 BUGLOG.md，重点：
- Windows subprocess 调 npm/npx 必须走 `cmd /c`
- Vite proxy target 写 `127.0.0.1` 不写 `localhost`
