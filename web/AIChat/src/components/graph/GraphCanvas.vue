<script setup lang="ts">
import { ref, onMounted, onUnmounted, computed, watch, nextTick } from 'vue'
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

interface Particle {
  x: number; y: number; vx: number; vy: number
  r: number; alpha: number; alphaDir: number
}

const nodes = ref<LayoutNode[]>([])
const edges = ref<LayoutEdge[]>([])
const facts = ref<GraphFact[]>([])
const loading = ref(true)
const empty = ref(false)
const selectedId = ref<string | null>(null)
const searchQuery = ref('')
const showSearch = ref(false)
const hoveredNodeId = ref<string | null>(null)
const animFrame = ref(0)
const showUI = ref(false)

const scale = ref(1)
const tx = ref(0)
const ty = ref(0)
const svgWidth = ref(800)
const svgHeight = ref(600)

const panStart = ref({ x: 0, y: 0 })
const dragStart = ref({ x: 0, y: 0, nodeX: 0, nodeY: 0 })
const mode = ref<'idle' | 'pan' | 'drag'>('idle')

// ===== 粒子系统 =====
const canvasRef = ref<HTMLCanvasElement | null>(null)
const particles = ref<Particle[]>([])
const PARTICLE_COUNT = 70

function initParticles(w: number, h: number) {
  const arr: Particle[] = []
  for (let i = 0; i < PARTICLE_COUNT; i++) {
    arr.push({
      x: Math.random() * w, y: Math.random() * h,
      vx: (Math.random() - 0.5) * 0.3, vy: (Math.random() - 0.5) * 0.3,
      r: Math.random() * 1.6 + 0.4,
      alpha: Math.random() * 0.5 + 0.15,
      alphaDir: Math.random() > 0.5 ? 1 : -1,
    })
  }
  particles.value = arr
}

function animateParticles() {
  const c = canvasRef.value
  if (!c) return
  const ctx = c.getContext('2d')
  if (!ctx) return
  const w = c.width, h = c.height
  ctx.clearRect(0, 0, w, h)
  for (const p of particles.value) {
    p.x += p.vx; p.y += p.vy
    if (p.x < 0) p.x = w
    if (p.x > w) p.x = 0
    if (p.y < 0) p.y = h
    if (p.y > h) p.y = 0
    p.alpha += 0.003 * p.alphaDir
    if (p.alpha >= 0.55) p.alphaDir = -1
    if (p.alpha <= 0.12) p.alphaDir = 1
    ctx.beginPath()
    ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2)
    ctx.fillStyle = `rgba(160,160,200,${p.alpha.toFixed(2)})`
    ctx.fill()
  }
  // 粒子间连线
  for (let i = 0; i < particles.value.length; i++) {
    for (let j = i + 1; j < particles.value.length; j++) {
      const a = particles.value[i]!, b = particles.value[j]!
      const dx = a.x - b.x, dy = a.y - b.y
      const dist = Math.sqrt(dx * dx + dy * dy)
      if (dist < 70) {
        ctx.beginPath()
        ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y)
        ctx.strokeStyle = `rgba(130,130,180,${(0.05 * (1 - dist / 70)).toFixed(3)})`
        ctx.lineWidth = 0.4
        ctx.stroke()
      }
    }
  }
  animFrame.value = requestAnimationFrame(animateParticles)
}

const resizeObserver = ref<ResizeObserver | null>(null)
let resizeRafId = 0

function resizeCanvas() {
  if (resizeRafId) return // 已排队，跳过
  resizeRafId = requestAnimationFrame(() => {
    resizeRafId = 0
    const c = canvasRef.value
    if (!c) return
    const parent = c.parentElement
    if (!parent) return
    const w = parent.clientWidth
    const h = parent.clientHeight
    if (c.width === w && c.height === h) return // 尺寸没变，跳过
    c.width = w
    c.height = h
    // 只在粒子未初始化或数量不匹配时重新初始化
    if (particles.value.length === 0) {
      initParticles(w, h)
    }
  })
}

// ===== 搜索 =====
const filteredNodeIds = computed(() => {
  const q = searchQuery.value.trim().toLowerCase()
  if (!q) return null
  return new Set(
    nodes.value.filter(n => n.name.toLowerCase().includes(q) || n.type.toLowerCase().includes(q))
      .map(n => n.id),
  )
})

const highlightedEdges = computed(() => {
  if (!filteredNodeIds.value) return null
  return new Set(
    edges.value.filter(e => filteredNodeIds.value!.has(e.source) || filteredNodeIds.value!.has(e.target))
      .map(e => `${e.source}|${e.target}`),
  )
})

const nodeCounts = computed(() => ({
  total: nodes.value.length,
  edges: edges.value.length,
  facts: facts.value.length,
}))

function toggleSearch() {
  showSearch.value = !showSearch.value
  if (!showSearch.value) searchQuery.value = ''
  else nextTick(() => {
    const el = document.querySelector('.search-input') as HTMLInputElement
    if (el) el.focus()
  })
}

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
  habit: '#06b6d4',
  interest: '#ec4899',
  project: '#6366f1',
  skill: '#22c55e',
}

// 已知有自定义渐变的类型
const KNOWN_GRAD_TYPES = new Set(Object.keys(COLOR))

function nodeColor(type: string): string {
  return COLOR[type] || '#7c7c90'
}

function nodeRadius(type: string): number {
  return type === 'User' ? 28 : 18
}

function nodeFill(type: string): string {
  return KNOWN_GRAD_TYPES.has(type) ? `url(#grad-${type})` : 'url(#grad-__default__)'
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
    habit: '习惯',
    interest: '兴趣',
    project: '项目',
    skill: '技能',
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

function edgePath(edge: LayoutEdge): string {
  const s = nodes.value.find(n => n.id === edge.source)
  const t = nodes.value.find(n => n.id === edge.target)
  if (!s || !t) return ''
  const dx = t.x - s.x
  const dy = t.y - s.y
  const len = Math.sqrt(dx * dx + dy * dy) || 1
  // 基于边标识的曲率变化，避免同向边重叠
  const hash = (edge.source + edge.target).split('').reduce((a, c) => a + c.charCodeAt(0), 0)
  const sign = hash % 2 === 0 ? 1 : -1
  const mag = 0.06 + (hash % 7) * 0.02  // 0.06 ~ 0.18
  const offset = Math.min(len * mag, 32) * sign
  const cx = ((s.x + t.x) / 2) - (dy / len) * offset
  const cy = ((s.y + t.y) / 2) + (dx / len) * offset
  return `M${s.x},${s.y} Q${cx},${cy} ${t.x},${t.y}`
}

function truncateName(name: string, maxLen = 10): string {
  return name.length > maxLen ? name.slice(0, maxLen) + '…' : name
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
      const r = 300 + Math.random() * 350
      node.x = cx + Math.cos(angle) * r
      node.y = cy + Math.sin(angle) * r
    }
    node.vx = 0
    node.vy = 0
  }

  const kRep = 18000
  const kAtt = 0.0025
  const restLen = 340
  const damping = 0.5

  for (let iter = 0; iter < 200; iter++) {
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

function autoFit() {
  if (nodes.value.length === 0) return
  const pad = 150
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity
  for (const n of nodes.value) {
    const r = nodeRadius(n.type)
    if (n.x - r < minX) minX = n.x - r
    if (n.y - r < minY) minY = n.y - r
    if (n.x + r > maxX) maxX = n.x + r
    if (n.y + r > maxY) maxY = n.y + r
  }
  svgWidth.value = Math.max(800, maxX - minX + pad * 2)
  svgHeight.value = Math.max(600, maxY - minY + pad * 2)
  // 居中偏移
  tx.value = -minX + pad
  ty.value = -minY + pad
  scale.value = 1
}

function onMouseDown(e: MouseEvent) {
  const target = e.target as SVGElement
  const circle = target.closest('circle[data-id]')
  if (circle) {
    const nodeId = circle.getAttribute('data-id')!
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
      autoFit()
    }
  } catch {
    empty.value = true
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  await refresh()
  await nextTick()
  resizeCanvas()
  initParticles(canvasRef.value?.width || 800, canvasRef.value?.height || 600)
  animateParticles()
  // ResizeObserver 监听容器大小变化（sidebar 展开/收起等）
  const container = canvasRef.value?.parentElement
  if (container) {
    resizeObserver.value = new ResizeObserver(() => {
      resizeCanvas()
    })
    resizeObserver.value.observe(container)
  }
  setTimeout(() => { showUI.value = true }, 400)
})

function onKeyDown(e: KeyboardEvent) {
  if (e.key === 'Escape') {
    if (showSearch.value && searchQuery.value) { searchQuery.value = ''; return }
    closePanel()
  }
  if ((e.ctrlKey || e.metaKey) && e.key === 'f') { e.preventDefault(); toggleSearch() }
}

onMounted(() => document.addEventListener('keydown', onKeyDown))
onUnmounted(() => {
  document.removeEventListener('keydown', onKeyDown)
  cancelAnimationFrame(animFrame.value)
  if (resizeRafId) cancelAnimationFrame(resizeRafId)
  resizeObserver.value?.disconnect()
})
</script>

<template>
  <div class="graph-container">
    <!-- 粒子背景 -->
    <canvas ref="canvasRef" class="particle-canvas" />

    <!-- 搜索栏 -->
    <Transition name="search-fade">
      <div v-if="showSearch" class="search-bar">
        <svg class="search-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/></svg>
        <input
          v-model="searchQuery"
          class="search-input"
          placeholder="搜索节点…"
          @keydown.esc.stop="searchQuery = ''"
        />
        <span v-if="filteredNodeIds" class="search-count">{{ filteredNodeIds.size }} 个匹配</span>
        <button class="search-close" @click="toggleSearch">✕</button>
      </div>
    </Transition>

    <!-- 统计徽章 -->
    <Transition name="search-fade">
      <div v-if="showUI && !loading && !empty" class="stats-badge">
        <span class="stat-item" style="--c:#f59e0b">● {{ nodeCounts.total }} 节点</span>
        <span class="stat-item" style="--c:#a78bfa">— {{ nodeCounts.edges }} 边</span>
      </div>
    </Transition>

    <!-- 加载状态 -->
    <div v-if="loading" class="graph-status">
      <div class="loader-ring"><div class="loader-ring-inner" /></div>
      <span class="loading-text">加载图谱</span>
    </div>

    <!-- 空状态 -->
    <div v-else-if="empty" class="graph-status">
      <div class="empty-icon">
        <svg width="80" height="80" viewBox="0 0 80 80" fill="none">
          <circle cx="40" cy="40" r="36" stroke="#1e1e32" stroke-width="1" stroke-dasharray="8 5"/>
          <circle cx="40" cy="40" r="18" stroke="#1a1a2e" stroke-width="0.8"/>
          <circle cx="26" cy="28" r="7" fill="none" stroke="#2a2a42" stroke-width="0.8"/>
          <circle cx="54" cy="32" r="5" fill="none" stroke="#2a2a42" stroke-width="0.8"/>
          <circle cx="44" cy="54" r="6" fill="none" stroke="#2a2a42" stroke-width="0.8"/>
          <line x1="31" y1="31" x2="49" y2="34" stroke="#222240" stroke-width="0.7"/>
          <line x1="52" y1="36" x2="47" y2="49" stroke="#222240" stroke-width="0.7"/>
          <line x1="23" y1="32" x2="40" y2="50" stroke="#222240" stroke-width="0.7"/>
        </svg>
      </div>
      <span class="empty-text">暂无图谱数据</span>
      <span class="empty-hint">开始对话后，AI 会将重要信息存入图谱</span>
    </div>

    <!-- 图谱画布 -->
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
      <defs>
        <!-- 边线渐变 -->
        <linearGradient id="edge-grad" x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" stop-color="rgba(255,255,255,0.02)"/>
          <stop offset="50%" stop-color="rgba(160,160,200,0.16)"/>
          <stop offset="100%" stop-color="rgba(255,255,255,0.02)"/>
        </linearGradient>
        <linearGradient id="edge-highlight" x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" stop-color="rgba(200,200,255,0.08)"/>
          <stop offset="50%" stop-color="rgba(200,200,255,0.6)"/>
          <stop offset="100%" stop-color="rgba(200,200,255,0.08)"/>
        </linearGradient>
        <!-- 发光滤镜 -->
        <filter id="glow-strong" x="-50%" y="-50%" width="200%" height="200%">
          <feGaussianBlur stdDeviation="4" result="blur"/>
          <feMerge>
            <feMergeNode in="blur"/>
            <feMergeNode in="blur"/>
            <feMergeNode in="SourceGraphic"/>
          </feMerge>
        </filter>
        <filter id="glow-soft" x="-50%" y="-50%" width="200%" height="200%">
          <feGaussianBlur stdDeviation="2.5" result="blur"/>
          <feMerge>
            <feMergeNode in="blur"/>
            <feMergeNode in="SourceGraphic"/>
          </feMerge>
        </filter>

        <!-- 节点渐变色 -->
        <radialGradient id="grad-User" cx="35%" cy="35%"><stop offset="0%" stop-color="#fcd34d"/><stop offset="100%" stop-color="#d97706"/></radialGradient>
        <radialGradient id="grad-preference" cx="35%" cy="35%"><stop offset="0%" stop-color="#c4b5fd"/><stop offset="100%" stop-color="#7c3aed"/></radialGradient>
        <radialGradient id="grad-fact" cx="35%" cy="35%"><stop offset="0%" stop-color="#6ee7b7"/><stop offset="100%" stop-color="#059669"/></radialGradient>
        <radialGradient id="grad-event" cx="35%" cy="35%"><stop offset="0%" stop-color="#93c5fd"/><stop offset="100%" stop-color="#2563eb"/></radialGradient>
        <radialGradient id="grad-plan" cx="35%" cy="35%"><stop offset="0%" stop-color="#fca5a5"/><stop offset="100%" stop-color="#dc2626"/></radialGradient>
        <radialGradient id="grad-topic" cx="35%" cy="35%"><stop offset="0%" stop-color="#fdba74"/><stop offset="100%" stop-color="#ea580c"/></radialGradient>
        <radialGradient id="grad-todo" cx="35%" cy="35%"><stop offset="0%" stop-color="#fde68a"/><stop offset="100%" stop-color="#ca8a04"/></radialGradient>
        <radialGradient id="grad-conflict" cx="35%" cy="35%"><stop offset="0%" stop-color="#f87171"/><stop offset="100%" stop-color="#b91c1c"/></radialGradient>
        <radialGradient id="grad-pending" cx="35%" cy="35%"><stop offset="0%" stop-color="#9ca3af"/><stop offset="100%" stop-color="#4b5563"/></radialGradient>
        <radialGradient id="grad-habit" cx="35%" cy="35%"><stop offset="0%" stop-color="#67e8f9"/><stop offset="100%" stop-color="#0891b2"/></radialGradient>
        <radialGradient id="grad-interest" cx="35%" cy="35%"><stop offset="0%" stop-color="#f9a8d4"/><stop offset="100%" stop-color="#db2777"/></radialGradient>
        <radialGradient id="grad-project" cx="35%" cy="35%"><stop offset="0%" stop-color="#a5b4fc"/><stop offset="100%" stop-color="#4f46e5"/></radialGradient>
        <radialGradient id="grad-skill" cx="35%" cy="35%"><stop offset="0%" stop-color="#86efac"/><stop offset="100%" stop-color="#16a34a"/></radialGradient>
        <radialGradient id="grad-__default__" cx="35%" cy="35%"><stop offset="0%" stop-color="#a1a1aa"/><stop offset="100%" stop-color="#52525b"/></radialGradient>
      </defs>

      <g :transform="`translate(${tx}, ${ty}) scale(${scale})`">
        <!-- 边线 -->
        <path
          v-for="edge in edges"
          :key="`${edge.source}-${edge.target}`"
          :d="edgePath(edge)"
          class="graph-edge"
          :class="{
            'edge-highlight': highlightedEdges?.has(`${edge.source}|${edge.target}`),
            'edge-dim': filteredNodeIds && !highlightedEdges?.has(`${edge.source}|${edge.target}`),
          }"
          :stroke="highlightedEdges?.has(`${edge.source}|${edge.target}`) ? 'url(#edge-highlight)' : 'url(#edge-grad)'"
        />

        <!-- 边标签 -->
        <text
          v-for="edge in edges"
          :key="`label-${edge.source}-${edge.target}`"
          :x="edgeLabelPos(edge).x"
          :y="edgeLabelPos(edge).y"
          class="edge-label"
        >{{ edge.rel_type }}</text>

        <!-- 节点 -->
        <g
          v-for="node in nodes"
          :key="node.id"
          class="node-group"
          :class="{
            'node-selected': selectedId === node.id,
            'node-user': node.type === 'User',
            'node-highlight': filteredNodeIds?.has(node.id),
            'node-dim': filteredNodeIds && !filteredNodeIds.has(node.id),
          }"
          @click.stop="onNodeClick(node)"
          @mouseenter="hoveredNodeId = node.id"
          @mouseleave="hoveredNodeId = null"
        >
          <!-- 外发光圈（选中时） -->
          <circle
            :cx="node.x"
            :cy="node.y"
            :r="nodeRadius(node.type) + 8"
            class="node-aura"
          />
          <!-- 主圆 -->
          <circle
            :cx="node.x"
            :cy="node.y"
            :r="nodeRadius(node.type)"
            :fill="nodeFill(node.type)"
            :data-id="node.id"
            class="node-circle"
          />
          <!-- 高光 -->
          <circle
            :cx="node.x - nodeRadius(node.type) * 0.25"
            :cy="node.y - nodeRadius(node.type) * 0.3"
            :r="nodeRadius(node.type) * 0.35"
            fill="rgba(255,255,255,0.15)"
            class="node-shine"
          />
          <!-- 标签 -->
          <text
            :x="node.x"
            :y="node.y + nodeRadius(node.type) + 14"
            class="node-label"
            text-anchor="middle"
          >{{ node.type === 'User' ? '🧠' : truncateName(node.name) }}</text>
        </g>
      </g>
    </svg>

    <!-- 底部提示 -->
    <Transition name="search-fade">
      <div v-if="showUI && !loading && !empty" class="graph-hint">
        🖱 滚轮缩放 · 拖拽平移 · 点击节点 · ⌘F 搜索 · Esc 关闭
      </div>
    </Transition>

    <!-- 详情面板 -->
    <Transition name="panel-slide">
      <aside v-if="selectedNode" class="detail-panel">
        <button class="panel-close" @click="closePanel">
          <svg width="16" height="16" viewBox="0 0 16 16"><path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>
        </button>

        <div class="panel-header">
          <div class="panel-dot-wrap">
            <div class="panel-dot" :style="{ background: nodeColor(selectedNode.type) }" />
            <div class="panel-dot-glow" :style="{ background: nodeColor(selectedNode.type) }" />
          </div>
          <div class="panel-titles">
            <span class="panel-name">{{ selectedNode.name }}</span>
            <span class="panel-type">{{ typeLabel(selectedNode.type) }}</span>
          </div>
        </div>

        <div class="panel-divider" />

        <div v-if="nodeFacts.length > 0" class="panel-section">
          <div class="panel-section-title">
            <svg width="12" height="12" viewBox="0 0 12 12"><circle cx="6" cy="6" r="4" fill="currentColor" opacity="0.6"/></svg>
            关联事实
          </div>
          <div v-for="(f, i) in nodeFacts" :key="i" class="panel-fact">
            <span class="fact-type-tag" :style="{ color: nodeColor(f.type), background: nodeColor(f.type) + '18' }">
              {{ typeLabel(f.type) }}
            </span>
            <span class="fact-content">{{ f.content }}</span>
          </div>
        </div>

        <div v-if="nodeEdges.length > 0" class="panel-section">
          <div class="panel-section-title">
            <svg width="12" height="12" viewBox="0 0 12 12"><line x1="2" y1="6" x2="10" y2="6" stroke="currentColor" stroke-width="1.5" opacity="0.5"/></svg>
            关联关系
          </div>
          <div v-for="(e, i) in nodeEdges" :key="i" class="panel-rel">
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
          <span>🔍</span>
          <span>暂无关联信息</span>
        </div>
      </aside>
    </Transition>
  </div>
</template>

<style scoped>
/* ===== 容器与背景 ===== */
.graph-container {
  width: 100%;
  height: 100%;
  position: relative;
  background: #080812;
  overflow: hidden;
  contain: layout style paint;
}

/* 粒子画布 */
.particle-canvas {
  position: absolute;
  inset: 0;
  z-index: 0;
  pointer-events: none;
}

/* ===== 搜索栏 ===== */
.search-bar {
  position: absolute;
  top: 14px;
  left: 50%;
  transform: translateX(-50%);
  display: flex;
  align-items: center;
  gap: 8px;
  background: rgba(16,16,28,0.9);
  backdrop-filter: blur(16px);
  border: 1px solid rgba(255,255,255,0.08);
  border-radius: 12px;
  padding: 8px 14px;
  z-index: 15;
  min-width: 320px;
  box-shadow: 0 8px 32px rgba(0,0,0,0.5);
}

.search-icon {
  color: #5a5a72;
  flex-shrink: 0;
}

.search-input {
  flex: 1;
  background: transparent;
  border: none;
  color: #d0d0e0;
  font-size: 0.82rem;
  outline: none;
  font-family: inherit;
}

.search-input::placeholder {
  color: #3a3a52;
}

.search-count {
  font-size: 0.68rem;
  color: #5a5a72;
  white-space: nowrap;
  flex-shrink: 0;
}

.search-close {
  background: none;
  border: none;
  color: #5a5a72;
  cursor: pointer;
  font-size: 0.8rem;
  padding: 2px 4px;
  border-radius: 4px;
  transition: color 0.2s;
}

.search-close:hover {
  color: #c0c0d0;
}

.search-fade-enter-active,
.search-fade-leave-active {
  transition: opacity 0.25s ease, transform 0.25s ease;
}
.search-fade-enter-from,
.search-fade-leave-to {
  opacity: 0;
  transform: translateX(-50%) translateY(-6px);
}

/* ===== 统计徽章 ===== */
.stats-badge {
  position: absolute;
  top: 14px;
  right: 18px;
  display: flex;
  gap: 14px;
  font-size: 0.7rem;
  z-index: 5;
  padding: 6px 12px;
  background: rgba(16,16,28,0.7);
  backdrop-filter: blur(10px);
  border: 1px solid rgba(255,255,255,0.04);
  border-radius: 20px;
}

.stat-item {
  color: #5a5a72;
  display: flex;
  align-items: center;
  gap: 4px;
}

.stat-item::before {
  content: '';
  display: inline-block;
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--c, #5a5a72);
}

/* ===== 加载 / 空状态 ===== */
.graph-status {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 1rem;
  height: 100%;
  position: relative;
  z-index: 1;
}

.loader-ring {
  width: 40px;
  height: 40px;
  border-radius: 50%;
  background: conic-gradient(from 0deg, transparent, #6366f1, #a78bfa, transparent);
  animation: ring-spin 1.2s linear infinite;
  display: flex;
  align-items: center;
  justify-content: center;
}

.loader-ring-inner {
  width: 28px;
  height: 28px;
  border-radius: 50%;
  background: #0c0c18;
}

@keyframes ring-spin {
  to { transform: rotate(360deg); }
}

.loading-text {
  color: #4a4a60;
  font-size: 0.85rem;
  letter-spacing: 0.06em;
}

.empty-icon {
  opacity: 0.35;
  margin-bottom: 0.25rem;
}

.empty-text {
  color: #5a5a6e;
  font-size: 1rem;
  font-weight: 500;
}

.empty-hint {
  color: #35354a;
  font-size: 0.78rem;
}

/* ===== SVG 画布 ===== */
.graph-svg {
  width: 100%;
  height: 100%;
  cursor: grab;
  position: relative;
  z-index: 1;
}

.graph-svg:active {
  cursor: grabbing;
}

/* ===== 边线 ===== */
.graph-edge {
  fill: none;
  stroke: url(#edge-grad);
  stroke-width: 0.7;
  transition: stroke 0.4s, stroke-width 0.4s, opacity 0.4s;
}

.graph-edge.edge-highlight {
  stroke: url(#edge-highlight);
  stroke-width: 1.4;
}

.graph-edge.edge-dim {
  opacity: 0.10;
}

/* ===== 边标签 ===== */
.edge-label {
  fill: #36364a;
  font-size: 8px;
  font-weight: 500;
  text-anchor: middle;
  pointer-events: none;
  user-select: none;
  letter-spacing: 0.03em;
  transition: fill 0.4s, opacity 0.4s;
}

/* ===== 节点 ===== */
.node-group {
  cursor: pointer;
}

.node-group:hover .node-circle {
  filter: url(#glow-soft);
}

.node-group:hover .node-shine {
  opacity: 0.4;
}

.node-selected .node-circle {
  filter: url(#glow-strong);
  stroke: rgba(255,255,255,0.6);
  stroke-width: 2;
}

.node-highlight .node-circle {
  filter: url(#glow-strong);
  stroke: rgba(255,255,255,0.5);
  stroke-width: 2.5;
}

.node-dim {
  opacity: 0.22;
  pointer-events: none;
}

.node-user .node-circle {
  animation: pulse-user 3.5s ease-in-out infinite;
}

@keyframes pulse-user {
  0%, 100% { filter: url(#glow-soft); }
  50% { filter: url(#glow-strong); }
}

.node-circle {
  transition: filter 0.3s, stroke 0.3s, stroke-width 0.3s, opacity 0.3s;
  stroke: rgba(0,0,0,0.2);
  stroke-width: 1;
}

.node-shine {
  pointer-events: none;
  opacity: 0.18;
  transition: opacity 0.3s;
}

.node-aura {
  fill: none;
  stroke: rgba(255,255,255,0.06);
  stroke-width: 1;
  opacity: 0;
  transition: opacity 0.35s, stroke 0.35s;
}

.node-selected .node-aura {
  opacity: 1;
  stroke: rgba(255,255,255,0.18);
}

.node-highlight .node-aura {
  opacity: 0.7;
  stroke: rgba(255,255,255,0.15);
}

.node-label {
  fill: #c8c8d8;
  font-size: 10px;
  font-weight: 500;
  pointer-events: none;
  user-select: none;
  text-shadow: 0 1px 4px rgba(0,0,0,0.7);
  letter-spacing: 0.02em;
  transition: fill 0.3s, opacity 0.3s;
}

/* ===== 底部提示 ===== */
.graph-hint {
  position: absolute;
  bottom: 14px;
  left: 50%;
  transform: translateX(-50%);
  font-size: 0.68rem;
  color: #2a2a3e;
  pointer-events: none;
  z-index: 2;
  letter-spacing: 0.05em;
  background: rgba(8,8,16,0.7);
  padding: 4px 14px;
  border-radius: 20px;
  border: 1px solid rgba(255,255,255,0.02);
}

/* ===== 详情面板 ===== */
.detail-panel {
  position: absolute;
  top: 0;
  right: 0;
  width: 300px;
  height: 100%;
  background: rgba(14, 14, 24, 0.88);
  backdrop-filter: blur(24px);
  -webkit-backdrop-filter: blur(24px);
  border-left: 1px solid rgba(255, 255, 255, 0.06);
  padding: 1.5rem;
  overflow-y: auto;
  z-index: 10;
  box-shadow: -8px 0 32px rgba(0,0,0,0.4);
}

/* 面板入场动画 */
.panel-slide-enter-active {
  transition: all 0.35s cubic-bezier(0.16, 1, 0.3, 1);
}
.panel-slide-leave-active {
  transition: all 0.25s ease-in;
}
.panel-slide-enter-from {
  transform: translateX(100%);
  opacity: 0;
}
.panel-slide-leave-to {
  transform: translateX(60%);
  opacity: 0;
}

.panel-close {
  position: absolute;
  top: 1rem;
  right: 1rem;
  width: 28px;
  height: 28px;
  border: 1px solid rgba(255,255,255,0.06);
  background: rgba(255,255,255,0.03);
  color: #6b6b80;
  border-radius: 8px;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.2s;
}

.panel-close:hover {
  background: rgba(255,255,255,0.08);
  color: #d0d0d8;
  border-color: rgba(255,255,255,0.12);
}

/* ===== 面板头部 ===== */
.panel-header {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  margin-bottom: 1rem;
  padding-right: 2rem;
}

.panel-dot-wrap {
  position: relative;
  flex-shrink: 0;
  width: 14px;
  height: 14px;
}

.panel-dot {
  width: 12px;
  height: 12px;
  border-radius: 50%;
  position: absolute;
  top: 1px;
  left: 1px;
  z-index: 1;
}

.panel-dot-glow {
  width: 14px;
  height: 14px;
  border-radius: 50%;
  position: absolute;
  top: 0;
  left: 0;
  filter: blur(6px);
  opacity: 0.5;
}

.panel-titles {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.panel-name {
  font-size: 1rem;
  font-weight: 600;
  color: #e0e0ec;
  line-height: 1.2;
}

.panel-type {
  font-size: 0.7rem;
  color: #5a5a72;
  letter-spacing: 0.03em;
}

.panel-divider {
  height: 1px;
  background: linear-gradient(90deg, rgba(255,255,255,0.08) 0%, rgba(255,255,255,0.01) 100%);
  margin-bottom: 1.25rem;
}

/* ===== 面板区块 ===== */
.panel-section {
  margin-bottom: 1.25rem;
}

.panel-section-title {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  font-size: 0.68rem;
  font-weight: 600;
  color: #4a4a5e;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  margin-bottom: 0.5rem;
}

.panel-fact {
  font-size: 0.8rem;
  color: #9a9ab0;
  line-height: 1.55;
  padding: 0.5rem 0.6rem;
  margin-bottom: 0.25rem;
  background: rgba(255,255,255,0.015);
  border-radius: 8px;
  border: 1px solid rgba(255,255,255,0.03);
  display: flex;
  gap: 0.5rem;
  align-items: flex-start;
  transition: background 0.2s;
}

.panel-fact:hover {
  background: rgba(255,255,255,0.03);
}

.fact-type-tag {
  font-size: 0.62rem;
  font-weight: 600;
  flex-shrink: 0;
  padding: 0.08rem 0.35rem;
  border-radius: 4px;
  margin-top: 1px;
}

.fact-content {
  flex: 1;
}

.panel-rel {
  font-size: 0.8rem;
  color: #9a9ab0;
  padding: 0.4rem 0.6rem;
  margin-bottom: 0.25rem;
  background: rgba(255,255,255,0.015);
  border-radius: 8px;
  border: 1px solid rgba(255,255,255,0.03);
  display: flex;
  align-items: center;
  gap: 0.5rem;
  transition: background 0.2s;
}

.panel-rel:hover {
  background: rgba(255,255,255,0.03);
}

.rel-dir {
  color: #4a4a5e;
  font-size: 0.85rem;
  flex-shrink: 0;
  font-weight: 300;
}

.rel-type {
  color: #a78bfa;
  font-size: 0.7rem;
  font-weight: 500;
  background: rgba(139, 92, 246, 0.12);
  padding: 0.1rem 0.4rem;
  border-radius: 5px;
  flex-shrink: 0;
}

.rel-target {
  color: #c8c8dc;
  font-weight: 500;
}

/* ===== 面板空状态 ===== */
.panel-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 0.5rem;
  color: #3a3a4e;
  font-size: 0.8rem;
  text-align: center;
  padding: 2.5rem 0;
}

/* ===== 滚动条 ===== */
.detail-panel::-webkit-scrollbar {
  width: 3px;
}
.detail-panel::-webkit-scrollbar-track {
  background: transparent;
}
.detail-panel::-webkit-scrollbar-thumb {
  background: rgba(255,255,255,0.05);
  border-radius: 3px;
}
</style>
