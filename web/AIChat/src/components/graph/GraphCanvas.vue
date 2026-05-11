<script setup lang="ts">
import { ref, onMounted, onUnmounted, computed, watch, nextTick } from 'vue'
import { fetchGraph, type GraphNode, type GraphEdge, type GraphFact } from '@/api/graph'

interface GraphConfig {
  centers: Array<{
    type: string
    name: string
    icon: string
    label: string
    is_root: boolean
    render: {
      radius: number
      pulse: string
      color: string
      truncate_name: boolean
    }
  }>
  entity_types: Record<string, {
    icon?: string
    label_prefix?: string
    render?: {
      radius?: number
      pulse?: string
      color?: string
      truncate_name?: boolean
    }
  }>
  default_render: {
    radius: number
    pulse: string
    color: string
    truncate_name: boolean
    max_name_len: number
  }
  root_node_ids: string[]
  injection: {
    header: string
    section_template: string
    unknown_section_icon: string
    unknown_section_label: string
    item_template: string
    relation_label: string
    source_label: string
    no_result: string
  }
}

const graphConfig = ref<GraphConfig | null>(null)

async function fetchConfig() {
  try {
    const resp = await fetch('/api/graph/config')
    graphConfig.value = await resp.json()
  } catch {
    graphConfig.value = {
      centers: [],
      entity_types: {},
      default_render: { radius: 18, pulse: 'none', color: '#7c7c90', truncate_name: true, max_name_len: 12 },
      root_node_ids: [],
      injection: { header: '', section_template: '', unknown_section_icon: '\uD83D\uDCCC', unknown_section_label: '其他', item_template: '', relation_label: '', source_label: '', no_result: '' },
    }
  }
}

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
  hue: number
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
const svgRef = ref<SVGSVGElement | null>(null)
const particles = ref<Particle[]>([])
const PARTICLE_COUNT = 100
const mouseCanvas = ref({ x: -999, y: -999 })

function initParticles(w: number, h: number) {
  const baseHues = [210, 230, 250, 270, 195, 225, 245, 260]
  const arr: Particle[] = []
  for (let i = 0; i < PARTICLE_COUNT; i++) {
    const baseHue = baseHues[i % baseHues.length]!
    arr.push({
      x: Math.random() * w, y: Math.random() * h,
      vx: (Math.random() - 0.5) * 0.2, vy: (Math.random() - 0.5) * 0.2,
      r: Math.random() * 2.2 + 0.4,
      alpha: Math.random() * 0.6 + 0.15,
      alphaDir: Math.random() > 0.5 ? 1 : -1,
      hue: baseHue + (Math.random() - 0.5) * 30,
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
  const mx = mouseCanvas.value.x
  const my = mouseCanvas.value.y

  for (const p of particles.value) {
    const ddx = mx - p.x, ddy = my - p.y
    const dm = Math.sqrt(ddx * ddx + ddy * ddy)
    if (dm < 200 && dm > 1) {
      const force = 0.02 / Math.max(dm * 0.04, 1)
      p.vx += (ddx / dm) * force
      p.vy += (ddy / dm) * force
    }
    p.x += p.vx; p.y += p.vy
    p.vx *= 0.998; p.vy *= 0.998
    if (p.x < -10) p.x = w + 10
    if (p.x > w + 10) p.x = -10
    if (p.y < -10) p.y = h + 10
    if (p.y > h + 10) p.y = -10
    p.alpha += 0.0025 * p.alphaDir
    if (p.alpha >= 0.6) p.alphaDir = -1
    if (p.alpha <= 0.1) p.alphaDir = 1
    ctx.beginPath()
    ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2)
    ctx.fillStyle = `hsla(${p.hue}, 65%, 82%, ${p.alpha.toFixed(2)})`
    ctx.fill()
  }
  for (let i = 0; i < particles.value.length; i++) {
    for (let j = i + 1; j < particles.value.length; j++) {
      const a = particles.value[i]!, b = particles.value[j]!
      const dx = a.x - b.x, dy = a.y - b.y
      const dist = Math.sqrt(dx * dx + dy * dy)
      if (dist < 85) {
        const ratio = 1 - dist / 85
        const avgHue = (a.hue + b.hue) / 2
        ctx.beginPath()
        ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y)
        ctx.strokeStyle = `hsla(${avgHue}, 45%, 78%, ${(0.15 * ratio * ratio).toFixed(3)})`
        ctx.lineWidth = 0.5 * ratio
        ctx.stroke()
      }
    }
  }
  animFrame.value = requestAnimationFrame(animateParticles)
}

const resizeObserver = ref<ResizeObserver | null>(null)
let resizeRafId = 0

function onSvgMouseMove(e: MouseEvent) {
  const svg = svgRef.value
  if (!svg) return
  const rect = svg.getBoundingClientRect()
  mouseCanvas.value = {
    x: e.clientX - rect.left,
    y: e.clientY - rect.top,
  }
}

function onSvgMouseLeave() {
  mouseCanvas.value = { x: -999, y: -999 }
}

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
    if (particles.value.length === 0) {
      initParticles(w, h)
    } else {
      // 窗口resize时重新分布粒子到全屏
      for (const p of particles.value) {
        p.x = Math.random() * w
        p.y = Math.random() * h
      }
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
  const c = graphConfig.value?.centers.find((c: { type: string }) => c.type === type)
  if (c) return c.render.radius
  return graphConfig.value?.default_render.radius ?? 18
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

function nodeIcon(type: string): string {
  const m: Record<string, string> = {
    User: '\u2299',
    preference: '\u2661',
    fact: '\u25CF',
    event: '\u25C6',
    plan: '\u25B6',
    topic: '\u2726',
    todo: '\u2610',
    conflict: '\u26A1',
    pending: '?',
    habit: '\u21BB',
    interest: '\u2605',
    project: '\u2B21',
    skill: '\u25C6',
  }
  return m[type] || '\u25CF'
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
  const hash = (edge.source + edge.target + edge.rel_type).split('').reduce((a, c) => a + c.charCodeAt(0), 0)
  const sign = hash % 2 === 0 ? 1 : -1
  const stagger = 12 + (hash % 5) * 9
  const offset = stagger * sign
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

function isRootNode(node: { type: string, id: string }): boolean {
  return graphConfig.value?.root_node_ids?.includes(node.id) ?? false
}

function getPulseType(node: { type: string }): string {
  const c = graphConfig.value?.centers.find((c: { type: string }) => c.type === node.type)
  return c?.render?.pulse ?? 'none'
}

function shouldTruncate(type: string): boolean {
  const c = graphConfig.value?.centers.find((c: { type: string }) => c.type === type)
  if (c) return c.render.truncate_name ?? false
  return graphConfig.value?.default_render.truncate_name ?? true
}

function simulate() {
  const cx = svgWidth.value / 2
  const cy = svgHeight.value / 2

  for (const node of nodes.value) {
    if (node.fixed) continue
    const angle = Math.random() * 2 * Math.PI
    const r = 300 + Math.random() * 350
    node.x = cx + Math.cos(angle) * r
    node.y = cy + Math.sin(angle) * r
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
      const rootIds = new Set(graphConfig.value?.root_node_ids ?? [])
      const rootCount = rootIds.size
      let rootIndex = 0
      const r = 120
      nodes.value = data.nodes.map(n => {
        if (rootIds.has(n.id)) {
          const angle = (rootIndex / rootCount) * 2 * Math.PI
          rootIndex++
          return { ...n, x: r * Math.cos(angle), y: r * Math.sin(angle), vx: 0, vy: 0, fixed: true }
        }
        return { ...n, x: (Math.random() - 0.5) * 200, y: (Math.random() - 0.5) * 200, vx: 0, vy: 0, fixed: false }
      })
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
  await fetchConfig()
  await refresh()
  await nextTick()
  resizeCanvas()
  await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))
  animateParticles()
  const container = canvasRef.value?.parentElement
  if (container) {
    resizeObserver.value = new ResizeObserver(() => {
      resizeCanvas()
    })
    resizeObserver.value.observe(container)
  }
  const svg = svgRef.value
  if (svg) {
    svg.addEventListener('mousemove', onSvgMouseMove)
    svg.addEventListener('mouseleave', onSvgMouseLeave)
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
  const svg = svgRef.value
  if (svg) {
    svg.removeEventListener('mousemove', onSvgMouseMove)
    svg.removeEventListener('mouseleave', onSvgMouseLeave)
  }
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

    <!-- 浮动图例 -->
    <Transition name="search-fade">
      <div v-if="showUI && !loading && !empty" class="type-legend">
        <span v-for="(clr, type) in COLOR" :key="type" class="legend-chip" :style="{ '--c': clr }">
          {{ nodeIcon(type) }} {{ typeLabel(type) }}
        </span>
      </div>
    </Transition>

    <!-- 加载状态 -->
    <div v-if="loading" class="graph-status">
      <div class="orbit-loader">
        <div class="orbit-ring o-1" />
        <div class="orbit-ring o-2" />
        <div class="orbit-ring o-3" />
        <div class="orbit-core" />
      </div>
      <span class="loading-text">加载图谱</span>
    </div>

    <!-- 空状态 -->
    <div v-else-if="empty" class="graph-status">
      <div class="empty-illustration">
        <svg width="120" height="120" viewBox="0 0 120 120" fill="none">
          <!-- 外圈 -->
          <circle cx="60" cy="60" r="52" stroke="#1e1e3a" stroke-width="0.8" stroke-dasharray="6 5"/>
          <circle cx="60" cy="60" r="38" stroke="#1a1a34" stroke-width="0.6"/>
          <!-- 装饰弧线 -->
          <path d="M48 24a46 46 0 0 1 32 0" stroke="#222250" stroke-width="0.6" stroke-linecap="round"/>
          <path d="M96 70a46 46 0 0 1-40 26" stroke="#222250" stroke-width="0.6" stroke-linecap="round"/>
          <!-- 节点 -->
          <circle cx="36" cy="42" r="10" fill="none" stroke="#2d2d52" stroke-width="0.9"/>
          <circle cx="36" cy="42" r="3.5" fill="#3a3a60" opacity="0.7"/>
          <circle cx="82" cy="48" r="7.5" fill="none" stroke="#2d2d52" stroke-width="0.9"/>
          <circle cx="82" cy="48" r="2.5" fill="#3a3a60" opacity="0.7"/>
          <circle cx="62" cy="80" r="8.5" fill="none" stroke="#2d2d52" stroke-width="0.9"/>
          <circle cx="62" cy="80" r="3" fill="#3a3a60" opacity="0.7"/>
          <!-- 连线 -->
          <path d="M44 46 Q58 44 76 48" stroke="#222248" stroke-width="0.7"/>
          <path d="M80 53 Q70 66 67 73" stroke="#222248" stroke-width="0.7"/>
          <path d="M32 47 Q46 64 56 74" stroke="#222248" stroke-width="0.7"/>
        </svg>
      </div>
      <span class="empty-text">暂无图谱数据</span>
      <span class="empty-hint">开始对话后，AI 会将重要信息存入图谱</span>
    </div>

    <!-- 图谱画布 -->
    <svg
      ref="svgRef"
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

        <!-- 点阵背景 -->
        <pattern id="dot-grid" width="28" height="28" patternUnits="userSpaceOnUse">
          <circle cx="14" cy="14" r="0.6" fill="rgba(255,255,255,0.06)"/>
        </pattern>
        <pattern id="dot-grid-small" width="7" height="7" patternUnits="userSpaceOnUse">
          <circle cx="3.5" cy="3.5" r="0.3" fill="rgba(255,255,255,0.025)"/>
        </pattern>

        <!-- 搜索边线流动动画 -->
      </defs>

      <!-- 点阵背景层（跟随变换） -->
      <rect x="0" y="0" :width="svgWidth" :height="svgHeight" fill="url(#dot-grid-small)" />
      <rect x="0" y="0" :width="svgWidth" :height="svgHeight" fill="url(#dot-grid)" />

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
          :key="`label-${edge.source}-${edge.target}-${edge.rel_type}`"
          :x="edgeLabelPos(edge).x"
          :y="edgeLabelPos(edge).y"
          class="edge-label"
          :class="{
            'edge-label-dim': filteredNodeIds && !highlightedEdges?.has(`${edge.source}|${edge.target}`),
          }"
        >{{ edge.rel_type }}</text>

        <!-- 节点 -->
        <g
          v-for="node in nodes"
          :key="node.id"
          class="node-group"
          :class="{
            'node-selected': selectedId === node.id,
            'node-user': isRootNode(node),
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
          <!-- 轨道环 -->
          <ellipse
            v-if="!isRootNode(node)"
            :cx="node.x"
            :cy="node.y"
            :rx="nodeRadius(node.type) + 13"
            :ry="nodeRadius(node.type) + 7"
            class="node-orbit"
            :style="{ stroke: nodeColor(node.type) }"
          />
          <!-- 脉冲环 -->
          <circle
            v-if="getPulseType(node) === 'double'"
            :cx="node.x"
            :cy="node.y"
            :r="nodeRadius(node.type) + 10"
            class="node-pulse-ring ring-1"
          />
          <circle
            v-if="getPulseType(node) === 'double'"
            :cx="node.x"
            :cy="node.y"
            :r="nodeRadius(node.type) + 22"
            class="node-pulse-ring ring-2"
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
          <!-- 类型图标 -->
          <text
            :x="node.x"
            :y="node.y + 0.5"
            class="node-icon"
            text-anchor="middle"
            dominant-baseline="central"
          >{{ nodeIcon(node.type) }}</text>
          <!-- 标签 -->
          <text
            :x="node.x"
            :y="node.y + nodeRadius(node.type) + 14"
            class="node-label"
            text-anchor="middle"
          >{{ shouldTruncate(node.type) ? truncateName(node.name) : node.name }}</text>
        </g>
      </g>
    </svg>

    <!-- 底部提示 -->
    <Transition name="search-fade">
      <div v-if="showUI && !loading && !empty" class="graph-hint">
        滚轮缩放 · 拖拽平移 · 点击节点 · Ctrl+F 搜索 · Esc 关闭
      </div>
    </Transition>

    <!-- 详情面板 -->
    <Transition name="panel-slide">
      <aside v-if="selectedNode" class="detail-panel">
        <!-- 顶部装饰线 -->
        <div class="panel-accent" :style="{ background: nodeColor(selectedNode.type) }" />
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
            <span class="panel-type" :style="{ color: nodeColor(selectedNode.type) }">{{ typeLabel(selectedNode.type) }}</span>
          </div>
        </div>

        <div class="panel-divider" />

        <div v-if="nodeFacts.length > 0" class="panel-section">
          <div class="panel-section-title">
            <svg width="13" height="13" viewBox="0 0 13 13"><rect x="2" y="2" width="9" height="9" rx="2" fill="none" stroke="currentColor" stroke-width="1.2" opacity="0.5"/><line x1="5" y1="6.5" x2="7" y2="8.5" stroke="currentColor" stroke-width="1.2" opacity="0.5"/><line x1="7" y1="8.5" x2="10" y2="4.5" stroke="currentColor" stroke-width="1.2" opacity="0.5"/></svg>
            关联事实
          </div>
          <div v-for="(f, i) in nodeFacts" :key="i" class="panel-fact">
            <div class="fact-gutter" :style="{ background: nodeColor(f.type) }" />
            <div class="fact-body">
              <span class="fact-type-tag" :style="{ color: nodeColor(f.type), background: nodeColor(f.type) + '18' }">
                {{ typeLabel(f.type) }}
              </span>
              <span class="fact-content">{{ f.content }}</span>
            </div>
          </div>
        </div>

        <div v-if="nodeEdges.length > 0" class="panel-section">
          <div class="panel-section-title">
            <svg width="13" height="13" viewBox="0 0 13 13"><circle cx="3.5" cy="6.5" r="2.2" fill="none" stroke="currentColor" stroke-width="1.2" opacity="0.5"/><circle cx="9.5" cy="6.5" r="2.2" fill="none" stroke="currentColor" stroke-width="1.2" opacity="0.5"/><line x1="5.7" y1="6.5" x2="7.3" y2="6.5" stroke="currentColor" stroke-width="1.2" opacity="0.5"/></svg>
            关联关系
          </div>
          <div v-for="(e, i) in nodeEdges" :key="i" class="panel-rel">
            <div class="rel-arrow-wrap">
              <svg width="16" height="16" viewBox="0 0 16 16" class="rel-arrow-icon">
                <circle cx="3" cy="8" r="2.5" fill="none" stroke="currentColor" stroke-width="1"/>
                <line x1="5.5" y1="8" x2="9" y2="8" stroke="currentColor" stroke-width="1"/>
                <polygon points="13,8 9.5,5 9.5,11" fill="currentColor"/>
              </svg>
            </div>
            <div class="rel-body">
              <span class="rel-type">{{ e.rel_type }}</span>
              <span class="rel-target">
                {{ e.source === selectedNode.id
                  ? (nodes.find(n => n.id === e.target)?.name || e.target)
                  : (nodes.find(n => n.id === e.source)?.name || e.source)
                }}
              </span>
            </div>
          </div>
        </div>

        <div v-if="nodeFacts.length === 0 && nodeEdges.length === 0" class="panel-empty">
          <svg width="32" height="32" viewBox="0 0 32 32" fill="none" class="panel-empty-icon">
            <circle cx="16" cy="16" r="14" stroke="#2a2a42" stroke-width="0.8"/>
            <path d="M11 16h10M16 11v10" stroke="#2a2a42" stroke-width="1.2" stroke-linecap="round"/>
          </svg>
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

/* 径向暗角叠加 + 四角色彩微光 */
.graph-container::after {
  content: '';
  position: absolute;
  inset: 0;
  pointer-events: none;
  z-index: 0;
  background:
    radial-gradient(ellipse at center, transparent 35%, rgba(4,4,10,0.55) 100%),
    radial-gradient(ellipse at 15% 15%, rgba(99,102,241,0.04) 0%, transparent 50%),
    radial-gradient(ellipse at 85% 85%, rgba(139,92,246,0.03) 0%, transparent 50%),
    radial-gradient(ellipse at 85% 15%, rgba(59,130,246,0.02) 0%, transparent 40%);
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
  background: rgba(14,14,32,0.94);
  backdrop-filter: blur(20px);
  border: 1px solid rgba(255,255,255,0.08);
  border-radius: 14px;
  padding: 9px 14px;
  z-index: 15;
  min-width: 340px;
  box-shadow: 0 8px 32px rgba(0,0,0,0.5), 0 0 0 0 rgba(99,102,241,0);
  transition: border-color 0.35s, box-shadow 0.35s, background 0.35s;
}

.search-bar:focus-within {
  border-color: rgba(139, 92, 246, 0.45);
  box-shadow: 0 8px 32px rgba(0,0,0,0.5), 0 0 24px rgba(99,102,241,0.15), 0 0 0 1px rgba(139,92,246,0.15);
  background: rgba(16,16,36,0.96);
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
  gap: 16px;
  font-size: 0.68rem;
  z-index: 5;
  padding: 7px 14px;
  background: rgba(14,14,32,0.85);
  backdrop-filter: blur(12px);
  border: 1px solid rgba(255,255,255,0.06);
  border-radius: 20px;
  letter-spacing: 0.02em;
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

/* ===== 类型图例 ===== */
.type-legend {
  position: absolute;
  bottom: 48px;
  left: 50%;
  transform: translateX(-50%);
  display: flex;
  flex-wrap: wrap;
  gap: 4px 12px;
  justify-content: center;
  z-index: 5;
  padding: 8px 16px;
  background: rgba(10,10,26,0.85);
  backdrop-filter: blur(16px);
  border: 1px solid rgba(255,255,255,0.06);
  border-radius: 24px;
  max-width: 92%;
  box-shadow: 0 4px 20px rgba(0,0,0,0.4);
}

.legend-chip {
  font-size: 0.6rem;
  color: #7a7a94;
  display: inline-flex;
  align-items: center;
  gap: 3px;
  white-space: nowrap;
  transition: color 0.2s, transform 0.2s;
  cursor: default;
}

.legend-chip::before {
  content: '';
  display: inline-block;
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--c, #7a7a94);
  box-shadow: 0 0 4px var(--c, #7a7a94);
}

.legend-chip:hover {
  color: #d4d4e8;
  transform: translateY(-1px);
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

/* 轨道加载器 */
.orbit-loader {
  position: relative;
  width: 60px;
  height: 60px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.orbit-core {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #a78bfa;
  box-shadow: 0 0 12px rgba(139, 92, 246, 0.5), 0 0 24px rgba(139, 92, 246, 0.25);
  z-index: 1;
}

.orbit-ring {
  position: absolute;
  inset: 0;
  border-radius: 50%;
  border: 1px solid transparent;
  opacity: 0.5;
}

.o-1 {
  border-color: rgba(139, 92, 246, 0.3);
  animation: orbit-spin 2.4s linear infinite;
}

.o-2 {
  inset: 8px;
  border-color: rgba(99, 102, 241, 0.25);
  animation: orbit-spin 1.8s linear infinite reverse;
}

.o-3 {
  inset: 16px;
  border-color: rgba(168, 85, 247, 0.2);
  animation: orbit-spin 3s linear infinite;
}

@keyframes orbit-spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

.loading-text {
  color: #4a4a60;
  font-size: 0.85rem;
  letter-spacing: 0.08em;
  animation: loading-fade 2s ease-in-out infinite;
}

@keyframes loading-fade {
  0%, 100% { opacity: 0.5; }
  50% { opacity: 1; }
}

.empty-illustration {
  opacity: 0.4;
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
  stroke-linecap: round;
  transition: stroke 0.5s, stroke-width 0.5s, opacity 0.5s;
}

.graph-edge.edge-highlight {
  stroke: url(#edge-highlight);
  stroke-width: 1.5;
  stroke-dasharray: 10 6;
  animation: dash-flow 1.2s linear infinite;
  filter: drop-shadow(0 0 3px rgba(180,180,240,0.3));
}

.graph-edge.edge-dim {
  opacity: 0.08;
}

@keyframes dash-flow {
  to { stroke-dashoffset: -32; }
}

@keyframes dash-pulse {
  0%, 100% { stroke-opacity: 0.3; }
  50% { stroke-opacity: 1; }
}

/* ===== 边标签 ===== */
.edge-label {
  fill: #5a5a70;
  font-size: 8px;
  font-weight: 500;
  text-anchor: middle;
  pointer-events: none;
  user-select: none;
  letter-spacing: 0.03em;
  paint-order: stroke;
  stroke: rgba(4, 4, 12, 0.75);
  stroke-width: 3;
  stroke-linecap: round;
  stroke-linejoin: round;
  transition: fill 0.4s, opacity 0.4s;
}

.edge-label-dim {
  opacity: 0.1;
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

.node-icon {
  fill: rgba(255,255,255,0.85);
  font-size: 11px;
  pointer-events: none;
  user-select: none;
  transition: fill 0.3s;
}

.node-user .node-icon {
  font-size: 13px;
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

/* ===== 节点轨道环 ===== */
.node-orbit {
  fill: none;
  stroke-opacity: 0.12;
  stroke-width: 0.8;
  opacity: 0;
  pointer-events: none;
  transition: opacity 0.4s, stroke-opacity 0.4s;
  transform-origin: var(--ox) var(--oy);
  animation: orbit-rotate 8s linear infinite;
}

.node-group:hover .node-orbit,
.node-selected .node-orbit,
.node-highlight .node-orbit {
  opacity: 1;
  stroke-opacity: 0.3;
}

@keyframes orbit-rotate {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

/* User 双层脉冲 */
.node-pulse-ring {
  fill: none;
  stroke: rgba(245, 158, 11, 0.3);
  stroke-width: 1;
  pointer-events: none;
  opacity: 0;
  transform-box: fill-box;
  transform-origin: center;
}

.ring-1 {
  animation: pulse-ring 2.2s ease-out infinite;
}

.ring-2 {
  animation: pulse-ring 2.2s ease-out 0.75s infinite;
}

.node-group:hover .node-pulse-ring,
.node-selected .node-pulse-ring,
.node-highlight .node-pulse-ring {
  opacity: 1;
}

@keyframes pulse-ring {
  0% {
    stroke-opacity: 0.6;
    stroke-width: 1.2;
    transform: scale(1);
  }
  100% {
    stroke-opacity: 0;
    stroke-width: 0.3;
    transform: scale(1.8);
  }
}

.node-label {
  fill: #c8c8d8;
  font-size: 10px;
  font-weight: 500;
  pointer-events: none;
  user-select: none;
  text-shadow: 0 1px 6px rgba(0,0,0,0.8), 0 0 3px rgba(0,0,0,0.5);
  letter-spacing: 0.03em;
  transition: fill 0.3s, opacity 0.3s;
}

.node-group:hover .node-label {
  fill: #f0f0f8;
}

.node-selected .node-label {
  fill: #ffffff;
  font-weight: 600;
}

/* ===== 底部提示 ===== */
.graph-hint {
  position: absolute;
  bottom: 14px;
  left: 50%;
  transform: translateX(-50%);
  font-size: 0.66rem;
  color: #2e2e46;
  pointer-events: none;
  z-index: 2;
  letter-spacing: 0.04em;
  background: rgba(8,8,16,0.65);
  padding: 4px 16px;
  border-radius: 20px;
  border: 1px solid rgba(255,255,255,0.03);
}

/* ===== 详情面板 ===== */
.detail-panel {
  position: absolute;
  top: 0;
  right: 0;
  width: 320px;
  height: 100%;
  background: rgba(10, 10, 26, 0.85);
  backdrop-filter: blur(28px) saturate(1.4);
  -webkit-backdrop-filter: blur(28px) saturate(1.4);
  border-left: 1px solid rgba(255, 255, 255, 0.08);
  padding: 1.5rem;
  overflow-y: auto;
  z-index: 10;
  box-shadow: -16px 0 48px rgba(0,0,0,0.5), inset 1px 0 0 rgba(255,255,255,0.02);
}

/* 顶部装饰线 */
.panel-accent {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 2px;
  opacity: 0.6;
}

/* 面板入场动画 */
.panel-slide-enter-active {
  transition: all 0.4s cubic-bezier(0.16, 1, 0.3, 1);
}
.panel-slide-leave-active {
  transition: all 0.3s cubic-bezier(0.5, 0, 0.75, 0);
}
.panel-slide-enter-from {
  transform: translateX(100%);
  opacity: 0;
  backdrop-filter: blur(0px);
}
.panel-slide-leave-to {
  transform: translateX(80%);
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
  margin-bottom: 0.35rem;
  background: rgba(255,255,255,0.02);
  border-radius: 10px;
  border: 1px solid rgba(255,255,255,0.04);
  display: flex;
  overflow: hidden;
  transition: background 0.2s, transform 0.2s, border-color 0.2s;
  animation: fact-enter 0.4s ease both;
}

.panel-fact:nth-child(2) { animation-delay: 0.05s; }
.panel-fact:nth-child(3) { animation-delay: 0.10s; }
.panel-fact:nth-child(4) { animation-delay: 0.15s; }
.panel-fact:nth-child(5) { animation-delay: 0.20s; }
.panel-fact:nth-child(6) { animation-delay: 0.25s; }

@keyframes fact-enter {
  from { opacity: 0; transform: translateY(12px); }
  to { opacity: 1; transform: translateY(0); }
}

.panel-fact:hover {
  background: rgba(255,255,255,0.05);
  border-color: rgba(255,255,255,0.10);
  transform: translateY(-1px);
  box-shadow: 0 4px 12px rgba(0,0,0,0.3);
}

.fact-gutter {
  width: 3px;
  flex-shrink: 0;
  opacity: 0.4;
  border-radius: 0 2px 2px 0;
  transition: opacity 0.2s;
}

.panel-fact:hover .fact-gutter {
  opacity: 0.8;
}

.fact-body {
  flex: 1;
  padding: 0.5rem 0.6rem;
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
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
  margin-bottom: 0.35rem;
  background: rgba(255,255,255,0.02);
  border-radius: 10px;
  border: 1px solid rgba(255,255,255,0.04);
  display: flex;
  align-items: center;
  gap: 0;
  overflow: hidden;
  transition: background 0.2s, transform 0.2s, border-color 0.2s;
  animation: fact-enter 0.4s ease both;
}

.panel-rel:nth-child(2) { animation-delay: 0.05s; }
.panel-rel:nth-child(3) { animation-delay: 0.10s; }
.panel-rel:nth-child(4) { animation-delay: 0.15s; }
.panel-rel:nth-child(5) { animation-delay: 0.20s; }

.panel-rel:hover {
  background: rgba(255,255,255,0.05);
  border-color: rgba(255,255,255,0.10);
  transform: translateY(-1px);
  box-shadow: 0 4px 12px rgba(0,0,0,0.3);
}

.rel-arrow-wrap {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 0.5rem 0.4rem 0.5rem 0.6rem;
  flex-shrink: 0;
}

.rel-arrow-icon {
  color: #4a4a62;
  opacity: 0.5;
  transition: opacity 0.2s, color 0.2s;
}

.panel-rel:hover .rel-arrow-icon {
  opacity: 0.8;
  color: #8b8baa;
}

.rel-body {
  flex: 1;
  padding: 0.5rem 0.6rem 0.5rem 0;
  display: flex;
  flex-direction: column;
  gap: 0.2rem;
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
  gap: 0.75rem;
  color: #3a3a4e;
  font-size: 0.78rem;
  text-align: center;
  padding: 3rem 0;
}

.panel-empty-icon {
  opacity: 0.25;
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
