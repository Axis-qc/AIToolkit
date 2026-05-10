<script setup lang="ts">
import { ref, onMounted, onUnmounted, computed } from 'vue'
import { fetchGraph, type GraphNode, type GraphEdge, type GraphFact } from '@/api/graph'

interface LayoutNode {
  id: string
  name: string
  type: string
  x: number
  y: number
  vx: number
  vy: number
  fixed: boolean
}

interface LayoutEdge {
  source: string
  target: string
  rel_type: string
}

const nodes = ref<LayoutNode[]>([])
const edges = ref<LayoutEdge[]>([])
const facts = ref<GraphFact[]>([])
const loading = ref(true)
const empty = ref(false)
const selectedId = ref<string | null>(null)

const scale = ref(1)
const tx = ref(0)
const ty = ref(0)
const svgWidth = ref(800)
const svgHeight = ref(600)

const panStart = ref({ x: 0, y: 0 })
const dragStart = ref({ x: 0, y: 0, nodeX: 0, nodeY: 0 })
const mode = ref<'idle' | 'pan' | 'drag'>('idle')

const COLOR: Record<string, string> = {
  User: '#f59e0b',
  preference: '#a78bfa',
  fact: '#34d399',
  event: '#60a5fa',
  plan: '#f87171',
  topic: '#fb923c',
  todo: '#fbbf24',
  conflict: '#ef4444',
  pending: '#6b7280',
}

function nodeColor(type: string): string {
  return COLOR[type] || '#7c7c90'
}

function nodeRadius(type: string): number {
  return type === 'User' ? 28 : 18
}

function typeLabel(type: string): string {
  const m: Record<string, string> = {
    User: '用户',
    preference: '偏好',
    fact: '事实',
    event: '事件',
    plan: '计划',
    topic: '主题',
    todo: '待办',
    conflict: '冲突',
    pending: '待跟进',
  }
  return m[type] || type
}

const selectedNode = computed(() => {
  return nodes.value.find(n => n.id === selectedId.value) || null
})

const nodeFacts = computed(() => {
  if (!selectedId.value) return []
  return facts.value.filter(f =>
    f.about_entities.some(e => e === selectedId.value),
  )
})

const nodeEdges = computed(() => {
  if (!selectedId.value) return []
  return edges.value.filter(
    e => e.source === selectedId.value || e.target === selectedId.value,
  )
})

function edgeLabelPos(edge: LayoutEdge) {
  const s = nodes.value.find(n => n.id === edge.source)
  const t = nodes.value.find(n => n.id === edge.target)
  if (!s || !t) return { x: 0, y: 0 }
  const mx = (s.x + t.x) / 2
  const my = (s.y + t.y) / 2
  const dx = t.x - s.x
  const dy = t.y - s.y
  const len = Math.sqrt(dx * dx + dy * dy) || 1
  const offset = 10
  return { x: mx - (dy / len) * offset, y: my + (dx / len) * offset }
}

function simulate() {
  const cx = svgWidth.value / 2
  const cy = svgHeight.value / 2

  for (const node of nodes.value) {
    if (node.fixed) {
      node.x = cx
      node.y = cy
    } else {
      const angle = Math.random() * 2 * Math.PI
      const r = 180 + Math.random() * 200
      node.x = cx + Math.cos(angle) * r
      node.y = cy + Math.sin(angle) * r
    }
    node.vx = 0
    node.vy = 0
  }

  const kRep = 8000
  const kAtt = 0.005
  const restLen = 200
  const damping = 0.6

  for (let iter = 0; iter < 100; iter++) {
    for (let i = 0; i < nodes.value.length; i++) {
      for (let j = i + 1; j < nodes.value.length; j++) {
        const a = nodes.value[i]!
        const b = nodes.value[j]!
        const dx = a.x - b.x
        const dy = a.y - b.y
        const dist = Math.max(Math.sqrt(dx * dx + dy * dy), 1)
        const force = kRep / (dist * dist)
        const fx = (dx / dist) * force
        const fy = (dy / dist) * force
        if (!a.fixed) { a.vx += fx; a.vy += fy }
        if (!b.fixed) { b.vx -= fx; b.vy -= fy }
      }
    }

    for (const edge of edges.value) {
      const s = nodes.value.find(n => n.id === edge.source)
      const t = nodes.value.find(n => n.id === edge.target)
      if (!s || !t) continue
      const dx = t.x - s.x
      const dy = t.y - s.y
      const dist = Math.max(Math.sqrt(dx * dx + dy * dy), 1)
      const force = kAtt * (dist - restLen)
      const fx = (dx / dist) * force
      const fy = (dy / dist) * force
      if (!s.fixed) { s.vx += fx; s.vy += fy }
      if (!t.fixed) { t.vx -= fx; t.vy -= fy }
    }

    for (const node of nodes.value) {
      if (node.fixed) continue
      node.vx += (cx - node.x) * 0.0005
      node.vy += (cy - node.y) * 0.0005
    }

    for (const node of nodes.value) {
      if (node.fixed) continue
      node.vx *= damping
      node.vy *= damping
      node.x += node.vx
      node.y += node.vy
    }

    const user = nodes.value.find(n => n.fixed)
    if (user) { user.x = cx; user.y = cy }
  }
}

function onMouseDown(e: MouseEvent) {
  const target = e.target as SVGElement
  const circle = target.closest('circle')
  if (circle) {
    const nodeId = circle.getAttribute('data-id')
    const node = nodes.value.find(n => n.id === nodeId)
    if (node && !node.fixed) {
      mode.value = 'drag'
      dragStart.value = { x: e.clientX, y: e.clientY, nodeX: node.x, nodeY: node.y }
    }
    return
  }
  mode.value = 'pan'
  panStart.value = { x: e.clientX - tx.value, y: e.clientY - ty.value }
}

function onMouseMove(e: MouseEvent) {
  if (mode.value === 'pan') {
    tx.value = e.clientX - panStart.value.x
    ty.value = e.clientY - panStart.value.y
  } else if (mode.value === 'drag') {
    const node = nodes.value.find(
      n => n.x === dragStart.value.nodeX && n.y === dragStart.value.nodeY,
    )
    if (node) {
      node.x = dragStart.value.nodeX + (e.clientX - dragStart.value.x) / scale.value
      node.y = dragStart.value.nodeY + (e.clientY - dragStart.value.y) / scale.value
    }
  }
}

function onMouseUp() {
  mode.value = 'idle'
}

function onWheel(e: WheelEvent) {
  e.preventDefault()
  const rect = (e.currentTarget as SVGSVGElement).getBoundingClientRect()
  const mx = e.clientX - rect.left
  const my = e.clientY - rect.top
  const factor = e.deltaY > 0 ? 0.92 : 1.08
  const newScale = Math.max(0.15, Math.min(4, scale.value * factor))
  tx.value = mx - (mx - tx.value) * (newScale / scale.value)
  ty.value = my - (my - ty.value) * (newScale / scale.value)
  scale.value = newScale
}

function onNodeClick(node: LayoutNode) {
  selectedId.value = selectedId.value === node.id ? null : node.id
}

function closePanel() {
  selectedId.value = null
}

async function refresh() {
  loading.value = true
  try {
    const data = await fetchGraph()
    if (data.nodes.length === 0) {
      empty.value = true
      nodes.value = []
      edges.value = []
      facts.value = []
    } else {
      empty.value = false
      nodes.value = data.nodes.map(n => ({
        ...n,
        x: 0, y: 0, vx: 0, vy: 0,
        fixed: n.id === 'User|default',
      }))
      edges.value = data.edges
      facts.value = data.facts
      simulate()
    }
  } catch {
    empty.value = true
  } finally {
    loading.value = false
  }
}

onMounted(refresh)

function onKeyDown(e: KeyboardEvent) {
  if (e.key === 'Escape') closePanel()
}

onMounted(() => document.addEventListener('keydown', onKeyDown))
onUnmounted(() => document.removeEventListener('keydown', onKeyDown))
</script>

<template>
  <div class="graph-container">
    <div v-if="loading" class="graph-status">加载中...</div>
    <div v-else-if="empty" class="graph-status">暂无图谱数据</div>

    <svg
      v-show="!loading && !empty"
      class="graph-svg"
      :viewBox="`0 0 ${svgWidth} ${svgHeight}`"
      preserveAspectRatio="xMidYMid meet"
      @mousedown="onMouseDown"
      @mousemove="onMouseMove"
      @mouseup="onMouseUp"
      @mouseleave="onMouseUp"
      @wheel.prevent="onWheel"
    >
      <g :transform="`translate(${tx}, ${ty}) scale(${scale})`">
        <line
          v-for="edge in edges"
          :key="`${edge.source}-${edge.target}`"
          :x1="nodes.find(n => n.id === edge.source)?.x || 0"
          :y1="nodes.find(n => n.id === edge.source)?.y || 0"
          :x2="nodes.find(n => n.id === edge.target)?.x || 0"
          :y2="nodes.find(n => n.id === edge.target)?.y || 0"
          class="graph-edge"
        />
        <text
          v-for="edge in edges"
          :key="`label-${edge.source}-${edge.target}`"
          :x="edgeLabelPos(edge).x"
          :y="edgeLabelPos(edge).y"
          class="edge-label"
        >{{ edge.rel_type }}</text>

        <g
          v-for="node in nodes"
          :key="node.id"
          class="node-group"
          @click.stop="onNodeClick(node)"
        >
          <circle
            :cx="node.x"
            :cy="node.y"
            :r="nodeRadius(node.type)"
            :fill="nodeColor(node.type)"
            :stroke="selectedId === node.id ? '#fff' : 'rgba(0,0,0,0.3)'"
            :stroke-width="selectedId === node.id ? 3 : 1.5"
            :data-id="node.id"
            class="node-circle"
          />
          <text
            :x="node.x"
            :y="node.y"
            class="node-label"
            text-anchor="middle"
            dominant-baseline="central"
          >{{ node.type === 'User' ? '我' : node.name }}</text>
        </g>
      </g>
    </svg>

    <aside v-if="selectedNode" class="detail-panel">
      <button class="panel-close" @click="closePanel">&times;</button>
      <div class="panel-header">
        <span
          class="panel-dot"
          :style="{ background: nodeColor(selectedNode.type) }"
        />
        <span class="panel-name">{{ selectedNode.name }}</span>
        <span class="panel-type">{{ typeLabel(selectedNode.type) }}</span>
      </div>

      <div v-if="nodeFacts.length > 0" class="panel-section">
        <div class="panel-section-title">关联事实</div>
        <div
          v-for="(f, i) in nodeFacts"
          :key="i"
          class="panel-fact"
        >
          <span class="fact-type-tag" :style="{ color: nodeColor(f.type) }">
            {{ typeLabel(f.type) }}
          </span>
          <span>{{ f.content }}</span>
        </div>
      </div>

      <div v-if="nodeEdges.length > 0" class="panel-section">
        <div class="panel-section-title">关联关系</div>
        <div
          v-for="(e, i) in nodeEdges"
          :key="i"
          class="panel-rel"
        >
          <span class="rel-dir">{{ e.source === selectedNode.id ? '→' : '←' }}</span>
          <span class="rel-type">{{ e.rel_type }}</span>
          <span class="rel-target">
            {{ e.source === selectedNode.id
              ? (nodes.find(n => n.id === e.target)?.name || e.target)
              : (nodes.find(n => n.id === e.source)?.name || e.source)
            }}
          </span>
        </div>
      </div>

      <div v-if="nodeFacts.length === 0 && nodeEdges.length === 0" class="panel-empty">
        暂无关联信息
      </div>
    </aside>
  </div>
</template>

<style scoped>
.graph-container {
  width: 100%;
  height: 100%;
  position: relative;
  background: #0a0a10;
  overflow: hidden;
}

.graph-status {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  color: #4a4a5a;
  font-size: 0.9rem;
}

.graph-svg {
  width: 100%;
  height: 100%;
  cursor: grab;
}

.graph-svg:active {
  cursor: grabbing;
}

.graph-edge {
  stroke: rgba(255, 255, 255, 0.12);
  stroke-width: 1.5;
}

.edge-label {
  fill: #5a5a6e;
  font-size: 10px;
  text-anchor: middle;
  pointer-events: none;
  user-select: none;
}

.node-group {
  cursor: pointer;
}

.node-circle {
  transition: stroke-width 0.2s, stroke 0.2s;
}

.node-circle:hover {
  stroke: rgba(255, 255, 255, 0.5);
  stroke-width: 2.5;
}

.node-label {
  fill: #fff;
  font-size: 11px;
  font-weight: 500;
  pointer-events: none;
  user-select: none;
  text-shadow: 0 1px 3px rgba(0, 0, 0, 0.6);
}

.detail-panel {
  position: absolute;
  top: 0;
  right: 0;
  width: 280px;
  height: 100%;
  background: rgba(10, 10, 16, 0.95);
  border-left: 1px solid rgba(255, 255, 255, 0.06);
  padding: 1.25rem;
  overflow-y: auto;
  z-index: 10;
}

.panel-close {
  position: absolute;
  top: 0.75rem;
  right: 0.75rem;
  width: 24px;
  height: 24px;
  border: none;
  background: rgba(255, 255, 255, 0.04);
  color: #6b6b80;
  border-radius: 6px;
  cursor: pointer;
  font-size: 1rem;
  line-height: 1;
  transition: all 0.2s;
}

.panel-close:hover {
  background: rgba(255, 255, 255, 0.08);
  color: #c8c8d8;
}

.panel-header {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  margin-bottom: 1.25rem;
  padding-right: 1.5rem;
}

.panel-dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  flex-shrink: 0;
}

.panel-name {
  font-size: 1rem;
  font-weight: 600;
  color: #d0d0dc;
}

.panel-type {
  font-size: 0.72rem;
  color: #505060;
  background: rgba(255, 255, 255, 0.04);
  padding: 0.1rem 0.4rem;
  border-radius: 4px;
}

.panel-section {
  margin-bottom: 1rem;
}

.panel-section-title {
  font-size: 0.7rem;
  font-weight: 600;
  color: #4a4a5a;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  margin-bottom: 0.4rem;
}

.panel-fact {
  font-size: 0.78rem;
  color: #8a8a9c;
  line-height: 1.5;
  padding: 0.4rem 0;
  border-bottom: 1px solid rgba(255, 255, 255, 0.03);
  display: flex;
  gap: 0.4rem;
  align-items: flex-start;
}

.fact-type-tag {
  font-size: 0.65rem;
  font-weight: 600;
  flex-shrink: 0;
  padding-top: 1px;
}

.panel-rel {
  font-size: 0.78rem;
  color: #8a8a9c;
  padding: 0.35rem 0;
  border-bottom: 1px solid rgba(255, 255, 255, 0.03);
  display: flex;
  align-items: center;
  gap: 0.4rem;
}

.rel-dir {
  color: #505060;
  font-size: 0.9rem;
  flex-shrink: 0;
}

.rel-type {
  color: #a78bfa;
  font-size: 0.72rem;
  background: rgba(124, 92, 252, 0.1);
  padding: 0.05rem 0.35rem;
  border-radius: 4px;
  flex-shrink: 0;
}

.rel-target {
  color: #c8c8d8;
}

.panel-empty {
  color: #4a4a5a;
  font-size: 0.8rem;
  text-align: center;
  padding: 2rem 0;
}
</style>
