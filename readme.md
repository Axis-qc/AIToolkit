# AIChat

## opencode 知识图谱记忆插件

自动在用户消息前注入知识图谱记忆梗概，防止模型遗忘规范。

### 架构

```
用户发消息
    │
    ▼
opencode plugin: .opencode/plugins/kg-memory.ts
    │
    ├─ chat.message 钩子拦截
    │
    ├─ [msgCount % 3 === 0] → 全量检索
    │   POST /api/graph/search  query=用户消息
    │   └─ 返回 固定实体 + 关键词匹配实体
    │
    ├─ [其他] → 仅固定实体
    │   POST /api/graph/search  query=""
    │   └─ 返回 仅固定实体（pinned=1）
    │
    └─ 梗概以 TextPart 注入到 output.parts 前部
         └─ AI 看到：[记忆梗概]\n... \n---\n用户原始消息
```

- 每隔 3 次用户消息做一次关键词检索注入
- 中间 2 次仅注入固定实体（保底记忆）
- 固定实体无视计数规律、每轮都有
- AI 还可调自定义 `search_memory` tool 深挖

### 改动项

#### 1. 后端新增 `POST /api/graph/search`

**文件**: `backend/app/api/graph.py`

```python
@router.post("/api/graph/search")
async def search_graph(query: str = Body(...), top_k: int = Body(5)) -> dict:
    summary = await memory.search(query, top_k)
    return {"summary": summary}
```

调用 `memory.search()` 时:
- `query=用户消息` → 固定实体 + 关键词匹配实体（格式化文本）
- `query=""` → 仅固定实体（格式化文本）

#### 2. opencode 插件 `.opencode/plugins/kg-memory.ts`

依赖: `@opencode-ai/plugin`（SDK 类型）；Bun 内置 fetch

```ts
import type { Plugin } from "@opencode-ai/plugin"

const BACKEND = "http://127.0.0.1:18000"
const PREFIX = "[记忆梗概]"
const INJECT_INTERVAL = 3

export const KGMemoryPlugin: Plugin = async () => {
  let msgCount = 0

  return {
    "chat.message": async (input, output) => {
      if (input.variant) return

      const hasInjected = output.parts.some(
        p => p.type === "text" && "text" in p && (p.text as string).startsWith(PREFIX),
      )
      if (hasInjected) return

      msgCount++
      const doFullSearch = msgCount % INJECT_INTERVAL === 0
      const userText = output.parts
        .filter(p => p.type === "text" && "text" in p)
        .map(p => (p as { text: string }).text || "")
        .join("\n")
      const query = doFullSearch ? userText : ""

      try {
        const ctrl = new AbortController()
        const timer = setTimeout(() => ctrl.abort(), 5000)
        const resp = await fetch(`${BACKEND}/api/graph/search`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ query, top_k: 5 }),
          signal: ctrl.signal,
        })
        clearTimeout(timer)
        if (!resp.ok) return
        const data = await resp.json() as { summary?: string }
        if (data.summary) {
          output.parts.unshift({
            id: `prt_kgmem-${Date.now()}`,
            sessionID: input.sessionID,
            messageID: output.message.id,
            type: "text",
            text: `${PREFIX}\n${data.summary}\n\n---`,
            synthetic: true,
          } as any)
        }
      } catch {
        // 后端不可达时不阻塞用户
      }
    },
  }
}
```

### 安装

1. 后端 `graph.py` 新增 `POST /api/graph/search` 端点
2. 将 `kg-memory.ts` 放到 opencode 项目的 `.opencode/plugins/` 目录
3. 重启 opencode 自动加载
