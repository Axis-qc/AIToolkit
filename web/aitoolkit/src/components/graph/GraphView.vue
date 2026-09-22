<script setup lang="ts">
/**
 * 知识图谱关系网视图。
 *
 * 数据流：/api/graph 取全量节点与边 → 过滤 → 布局内核 → Canvas 渲染。
 * 本组件只负责编排：持有布局实例、视口状态、交互状态与面板开关。
 * 力计算在 layout/forceSim，绘制在 layout/renderer，标签避让在 layout/labels。
 */
import { ref, computed, onMounted, onUnmounted, shallowRef, watch } from 'vue'
import { fetchFullGraph, type GraphNode } from '@/api/graph'
import { ForceSimulation, type SimInputNode, type SimLink } from './layout/forceSim'
import { placeLabels, type PlacedLabel } from './layout/labels'
import {
  renderScene,
  DEFAULT_STYLE,
  nodeHitRadius,
  nodeRadius,
  type RenderNode,
  type Viewport,
} from './layout/renderer'
import { buildTypeLegend, isHierarchyRelation } from './graphTheme'
import GraphDetailPanel from './GraphDetailPanel.vue'
import GraphFilterPanel from './GraphFilterPanel.vue'
import GraphSearchBox from './GraphSearchBox.vue'

// ===== 原始数据 =====
const rawNodes = shallowRef<GraphNode[]>([])
const rawEdges = shallowRef<{ source: string; target: string; rel_type: string }[]>([])

const loading = ref(true)
const error = ref('')

// ===== 视图状态 =====
const containerRef = ref<HTMLDivElement>()
const canvasRef = ref<HTMLCanvasElement>()
const sim = shallowRef<ForceSimulation>()
const viewport = ref<Viewport>({ scale: 1, offsetX: 0, offsetY: 0, width: 0, height: 0, dpr: 1 })

const selectedIndex = ref(-1)
const hoveredIndex = ref(-1)
const focusSet = ref<Set<number>>(new Set())
const renderedLabelCount = ref(0)

// 面板与显示选项
const showFilters = ref(true)
const showLabelsAlways = ref(false)
const labelScale = ref(1.1)
/** 筛选面板宽度，用于计算自动全览时的左侧留白。与样式中的 224px 保持一致。 */
const PANEL_WIDTH = 224

// 交互临时状态
let dragging = -1
let panning = false
let panStartX = 0
let panStartY = 0
let panOriginX = 0
let panOriginY = 0
let frameHandle = 0
let running = false
/** 本次按下是否发生过位移，用于区分点击与拖拽。 */
let dragMoved = false

// ===== 过滤条件 =====
const hiddenTypes = ref<Set<string>>(new Set())
const minImportance = ref(1)

const legend = computed(() => buildTypeLegend(rawNodes.value.map(n => n.type)))

/**
 * 过滤结果：可见节点、以可见节点下标表示的边、每条边的类型。
 * 边的 source/target 直接落成布局数组下标，避免下游再查一次映射。
 */
const filtered = computed(() => {
  const nodes: GraphNode[] = []
  const indexById = new Map<string, number>()

  for (const node of rawNodes.value) {
    if (hiddenTypes.value.has(node.type)) continue
    if ((node.importance || 1) < minImportance.value) continue
    indexById.set(node.id, nodes.length)
    nodes.push(node)
  }

  const links: SimLink[] = []
  const linkTypes: string[] = []
  const hierarchy = new Set<number>()
  const degree = new Array<number>(nodes.length).fill(0)

  for (const edge of rawEdges.value) {
    const source = indexById.get(edge.source)
    const target = indexById.get(edge.target)
    if (source === undefined || target === undefined) continue
    if (source === target) continue
    links.push({ source, target })
    linkTypes.push(edge.rel_type)
    if (isHierarchyRelation(edge.rel_type)) hierarchy.add(links.length - 1)
    degree[source] = (degree[source] ?? 0) + 1
    degree[target] = (degree[target] ?? 0) + 1
  }

  const inputs: SimInputNode[] = nodes.map((node, i) => ({
    id: node.id,
    degree: degree[i] ?? 0,
    importance: node.importance || 1,
    isRoot: !!node.is_root,
  }))

  return { nodes, inputs, links, linkTypes, hierarchy }
})

// ===== 渲染节点视图 =====
/**
 * 构建本帧的渲染节点数组。
 * 不能用 computed：布局内核每帧原地修改坐标，不触发任何响应式依赖，
 * computed 会缓存首次结果导致画面冻结在初始位置。
 */
function buildRenderNodes(): RenderNode[] {
  const simulation = sim.value
  const nodes = filtered.value.nodes
  const result: RenderNode[] = new Array(nodes.length)
  for (let i = 0; i < nodes.length; i++) {
    const node = nodes[i]!
    const positioned = simulation?.nodes[i]
    result[i] = {
      x: positioned?.x ?? 0,
      y: positioned?.y ?? 0,
      type: node.type,
      name: node.name,
      importance: node.importance || 1,
      isRoot: !!node.is_root,
    }
  }
  return result
}

// ===== 标签宽度测量 =====
const measureCtx = document.createElement('canvas').getContext('2d')

function truncate(name: string): string {
  return name.length > MAX_LABEL_CHARS ? name.slice(0, MAX_LABEL_CHARS) + '…' : name
}

function labelWidth(index: number): number {
  const name = filtered.value.nodes[index]?.name ?? ''
  const text = truncate(name)
  if (measureCtx === null) return text.length * 7 + 6
  measureCtx.font = `${DEFAULT_STYLE.labelFontSize}px ${DEFAULT_STYLE.font}`
  return measureCtx.measureText(text).width + 6
}

const MAX_LABEL_CHARS = 14

// ===== 绘制一帧 =====
function draw(): void {
  const canvas = canvasRef.value
  const simulation = sim.value
  if (!canvas || simulation === undefined) return

  const ctx = canvas.getContext('2d')
  if (ctx === null) return

  const nodes = buildRenderNodes()
  const { links, linkTypes, hierarchy } = filtered.value

  // 高亮锚点只用选中节点（由双击设置），悬停不参与高亮。
  const anchor = selectedIndex.value
  const highlightLinks = new Set<number>()
  if (anchor >= 0) {
    for (let i = 0; i < links.length; i++) {
      const link = links[i]!
      if (link.source === anchor || link.target === anchor) highlightLinks.add(i)
    }
  }

  const placed = placeLabels(nodes, {
    scale: viewport.value.scale,
    offsetX: viewport.value.offsetX,
    offsetY: viewport.value.offsetY,
    viewWidth: viewport.value.width,
    viewHeight: viewport.value.height,
    radiusOf: (index: number) => {
      const node = nodes[index]
      return node === undefined ? 4 : nodeRadius(node.importance)
    },
    widthOf: labelWidth,
    height: DEFAULT_STYLE.labelFontSize + 5,
    showAll: showLabelsAlways.value,
    labelScale: labelScale.value,
    focus: focusSet.value,
    hasSelection: selectedIndex.value >= 0,
  })

  renderedLabelCount.value = renderScene(
    ctx,
    {
      nodes,
      links,
      linkTypes,
      hierarchyLinks: hierarchy,
      selected: selectedIndex.value,
      focus: focusSet.value,
      highlightLinks,
      labels: placed,
      maxLabelChars: MAX_LABEL_CHARS,
    },
    viewport.value,
    DEFAULT_STYLE,
  )
}

// ===== 动画循环 =====
function tick(): void {
  const simulation = sim.value
  if (simulation === undefined) {
    running = false
    frameHandle = 0
    return
  }

  const alive = simulation.step()
  draw()

  // 收敛且不在拖拽时停帧，静止状态不占用 CPU。
  if (!alive && dragging < 0) {
    running = false
    frameHandle = 0
    return
  }
  frameHandle = requestAnimationFrame(tick)
}

function startLoop(): void {
  if (running) return
  running = true
  frameHandle = requestAnimationFrame(tick)
}

function stopLoop(): void {
  if (frameHandle !== 0) {
    cancelAnimationFrame(frameHandle)
    frameHandle = 0
  }
  running = false
}

// ===== 尺寸 =====
function resizeCanvas(): void {
  const container = containerRef.value
  const canvas = canvasRef.value
  if (!container || !canvas) return

  const dpr = Math.min(window.devicePixelRatio || 1, 2)
  const width = container.clientWidth
  const height = container.clientHeight

  canvas.width = Math.max(1, Math.round(width * dpr))
  canvas.height = Math.max(1, Math.round(height * dpr))
  canvas.style.width = `${width}px`
  canvas.style.height = `${height}px`

  viewport.value = { ...viewport.value, width, height, dpr }
}

/** 自动缩放到刚好铺满内容。会把筛选面板遮挡的宽度计入留白，避免图被压在面板下。 */
function fitToContent(animate = false): void {
  const simulation = sim.value
  if (simulation === undefined || simulation.nodes.length === 0) return

  const bounds = simulation.bounds()
  const { width, height } = viewport.value
  if (width === 0 || height === 0) return

  const padding = 60
  // 筛选面板浮在画布左侧，展开时把它占用的宽度算作左留白，
  // 让内容在未被遮挡的区域居中，而不是整块画布居中。
  const leftInset = showFilters.value ? PANEL_WIDTH + 34 : 0

  const availWidth = Math.max(80, width - leftInset - padding * 2)
  const availHeight = Math.max(80, height - padding * 2)
  const contentWidth = Math.max(1, bounds.maxX - bounds.minX)
  const contentHeight = Math.max(1, bounds.maxY - bounds.minY)

  let scale = Math.min(availWidth / contentWidth, availHeight / contentHeight)
  scale = Math.max(0.08, Math.min(2.2, scale))

  const cx = (bounds.minX + bounds.maxX) / 2
  const cy = (bounds.minY + bounds.maxY) / 2
  const target: Viewport = {
    ...viewport.value,
    scale,
    offsetX: leftInset + padding + availWidth / 2 - cx * scale,
    offsetY: padding + availHeight / 2 - cy * scale,
  }

  if (animate) animateViewport(target)
  else {
    viewport.value = target
    draw()
  }
}

/** 平滑移动视口，用于搜索定位与全览。 */
function animateViewport(target: Viewport, duration = 420): void {
  const start = { ...viewport.value }
  const t0 = performance.now()

  function frame(now: number): void {
    const t = Math.min(1, (now - t0) / duration)
    const e = 1 - Math.pow(1 - t, 3)
    viewport.value = {
      scale: start.scale + (target.scale - start.scale) * e,
      offsetX: start.offsetX + (target.offsetX - start.offsetX) * e,
      offsetY: start.offsetY + (target.offsetY - start.offsetY) * e,
      width: target.width,
      height: target.height,
      dpr: target.dpr,
    }
    draw()
    if (t < 1) requestAnimationFrame(frame)
  }
  requestAnimationFrame(frame)
}

/** 把镜头对准某个节点。会避开筛选面板遮挡的区域，保证目标可见。 */
function focusOnNode(index: number, scale = 1.5): void {
  const simulation = sim.value
  const node = simulation?.nodes[index]
  if (node === undefined) return
  const { width, height, dpr } = viewport.value
  // 面板展开时把可见区域的中心右移，否则目标节点会被面板压住。
  const leftInset = showFilters.value ? PANEL_WIDTH + 34 : 0
  const visibleWidth = Math.max(120, width - leftInset)
  animateViewport({
    scale,
    offsetX: leftInset + visibleWidth / 2 - node.x * scale,
    offsetY: height / 2 - node.y * scale,
    width,
    height,
    dpr,
  })
}

// ===== 邻域聚焦 =====
function updateFocus(index: number): void {
  const simulation = sim.value
  if (simulation === undefined || index < 0) {
    focusSet.value = new Set()
    return
  }
  const set = new Set<number>([index])
  for (const neighbor of simulation.neighborsOf(index)) set.add(neighbor)
  focusSet.value = set
}

// ===== 坐标换算与命中 =====
function toWorld(clientX: number, clientY: number): { x: number; y: number } {
  const canvas = canvasRef.value
  if (!canvas) return { x: 0, y: 0 }
  const rect = canvas.getBoundingClientRect()
  const { scale, offsetX, offsetY } = viewport.value
  return {
    x: (clientX - rect.left - offsetX) / scale,
    y: (clientY - rect.top - offsetY) / scale,
  }
}

function hitTest(clientX: number, clientY: number): number {
  const simulation = sim.value
  if (simulation === undefined) return -1
  const world = toWorld(clientX, clientY)
  const nodes = filtered.value.nodes
  const { scale } = viewport.value

  let best = -1
  let bestDistance = Infinity
  for (let i = 0; i < simulation.nodes.length; i++) {
    const positioned = simulation.nodes[i]!
    const node = nodes[i]
    if (node === undefined) continue
    const distance = Math.hypot(positioned.x - world.x, positioned.y - world.y)
    // 命中半径按缩放折算，保证缩小时也能点中。
    const radius = nodeHitRadius(node.importance || 1) / scale + 3
    if (distance <= radius && distance < bestDistance) {
      bestDistance = distance
      best = i
    }
  }
  return best
}

// ===== 鼠标交互 =====
function onPointerDown(event: PointerEvent): void {
  const canvas = canvasRef.value
  if (!canvas) return
  canvas.setPointerCapture(event.pointerId)
  dragMoved = false

  const hit = event.button === 0 ? hitTest(event.clientX, event.clientY) : -1
  if (hit >= 0) {
    dragging = hit
    const world = toWorld(event.clientX, event.clientY)
    sim.value?.pin(hit, world.x, world.y)
    startLoop()
    return
  }

  if (event.button === 0) {
    panning = true
    panStartX = event.clientX
    panStartY = event.clientY
    panOriginX = viewport.value.offsetX
    panOriginY = viewport.value.offsetY
  }
}

function onPointerMove(event: PointerEvent): void {
  if (dragging >= 0) {
    const world = toWorld(event.clientX, event.clientY)
    sim.value?.pin(dragging, world.x, world.y)
    dragMoved = true
    return
  }

  if (panning) {
    const dx = event.clientX - panStartX
    const dy = event.clientY - panStartY
    if (Math.abs(dx) > 2 || Math.abs(dy) > 2) dragMoved = true
    viewport.value = {
      ...viewport.value,
      offsetX: panOriginX + dx,
      offsetY: panOriginY + dy,
    }
    draw()
    return
  }

  // 悬停只改变光标形状，不做任何高亮或淡化。
  // 重点显示统一由双击触发，避免鼠标扫过就把整张图压暗。
  const hit = hitTest(event.clientX, event.clientY)
  if (hit !== hoveredIndex.value) hoveredIndex.value = hit
  const canvas = canvasRef.value
  if (canvas) canvas.style.cursor = hit >= 0 ? 'pointer' : 'grab'
}

function onPointerUp(event: PointerEvent): void {
  const canvas = canvasRef.value
  if (canvas && canvas.hasPointerCapture(event.pointerId)) {
    canvas.releasePointerCapture(event.pointerId)
  }

  if (dragging >= 0) {
    // 真正拖动过才把节点留在落点并重新加热；单纯单击完全释放，
    // 否则每次点击都会让布局重新抖动一下。
    sim.value?.unpin(dragging, !dragMoved)
    if (dragMoved) {
      sim.value?.reheat(0.3)
      startLoop()
    } else {
      draw()
    }
    dragging = -1
    return
  }

  if (panning) {
    panning = false
  }
}

function onWheel(event: WheelEvent): void {
  event.preventDefault()
  const canvas = canvasRef.value
  if (!canvas) return

  const rect = canvas.getBoundingClientRect()
  const px = event.clientX - rect.left
  const py = event.clientY - rect.top
  const { scale, offsetX, offsetY } = viewport.value

  const factor = Math.exp(-event.deltaY * 0.0015)
  const next = Math.max(0.08, Math.min(4, scale * factor))

  // 以光标为锚点缩放：光标下的世界坐标保持不动。
  const worldX = (px - offsetX) / scale
  const worldY = (py - offsetY) / scale
  viewport.value = {
    ...viewport.value,
    scale: next,
    offsetX: px - worldX * next,
    offsetY: py - worldY * next,
  }
  draw()
}

/**
 * 单击：只负责退出重点显示。命中节点时不作反应——单击的用途是拖拽，
 * 若单击即选中，拖动前的那一下按下会把节点钉住并弹开详情面板。
 */
function onCanvasClick(event: MouseEvent): void {
  if (dragMoved) return
  const hit = hitTest(event.clientX, event.clientY)
  if (hit < 0) clearSelection()
}

/**
 * 双击：进入重点显示。
 * 打开详情面板、高亮该节点的一跳邻域、并把其余元素压暗，
 * 同时高亮它的关联边并显示关系类型。
 */
function onCanvasDblClick(event: MouseEvent): void {
  if (dragMoved) return
  const hit = hitTest(event.clientX, event.clientY)
  if (hit < 0) {
    clearSelection()
    return
  }
  selectNode(hit)
  focusOnNode(hit, Math.max(1.2, viewport.value.scale))
}

// ===== 选中 =====
function selectNode(index: number): void {
  selectedIndex.value = index
  updateFocus(index)
  draw()
}

function clearSelection(): void {
  selectedIndex.value = -1
  updateFocus(-1)
  draw()
}

const selectedNode = computed<GraphNode | null>(() => {
  const node = filtered.value.nodes[selectedIndex.value]
  return node ?? null
})

// ===== 搜索命中 =====
function onSearchSelect(nodeId: string): void {
  const index = filtered.value.nodes.findIndex(n => n.id === nodeId)
  if (index < 0) return
  selectNode(index)
  focusOnNode(index)
}

// ===== 建立与重建布局 =====
/**
 * 同步跑完布局并适配视口。
 * 相比让动画帧逐步收敛，这样每次重建后画面立即是稳定完整的，
 * 不会出现从中心膨胀数秒的中间态。实测 622 节点收敛约 220ms。
 */
function settleAndFit(): void {
  const simulation = sim.value
  if (simulation === undefined) return
  let guard = 0
  while (simulation.step() && guard < 600) guard++
  stopLoop()
  fitToContent(false)
}

function rebuild(preservePositions: boolean, fit = true): void {
  const { inputs, links } = filtered.value
  const previous = preservePositions ? sim.value?.nodes : undefined

  let simulation = sim.value
  if (simulation === undefined) {
    simulation = new ForceSimulation()
    sim.value = simulation
  }
  simulation.setGraph(inputs, links, previous)
  selectedIndex.value = -1
  hoveredIndex.value = -1
  focusSet.value = new Set()

  if (fit) settleAndFit()
  else {
    startLoop()
  }
}

// 过滤变化时重建，沿用已有坐标避免整张图跳变。
watch([hiddenTypes, minImportance], () => {
  rebuild(true)
})

// 标签显示选项只影响绘制，不必重跑布局。
watch([showLabelsAlways, labelScale], () => {
  draw()
})

// 筛选面板开合会改变可用视口宽度，重新全览一次让内容保持居中。
watch(showFilters, () => {
  fitToContent(true)
})

// ===== 生命周期 =====
async function load(): Promise<void> {
  loading.value = true
  error.value = ''
  try {
    const data = await fetchFullGraph()
    rawNodes.value = data.nodes
    rawEdges.value = data.edges
  } catch (e) {
    console.error(e)
    error.value = '加载图谱数据失败'
    loading.value = false
    return
  }

  loading.value = false
  if (rawNodes.value.length === 0) return

  // 等一帧让 canvas 获得实际尺寸。
  await new Promise<void>(resolve => requestAnimationFrame(() => resolve()))
  resizeCanvas()
  // rebuild 内部会同步跑完布局并适配视口。
  rebuild(false)
}

function onResize(): void {
  resizeCanvas()
  draw()
}

let resizeTimer = 0
function onWindowResize(): void {
  if (resizeTimer !== 0) window.clearTimeout(resizeTimer)
  resizeTimer = window.setTimeout(onResize, 150)
}

onMounted(() => {
  load()
  window.addEventListener('resize', onWindowResize)
})

onUnmounted(() => {
  stopLoop()
  if (resizeTimer !== 0) window.clearTimeout(resizeTimer)
  window.removeEventListener('resize', onWindowResize)
})

function resetView(): void {
  fitToContent(true)
}

function toggleType(type: string): void {
  const next = new Set(hiddenTypes.value)
  if (next.has(type)) next.delete(type)
  else next.add(type)
  hiddenTypes.value = next
}

function resetFilters(): void {
  hiddenTypes.value = new Set()
  minImportance.value = 1
}

const visibleCount = computed(() => filtered.value.nodes.length)
const totalCount = computed(() => rawNodes.value.length)
</script>

<template>
  <div class="graph-view">
    <div ref="containerRef" class="canvas-wrap">
      <canvas
        v-show="!loading && !error"
        ref="canvasRef"
        class="graph-canvas"
        @pointerdown="onPointerDown"
        @pointermove="onPointerMove"
        @pointerup="onPointerUp"
        @pointercancel="onPointerUp"
        @wheel="onWheel"
        @click="onCanvasClick"
        @dblclick="onCanvasDblClick"
      />

      <!-- 加载 -->
      <div v-if="loading" class="status-view">
        <div class="orbit-loader">
          <div class="orbit-ring o-1" /><div class="orbit-ring o-2" /><div class="orbit-ring o-3" />
          <div class="orbit-core" />
        </div>
        <span class="status-text">加载图谱</span>
      </div>

      <!-- 错误 -->
      <div v-else-if="error" class="status-view">
        <span class="status-text">{{ error }}</span>
      </div>

      <!-- 空 -->
      <div v-else-if="totalCount === 0" class="status-view">
        <span class="status-text">暂无图谱数据</span>
      </div>

      <!-- 工具栏 -->
      <div v-else class="toolbar">
        <GraphSearchBox :nodes="filtered.nodes" @select="onSearchSelect" />
        <button
          class="tool-btn"
          :class="{ active: showLabelsAlways }"
          title="始终显示全部标签"
          @click="showLabelsAlways = !showLabelsAlways"
        >
          全部标签
        </button>
        <button
          class="tool-btn"
          :class="{ active: showFilters }"
          title="显示或隐藏筛选面板"
          @click="showFilters = !showFilters"
        >
          筛选
        </button>
        <button class="tool-btn" title="缩放到全部内容" @click="resetView">全览</button>
      </div>

      <!-- 统计 -->
      <div v-if="!loading && !error && totalCount > 0" class="stats">
        <span v-text="visibleCount + ' / ' + totalCount + ' 节点'" />
        <span class="stats-sep">·</span>
        <span v-text="renderedLabelCount + ' 标签'" />
      </div>

      <!-- 提示 -->
      <div v-if="!loading && !error && totalCount > 0" class="hint">
        滚轮缩放 · 拖拽平移 · 拖动节点调整 · 双击查看关系
      </div>

      <!-- 筛选面板 -->
      <GraphFilterPanel
        v-if="!loading && !error && totalCount > 0 && showFilters"
        :legend="legend"
        :hidden-types="hiddenTypes"
        :min-importance="minImportance"
        :label-scale="labelScale"
        :visible-count="visibleCount"
        :total-count="totalCount"
        @toggle-type="toggleType"
        @update:min-importance="minImportance = $event"
        @update:label-scale="labelScale = $event"
        @reset="resetFilters"
        @close="showFilters = false"
      />

      <!-- 详情面板 -->
      <GraphDetailPanel
        v-if="selectedNode"
        :node="selectedNode"
        @close="clearSelection"
      />
    </div>
  </div>
</template>

<style scoped>
.graph-view {
  width: 100%;
  height: 100%;
  position: relative;
  background: #08080b;
}

.canvas-wrap {
  width: 100%;
  height: 100%;
  position: relative;
  overflow: hidden;
}

.graph-canvas {
  width: 100%;
  height: 100%;
  display: block;
  touch-action: none;
  cursor: grab;
}

/* 状态 */
.status-view {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 1rem;
  pointer-events: none;
}
.status-text { color: var(--muted); font-size: 0.85rem; letter-spacing: 0.06em; }

.orbit-loader {
  position: relative; width: 60px; height: 60px;
  display: flex; align-items: center; justify-content: center;
}
.orbit-core {
  width: 6px; height: 6px; border-radius: 50%; background: var(--accent);
  box-shadow: 0 0 12px color-mix(in srgb, var(--accent) 50%, transparent);
  z-index: 1;
}
.orbit-ring {
  position: absolute; inset: 0; border-radius: 50%;
  border: 1px solid transparent; opacity: 0.5;
}
.o-1 { border-color: color-mix(in srgb, var(--accent) 30%, transparent); animation: orbit-spin 2.4s linear infinite; }
.o-2 { inset: 8px; border-color: rgba(126, 226, 168, 0.25); animation: orbit-spin 1.8s linear infinite reverse; }
.o-3 { inset: 16px; border-color: rgba(243, 189, 104, 0.2); animation: orbit-spin 3s linear infinite; }
@keyframes orbit-spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }

/* 工具栏 */
.toolbar {
  position: absolute; top: 14px; left: 50%; transform: translateX(-50%);
  display: flex; align-items: center; gap: 6px; z-index: 12;
  background: rgba(10, 10, 13, 0.92);
  backdrop-filter: blur(12px);
  border: 1px solid var(--line);
  border-radius: 20px;
  padding: 4px 6px;
  box-shadow: 0 12px 35px rgba(0, 0, 0, 0.25);
}

.tool-btn {
  border: none;
  background: transparent;
  color: var(--muted);
  font-size: 0.72rem;
  font-family: inherit;
  padding: 5px 12px;
  border-radius: 14px;
  cursor: pointer;
  white-space: nowrap;
  transition: all 0.18s;
}
.tool-btn:hover {
  color: var(--text);
  background: color-mix(in srgb, var(--accent) 8%, transparent);
}
.tool-btn.active {
  color: var(--accent);
  background: color-mix(in srgb, var(--accent) 12%, transparent);
  box-shadow: inset 0 0 0 1px var(--line-strong);
}

/* 统计 */
.stats {
  position: absolute; top: 16px; right: 18px; z-index: 11;
  display: flex; align-items: center; gap: 6px;
  font-size: 0.68rem; color: var(--muted);
  background: rgba(10, 10, 13, 0.86);
  border: 1px solid var(--line);
  border-radius: 14px;
  padding: 5px 12px;
  pointer-events: none;
}
.stats-sep { opacity: 0.5; }

/* 提示 */
.hint {
  position: absolute; bottom: 12px; left: 50%; transform: translateX(-50%);
  font-size: 0.64rem; color: var(--muted);
  letter-spacing: 0.04em;
  background: rgba(10, 10, 13, 0.84);
  border: 1px solid var(--line);
  border-radius: 20px;
  padding: 3px 14px;
  pointer-events: none;
  z-index: 2;
}
</style>
