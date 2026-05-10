# Bug 记录

## 1. Windows `localhost` 优先走 IPv6

**现象：** Vite proxy 配置 `localhost:18000`，后端 uvicorn 绑 `0.0.0.0:18000`，前端请求返回 `502 Bad Gateway`。用 `127.0.0.1` 直接访问后端正常。

**原因：** Windows 上 `localhost` 解析顺序是 IPv6 `::1` 优先于 IPv4 `127.0.0.1`。uvicorn 绑 `0.0.0.0` 只监听 IPv4，IPv6 连不上。

**修复：** Vite proxy 目标写 `http://127.0.0.1:PORT`，不用 `localhost`。同时加 `changeOrigin: true`。

```ts
// vite.config.ts - 错误写法
proxy: { '/api': 'http://localhost:18000' }

// 正确写法
proxy: { '/api': { target: 'http://127.0.0.1:18000', changeOrigin: true } }
```

---

## 2. `subprocess` 无法直接调用 `npm`/`npx`（Windows）

**现象：** `subprocess.run(["npm", "install"])` 报错 `FileNotFoundError: [WinError 2]`。

**原因：** Windows 上 `npm` 是 `npm.cmd`（批处理包装），不是可执行文件。`subprocess` 在 Windows 上不解析 PATHEXT，不会自动找 `.cmd`。

**修复：** 所有 node 工具调用都走 `cmd /c`：

```python
subprocess.run(["cmd", "/c", "npm install --silent"], cwd=FRONTEND)
subprocess.run(["cmd", "/c", "npx vite --port 15173"], cwd=FRONTEND)
subprocess.run(["cmd", "/c", "node --version"], capture_output=True)
```

---

## 3. PowerShell 中文编码乱码

**现象：** `.ps1` 脚本中文输出变乱码，即使 `chcp 65001` 也无效。

**原因：** Windows PowerShell 5.1 默认用系统 ANSI 编码（GBK），不识别 UTF-8 without BOM 的 `.ps1` 文件。

**修复：** 改方案，用 Python `start.py` 替代 `start.ps1`。Python 的 `print()` 自动处理编码。如果必须用 `.ps1`，需保存为 UTF-8 with BOM 且全部用英文。

---

## 4. Neo4j 连接阻塞 FastAPI lifespan

**现象：** uvicorn 启动后卡在 `Waiting for application startup.`，永远无法接受请求。

**原因：** FastAPI lifespan 中同步等待 Neo4j 连接。如果 Neo4j 未运行，`driver.session()` 默认超时 30s，阻塞整个启动流程。即使设了 `connection_timeout`，某些情况下异步驱动仍可能长时间挂起。

**修复：** lifespan 中用 `asyncio.create_task` 把 Neo4j 初始化丢后台，不阻塞启动：

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    async def init_neo4j():
        # 5s 超时，失败只打 warning
        ...
    asyncio.create_task(init_neo4j())
    yield
    await neo4j.close()
```

---

## 5. DeepSeek 思考模式 `reasoning_content` 必须透传

**现象：** DeepSeek 调用工具后，第二次 `/chat/completions` 请求返回 `400 - 'The reasoning_content in the thinking mode must be passed back to the API'`。

**原因：** `deepseek-v4-pro` 默认启用思考模式。AI 回复中带 `reasoning_content` 字段。当 AI 调用工具后，后续请求的 messages 数组必须包含之前 assistant 消息的 `reasoning_content`，否则 DeepSeek 拒绝。

**修复：** 流式响应中捕获 `delta.reasoning_content`，构建 assistant 消息时附上：

```python
reasoning_buffer = ""

async for chunk in response:
    delta = chunk.choices[0].delta
    if getattr(delta, 'reasoning_content', None):
        reasoning_buffer += delta.reasoning_content

assistant_msg = {"role": "assistant", "content": ..., "tool_calls": [...]}
if reasoning_buffer:
    assistant_msg["reasoning_content"] = reasoning_buffer
```

---

## 6. OpenAI/DeepSeek API `base_url` 尾部不带 `/v1`

**现象：** OpenAI SDK 调用时报 404 或连接错误。

**原因：** OpenAI Python SDK 自动在 `base_url` 后追加 `/v1`。如果 base_url 写成 `https://api.deepseek.com/v1`，实际请求变成 `https://api.deepseek.com/v1/v1/chat/completions`。

**修复：** `base_url` 写到域名即可，末尾不加 `/`，不加 `/v1`：

```python
# 正确
client = AsyncOpenAI(base_url="https://api.deepseek.com", api_key=...)

# 错误
client = AsyncOpenAI(base_url="https://api.deepseek.com/v1", api_key=...)
```

---

## 7. Python `subprocess` 工作目录与 `.env` 加载

**现象：** uvicorn 启动时报 pydantic `ValidationError: Field required`，但 `.env` 文件存在。

**原因：** `pydantic-settings` 的 `env_file=".env"` 是相对路径，相对于进程的当前工作目录。如果 uvicorn 进程的 `cwd` 不是 backend 目录，找不到 `.env`。

**修复：** 启动子进程时明确设置 `cwd`：

```python
subprocess.Popen(
    [str(uvicorn), "app.main:app", "--port", "18000"],
    cwd=BACKEND,  # 必须设置
)
```

---

## 8. PowerShell `Invoke-WebRequest` 不支持 SSE 流

**现象：** `Invoke-WebRequest http://127.0.0.1:18000/api/chat` 超时或报错，即使后端正常运行。

**原因：** `Invoke-WebRequest` 等待完整响应体才返回，SSE 是持久连接，永远不结束。PowerShell 5.1 也没有 `HttpClient`。

**修复：** 测试 SSE 端点用 Python 的 `http.client` 或 `curl`，不要用 PowerShell：

```python
import http.client, json
conn = http.client.HTTPConnection('127.0.0.1', 18000, timeout=45)
conn.request('POST', '/api/chat', body=json.dumps({...}), headers={...})
resp = conn.getresponse()
# 逐块读取
```

---

## 9. Python `urllib` 读 SSE chunked 响应报 `IncompleteRead`

**现象：** 用 `urllib.request.urlopen` 读 SSE 流时抛 `http.client.IncompleteRead`。

**原因：** urllib 的 HTTP 客户端对 chunked transfer encoding 处理不健壮。当后端关闭连接或流中断时，urllib 收到不完整的 chunk 边界会抛异常。

**修复：** 用 `http.client.HTTPConnection` 逐块 `resp.read(4096)`，捕获 `IncompleteRead` 不影响已读数据：

```python
conn = http.client.HTTPConnection('127.0.0.1', 18000, timeout=45)
conn.request('POST', '/api/chat', body=..., headers={...})
resp = conn.getresponse()
chunks = []
try:
    while True:
        chunk = resp.read(4096)
        if not chunk: break
        chunks.append(chunk.decode())
except http.client.IncompleteRead:
    pass  # 已读数据正常使用
```

---

## 常见端口占用速查

| 端口 | 用途 |
|------|------|
| 18000 | 后端 API (uvicorn) |
| 15173 | 前端 dev (Vite) |
| 8000 | 被系统 "Manager" 进程占用，已废弃 |

## 5. `addMessage` 返回 raw 对象导致 Vue 不响应式更新

**现象：** SSE token 事件逐条到达浏览器，但前端 UI 在流结束后一次性渲染全部内容。

**原因：** `addMessage` 中 `messages.value.push(msg)` 后返回原始 `msg`（非 reactive proxy）。后续 `assistantMsg.content +=` 直接修改 raw 对象，Vue Proxy 感知不到。直到其他 reactive 属性变化（如 `isStreaming`）触发全量重渲染。

**修复：** 返回 `messages.value[messages.value.length - 1]`，取数组中的 reactive proxy。遵循数据流原则：中间层只转发，不从 reactive 容器取出 raw 引用再操作。
