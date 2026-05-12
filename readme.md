# AIChat 项目

---

## 知识图谱可视化完全重写计划

### 目标

彻底重写 `GraphCanvas.vue`，从"一次性全量加载+固定根节点"改为"流式渐进渲染+全节点可拖动"。根节点最先渲染，然后子节点几个几个地慢慢冒出来（播种动画），孙节点随后逐步冒出，无限自动展开直到整棵树渲染完毕。所有节点自由可动，父节点自动拉近子节点，靠近的节点互相排斥。

---

### 一、后端 API 新增

**新增四个渐进式加载接口：**

#### 1. `GET /api/graph/roots`

返回根节点（无父节点的顶层实体），用于首屏渲染。

```
GET /api/graph/roots
→ { nodes: GraphNode[], edges: GraphEdge[] }
```

根节点判定逻辑：
- 优先使用 `graph_config.root_node_ids` 配置
- 若无配置，则取所有不作为 `to` 端出现在 relations 中的实体（即无人指向它的实体）

#### 2. `GET /api/graph/children`

返回指定节点的直接子节点及其关系边。

```
GET /api/graph/children?type=User&name=AXIS
→ { nodes: GraphNode[], edges: GraphEdge[], has_children: Record<string, boolean> }
```

- `nodes`：该节点所有 `relations` 中 `to` 指向的实体
- `edges`：该节点作为 `from` 的所有 relation 边（含 `rel_type`）
- `has_children`：对每个子节点标记它是否还有 outgoing relations，前端据此决定是否继续递归

#### 3. `GET /api/graph/facts`

返回指定实体的记忆事实（用于详情面板）。

```
GET /api/graph/facts?type=User&name=AXIS
→ { facts: GraphFact[] }
```

- 按需加载，点击节点展开详情面板时调用
- 替代旧版 `fetchGraph()` 中的全量 facts 加载

#### 4. `GET /api/graph/orphans`

返回所有孤岛节点（没有任何边连接的实体）。

```
GET /api/graph/orphans
→ { nodes: GraphNode[] }
```

- Phase 3 调用，在连通分量全部播完后渲染

**接口实现位置：** `backend/app/api/graph.py`，函数 `graph_core.get_roots()` / `get_children()` / `get_facts()` / `get_orphans()`

---

### 二、GraphCanvas.vue 完全重写

**文件：** `web/AIChat/src/components/graph/GraphCanvas.vue`

**重写策略：** 删除整个旧文件（~1939行），新建一个干净的组件。保留粒子背景、搜索栏、统计徽章、详情面板等 UI 组件，核心渲染和力模拟逻辑全新实现。

#### 2.1 新的 LayoutNode 数据结构

```ts
interface LayoutNode {
  id: string           // "type|name"
  name: string
  type: string
  x: number; y: number
  vx: number; vy: number
  parentId: string | null   // 父节点 ID（null = 根节点）
  loaded: boolean           // 是否已拉取其子节点
  opacity: number           // 0→1 淡入动画（播种效果）
  targetOpacity: number
  radius: number
}
```

- **不再有 `fixed` 属性**——所有节点自由可动
- **不再有 `cloneOf`**——去掉视觉克隆逻辑，每个实体只出现一次
- **不再有 `state`**——无需展开/收起状态，全部自动展开
- 子节点通过 `parentId` 关联父节点，力模拟中产生父子吸引

#### 2.2 流式渐进渲染流程（核心）

分三个 Phase，按优先级依次渲染：

```
Phase 1 — 根节点播种（~0-1s）:
  fetchRoots() → 根节点逐个淡入（间隔 150-200ms）→ 全部加入 pendingQueue

Phase 2 — 连通分量流式展开（主体，持续到整棵连通树播完）:
  spawnLoop() 递归播种，从根节点出发沿边逐层展开
  所有通过边可达的节点都会在此阶段渲染完毕
  
Phase 3 — 孤岛节点（连通分量播完后）:
  查询所有 loaded=false 的节点（无任何边连接的实体）
  逐个淡入渲染，间隔 200ms，散布在画布边缘空白区域

onMounted:
  1. fetchConfig()              // 获取配色、根节点配置
  2. fetchRoots()               // GET /api/graph/roots → Phase 1
  3. 将根节点放入 nodes[]
     - 随机散布在画布中央
     - opacity: 0 → 1（逐个淡入，每个间隔 150-200ms）
     - 每个根节点加入 pendingQueue
  4. 启动 requestAnimationFrame 主循环（力模拟 + 渲染合一）
  5. 启动 spawnLoop()

spawnLoop() — 异步递归播种:
  每轮:
    a. 从 pendingQueue 头部取出 3~5 个节点
    b. 对每个节点并行调用 fetchChildren(node)
    c. 拿到子节点后，逐个加入 nodes[]:
       - 初始位置在父节点周围随机散布（半径 60-140px）
       - parentId = 父节点ID
       - opacity: 0，targetOpacity: 1
       - 每个子节点间隔 80-120ms push 入 nodes[]（几个几个慢慢冒出）
    d. 边逐个加入 edges[]
    e. 子节点中如果有 has_children → 加入 pendingQueue 尾部
    f. 标记 node.loaded = true
    g. 等待本轮所有子节点淡入完毕 + 250ms 冷却 → 下一轮
  
  终止条件: pendingQueue 为空 → 连通分量全部播完 → 进入 Phase 3
  
  兜底机制（防 fetchChildren 超时/失败导致 Phase 3 永不触发）：
    - 每个 fetchChildren 请求超时 10s，失败自动重试 1 次
    - 两次都失败 → 标记 node.loaded=true 并跳过，继续处理后续节点
    - 确保 pendingQueue 最终能归零

Phase 3 — 孤岛节点:
  fetchOrphans()  // GET /api/graph/orphans（或从全量 entities 中过滤）
  孤立节点逐个 push 入 nodes[]，间隔 200ms，坐标随机散布在画布边缘
```

**视觉效果**：种子在画布上不断发芽——根节点先出现 → 子节点从父节点周围冒出 → 孙节点继续冒出，力场持续把节点推开调整位置。最后孤岛节点像小行星一样从边缘飘入。整个过程流畅自然。

#### 2.3 力模拟系统（核心）

全新力导向算法。**边 = 弹力线**——每条边对其两端节点施加弹簧力，使相连节点保持自然距离。拖动节点时，弹力线将力传导至整个连通系统。排斥力使用空间哈希网格避免 O(n²)。

```ts
// ===== 每帧执行（单一 requestAnimationFrame 循环） =====
function stepForce():
  // 1. 弹力线（边 = 弹簧，连接所有有关系的节点对）
  for each edge:
    applySpring(edge.source, edge.target, restLen=120, strength=0.3)
  
  // 2. 父-子额外吸引（比普通边弹簧稍强，确保层级聚合）
  for each node where node.parentId:
    parent = findNode(node.parentId)
    applySpring(parent, node, restLen=80, strength=0.15)
  
  // 3. 空间哈希排斥力（O(n) 替代 O(n²)）
  //    cellSize 必须 ≥ repulsionMinDist*2.5=300，否则不同非邻格的节点
  //    即使距离在排斥触发范围内也会漏算，形成盲区
  buildSpatialHash(nodes, cellSize=300)
  for each cell:
    for each node a in cell:
      for each node b in cell + 8 neighbors:
        if a.id < b.id:  // 每对只算一次
          applyRepulsion(a, b, minDist=120, strength=80000)
  
  // 4. 中心引力
  for each node:
    applyCenterGravity(node, centerX, centerY, strength=0.0005)
  
  // 5. 速度阻尼 + 位置更新
  for each node:
    if !node._dragging:
      node.vx *= 0.88; node.vy *= 0.88   // 0.65 过强→节点抽搐；0.88 平滑收敛
    node.x += node.vx; node.y += node.vy
  
  // 6. 透明度插值（播种动画）
  for each node:
    node.opacity += (node.targetOpacity - node.opacity) * 0.12

// ===== 弹簧力（弹力线核心） =====
function applySpring(a, b, restLen, strength):
  dx = a.x - b.x; dy = a.y - b.y
  dist = max(sqrt(dx²+dy²), 1)
  force = strength * (dist - restLen)   // 胡克定律
  // dist > restLen → 拉近；dist < restLen → 推开
  a.vx -= (dx/dist) * force
  a.vy -= (dy/dist) * force
  b.vx += (dx/dist) * force
  b.vy += (dy/dist) * force

// ===== 排斥力 =====
function applyRepulsion(a, b, minDist, strength):
  dx = a.x - b.x; dy = a.y - b.y
  dist = max(sqrt(dx²+dy²), 1)
  if dist < minDist * 2.5:    // 仅近距离触发
    f = strength / (dist * dist)
    a.vx += (dx/dist) * f
    a.vy += (dy/dist) * f
    b.vx -= (dx/dist) * f
    b.vy -= (dy/dist) * f

// ===== 空间哈希网格 =====
function buildSpatialHash(nodes, cellSize):
  // 将画布划分为 cellSize×cellSize 的网格
  // 每个节点存入所在格子的桶中
  // 查询时只检查本格 + 8 邻格
  // 复杂度: O(n) 构建 + O(n) 查询（vs O(n²) 暴力）
```

**力模型设计理念：**
- **弹力线模型**：每条边是弹簧，`dist > restLen` 拉近，`dist < restLen` 推开。这使相连节点自然聚拢，拖动时通过弹力线牵动整个连通系统。
- **父子弹簧稍强**：父-子额外加 0.15 强度弹簧，确保层级聚合但不锁死。
- **排斥力防重叠**：所有节点靠近时互斥，保持可读性。
- **中心引力防漂散**：防止连通分量漂出视口。

**关键参数（可运行时调参）：**
| 参数 | 默认值 | 说明 |
|------|--------|------|
| edgeSpringRestLen | 120 | 边弹簧自然长度 |
| edgeSpringStrength | 0.3 | 边弹簧系数 |
| parentSpringRestLen | 80 | 父子弹簧自然长度 |
| parentSpringStrength | 0.15 | 父子弹簧额外系数 |
| repulsionMinDist | 120 | 排斥力触发距离 |
| repulsionStrength | 80000 | 排斥力系数 |
| spatialCellSize | 300 | 空间哈希格子大小（≥ repulsionMinDist×2.5，防盲区） |
| centerGravity | 0.0005 | 中心引力 |
| damping | 0.88 | 速度阻尼（每帧保留比例，0.88≈60fps下半衰期0.4s） |
| childSpawnRadius | 80-150 | 子节点初始散布半径 |

#### 2.4 拖动系统（弹力线联动）

- **所有节点可拖动**——拖拽时该节点暂时不受力影响
- **弹力线联动**：拖动节点 → 边弹簧拉动直接相连节点 → 直接相连节点再拉动它们的相连节点 → 力沿连通图传导，整个系统像渔网一样联动
- 松开后节点保留当前位置，所有节点恢复受力模拟

```
onDragStart(node):
  node._dragging = true
  
onDragMove(node, dx, dy):
  node.x += dx
  node.y += dy
  // 不手动移动子节点——依赖弹力线自然传导
  // 下一帧 stepForce() 中弹簧力会自动拉动相连节点

onDragEnd(node):
  node._dragging = false
  node.vx = 0; node.vy = 0  // 松手即停
```

**效果**：拖一个节点，弹力线像橡皮筋一样拉着所有相连节点跟随，移动幅度随距离衰减，形成自然的"渔网拖动"效果。

#### 2.5 渲染优化

- **视口裁剪**：只渲染 viewBox 可见范围内的节点和边（±200px 缓冲区）
- **节点状态样式**：
  - 正在淡入的节点（opacity < 1）：微弱的辉光脉冲
  - 已加载的节点：正常显示
  - 有子节点但尚未加载（loaded=false）：节点外围显示细微虚线环（提示还有子节点待冒出）
- **播种淡入动画**：新节点 `opacity` 从 0 过渡到 1（约 400-600ms），逐个错开
- **不再有收起动画**——节点一旦渲染就永久可见

#### 2.6 保留但简化的功能

| 功能 | 处理 |
|------|------|
| 粒子背景 | 保留，代码不变 |
| 搜索过滤 (Ctrl+F) | 保留，只搜索已加载的节点 |
| 统计徽章 | 保留，统计当前可见节点/边数 |
| 类型图例 | 保留 |
| 详情面板（侧面板） | 保留，展示选中节点的 facts 和 edges |
| 缩放平移 | 保留 |
| 深色主题 + 渐变 + 发光 | 保留 defs 和样式 |
| 视觉克隆（cloneOf） | **移除**——每实体只出现一次 |
| fixed 根节点 | **移除**——所有节点可拖动 |
| autoFit | 保留，但改为手动触发（双击空白区域） |

#### 2.7 新增功能

1. **播种动画**：子节点从父节点周围逐个淡入冒出（间隔 80-120ms），如同种子发芽
2. **节点右键菜单**：聚焦此节点 / 高亮子孙节点
3. **双击空白自适应**：重新 fit 所有已加载节点到视口
4. **键盘导航**：方向键移动选中节点
5. **渲染进度指示器**：底部状态栏显示「已加载 X/Y 节点 · 队列剩余 Z」
6. **重新布局按钮**：一键重置所有节点位置，重新从头播种（保留已加载的节点数据）

---

### 三、后端 graph.py 新增函数

**文件：** `backend/app/core/graph.py`

```python
async def get_roots() -> dict:
    """返回根节点：root_node_ids 中配置的，或无父节点的实体"""
    # 1. 优先取 graph_config 中 root_node_ids 对应的实体
    # 2. 若无配置，查询所有不作为 relations.to 的实体
    # 返回 {"nodes": [...], "edges": []}

async def get_children(entity_type: str, entity_name: str) -> dict:
    """返回指定实体的直接子节点和关系边"""
    # 查询所有 from_type=entity_type AND from_name=entity_name 的 relations
    # 返回 to 端实体 + 这些边
    # 批量查询所有子节点的 has_children（单条 SQL，非 N+1）：
    #   SELECT from_type, from_name, COUNT(*) > 0 AS has_children
    #   FROM graph_relations WHERE (from_type, from_name) IN (子节点列表)
    #   GROUP BY from_type, from_name
    # 返回 {"nodes": [...], "edges": [...], "has_children": {...}}

async def get_facts(entity_type: str, entity_name: str) -> dict:
    """返回指定实体的记忆事实"""
    # 用 SQL json_each 在数据库层过滤，避免拉全量 facts 到内存再遍历
    # SELECT * FROM graph_facts
    # WHERE EXISTS (SELECT 1 FROM json_each(about_entities) WHERE value = 'entity_name')
    # 返回 {"facts": [...]}

async def get_orphans() -> dict:
    """返回所有无边的孤岛实体（排除已在 root_node_ids 中的实体）"""
    # 查询所有不在任何 relation 的 from 或 to 中的实体
    # 必须排除 is_root=true 的中心实体（否则无 outgoing 边的根节点会同时出现在 roots 和 orphans 中）
    # 返回 {"nodes": [...]}
```

---

### 四、API 路由注册

**文件：** `backend/app/api/graph.py`

在现有 router 上新增四个端点：

```python
@router.get("/api/graph/roots")
async def get_roots():
    return await graph_core.get_roots()

@router.get("/api/graph/children")
async def get_children(type: str, name: str):
    return await graph_core.get_children(type, name)

@router.get("/api/graph/facts")
async def get_facts(type: str, name: str):
    return await graph_core.get_facts(type, name)

@router.get("/api/graph/orphans")
async def get_orphans():
    return await graph_core.get_orphans()
```

---

### 五、前端 API 层更新

**文件：** `web/AIChat/src/api/graph.ts`

```ts
// 保留现有接口，新增：
export async function fetchRoots(): Promise<GraphData>
export async function fetchChildren(type: string, name: string): Promise<{ nodes: GraphNode[], edges: GraphEdge[], has_children: Record<string, boolean> }>
export async function fetchFacts(type: string, name: string): Promise<{ facts: GraphFact[] }>
export async function fetchOrphans(): Promise<{ nodes: GraphNode[] }>
```

---

### 六、实施顺序

| 步骤 | 内容 | 预估改动量 |
|------|------|-----------|
| **1** | 后端 `graph_core.get_roots()` + `get_children()` + `get_facts()` + `get_orphans()` | ~70行 |
| **2** | 后端 API 路由注册（4个端点） | ~15行 |
| **3** | 前端 API 层 `fetchRoots()` / `fetchChildren()` / `fetchFacts()` / `fetchOrphans()` | ~20行 |
| **4** | `GraphCanvas.vue` 完全重写 | ~800-1000行（含模板+样式） |
| **5** | 调参 + 动画微调 | 测试迭代 |

**步骤 1-3 可以并行或先做，步骤 4 是核心重写，步骤 5 是调优。**

---

### 七、与旧版的对比

| 方面 | 旧版 | 新版 |
|------|------|------|
| 加载方式 | 一次性全量 `GET /api/graph` | 流式渐进渲染，几个几个慢慢冒出 |
| 首屏速度 | 节点多时慢 | 只加载根节点，极快 |
| 节点可动性 | 根节点固定，子节点可动 | 所有节点可动 |
| 力模拟 | 一次性模拟到稳定后停止 | 持续 raf 循环，新节点动态调整 |
| 边模型 | 纯视觉连线 | **弹力线**——胡克定律弹簧，拉近推远 |
| 拖动 | 只拖自己 | 弹力线传导，连通系统像渔网联动 |
| 排斥力 | O(n²) 暴力双重循环 | O(n) 空间哈希网格（8邻格） |
| 父子关系 | 仅靠边连接 | 边弹簧 + 父子额外弹簧双重吸引 |
| 孤岛节点 | 混在全量中 | Phase 3 最后从边缘飘入 |
| 视觉克隆 | 复杂的 cloneOf 逻辑 | 移除，每实体唯一 |
| 展开收起 | 不支持 | 全自动无限展开 |
| 动画效果 | 无 | 逐个播种淡入 |
| facts 加载 | 全量预加载 | 按需加载（点击节点时请求） |
| 代码行数 | ~1939 行 | ~1000 行（更清晰） |
