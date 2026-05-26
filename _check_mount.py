"""验证 sse_app 的实际路由和行为"""
from mcp.server.fastmcp import FastMCP
from fastapi import FastAPI

mcp = FastMCP("Test")

# 先注册 tool
@mcp.tool()
async def ping(msg: str = "pong") -> str:
    return f"echo: {msg}"

app = FastAPI()
sse_app = mcp.sse_app()
app.mount("/mcp", sse_app)

from httpx import AsyncClient, ASGITransport
import asyncio

async def test():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", follow_redirects=False) as client:
        # 1. GET /mcp -> 307 redirect to /mcp/sse
        r = await client.get("/mcp")
        print(f"GET /mcp: {r.status_code} -> {r.headers.get('location')}")
        
        # 2. GET /mcp/sse -> SSE stream
        r = await client.get("/mcp/sse")
        print(f"GET /mcp/sse: status={r.status_code} ct={r.headers.get('content-type')}")
        print(f"  body preview: {r.text[:200]}")
        
        # 3. 列出 sse_app 内部路由结构
        print(f"\nsse_app routes:")
        sse = mcp.sse_app()
        for route in sse.routes:
            methods = [m for m in route.methods] if hasattr(route, 'methods') else 'ASGI'
            print(f"  {route.path} -> {methods}")

asyncio.run(test())
