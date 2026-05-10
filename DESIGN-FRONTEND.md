# 前端设计 — AI 知识图谱记忆系统

## 一、路由设计

```
路由表
─────────────────────────────────────────────
  /               主页（功能卡片入口）
  /chat           知识图谱 AI 对话（独立页面）
  /tools          网页工具（预留，独立页面）
  /games          小游戏（预留，独立页面）
```

每个路由对应一个独立完整页面，不共享布局组件。无侧边栏，无全局 Header。

---

## 二、各页面详设

### 2.1 主页（HomeView）

```
┌──────────────────────────────────────────────────┐
│                                                  │
│                                                  │
│               AI 工作台                           │
│                                                  │
│      ┌───────────┐   ┌───────────┐              │
│      │           │   │           │              │
│      │  对话     │   │  工具     │              │
│      │ AI知识图谱 │   │  待开发   │              │
│      │  记忆对话  │   │           │              │
│      │           │   │           │              │
│      └───────────┘   └───────────┘              │
│                                                  │
│      ┌───────────┐   ┌───────────┐              │
│      │           │   │           │              │
│      │  游戏     │   │  设置     │              │
│      │  待开发   │   │  待开发   │              │
│      │           │   │           │              │
│      └───────────┘   └───────────┘              │
│                                                  │
└──────────────────────────────────────────────────┘
```

- 全屏居中展示功能卡片网格
- 点击卡片 `router.push('/chat')` 跳转
- 后续新功能添加新卡片即可

### 2.2 对话页（ChatView）

全屏独立页面，顶部一个返回主页的按钮，其余为对话区。

```
┌──────────────────────────────────────────────────┐
│  ← 返回                          知识图谱 AI 对话 │
├──────────────────────────────────────────────────┤
│                                                  │
│                                                  │
│              ChatWindow（消息列表）                │
│                                                  │
│  ┌ Assistant ───────────────────────────────┐   │
│  │ 根据记忆，你之前学过 Rust 的基础...         │   │
│  └───────────────────────────────────────────┘   │
│  ┌ User ────────────────────────────────────┐   │
│  │ trait 具体怎么用？                         │   │
│  └───────────────────────────────────────────┘   │
│                                                  │
├──────────────────────────────────────────────────┤
│  ChatInput：[输入框___________________] [发送]    │
└──────────────────────────────────────────────────┘
```

---

## 三、目录结构

```
src/
├── main.ts
├── App.vue                         # <RouterView> 根节点
├── router/
│   └── index.ts                    # 路由定义
├── views/
│   ├── HomeView.vue                # 主页（功能卡片入口）
│   ├── ChatView.vue                # 知识图谱 AI 对话
│   ├── ToolsView.vue               # 工具页（预留）
│   └── GamesView.vue               # 游戏页（预留）
├── components/
│   └── chat/
│       ├── ChatWindow.vue          # 消息列表容器
│       ├── MessageBubble.vue       # 单条消息气泡
│       └── ChatInput.vue           # 输入框 + 发送按钮
├── stores/
│   └── chat.ts                     # Pinia：对话消息、上下文状态
├── services/
│   └── api.ts                      # HTTP 请求封装（SSE 流式）
└── types/
    └── chat.ts                     # Message、ToolCall 等 TypeScript 类型
```

---

## 四、状态管理（Pinia）

```typescript
// stores/chat.ts

interface ChatState {
  conversationId: string | null
  messages: Message[]           // 当前上下文消息（截断后）
  fullHistory: Message[]        // 完整历史（图谱更新时传入）
  contextTokens: number         // 估算 token 数
  isStreaming: boolean
}
```

---

## 五、类型定义

```typescript
// types/chat.ts

interface Message {
  id: string
  role: 'user' | 'assistant' | 'system' | 'tool'
  content: string
  toolCalls?: ToolCall[]
  toolCallId?: string
  timestamp: number
}

interface ToolCall {
  id: string
  name: string
  arguments: Record<string, any>
}

interface SSEChunk {
  type: 'token' | 'tool_call' | 'tool_result' | 'done' | 'error'
  data: any
}
```

---

## 六、API 服务层

```typescript
// services/api.ts

// SSE 流式对话
function sendMessage(conversationId: string | null, messages: Message[])
  -> AsyncGenerator<SSEChunk>

// 触发图谱更新
function updateGraph(conversationId: string, messages: Message[])
  -> Promise<{ summary: string }>
```

---

## 七、上下文管理

前端监听触发时机，触发后调 `/api/chat/update-graph`：

| 触发条件 | 检测方式 | 动作 |
|----------|----------|------|
| 上下文 > 8 轮 | `messages.length > 16` | 调 `updateGraph` |
| Token 超限 | `contextTokens` 估算 | 调 `updateGraph` |
| 用户空闲 > 5 分钟 | 事件监听计时器 | 调 `updateGraph` |

`updateGraph` 返回后，截断 `messages` 为最近 2 轮，插入摘要。

---

## 八、技术栈

| 项 | 选择 |
|----|------|
| 框架 | Vue 3（已有） |
| 构建 | Vite（已有） |
| 语言 | TypeScript（已有） |
| 路由 | vue-router |
| 状态管理 | Pinia |
| HTTP | fetch（SSE 用 ReadableStream） |
| CSS | 原生 CSS |
