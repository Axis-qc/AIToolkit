<script setup lang="ts">
/**
 * LineChart —— 暗色风格 canvas 折线图
 *  - 网格 + 自适应时间轴/数值轴 + 折线 + 渐变填充
 *  - 鼠标悬停：十字准星 + 就近点高亮 + 提示框（HH:MM:SS · 各序列值）
 *  - 滚轮：以鼠标所在时间为中心缩放窗口；拖拽：平移窗口（emit viewport）
 *  - 窗口（t0/t1）由父组件掌控，缩放/平移后通过 viewport 事件交还父组件
 */
import { onMounted, onBeforeUnmount, ref, watch } from 'vue'
import { scaleLinear, ticks } from 'd3'

interface LinePoint {
  ts: number
  [k: string]: number | null
}

interface LineSeries {
  key: string
  label: string
  color: string
  unit: string // '%' 或 'KB/s'
  digits?: number
}

const props = withDefaults(
  defineProps<{
    points: LinePoint[]
    series: LineSeries[]
    t0: number
    t1: number
    yMax?: number
    height?: number
  }>(),
  { yMax: 0, height: 195 },
)

const emit = defineEmits<{ (e: 'viewport', v: { t0: number; t1: number }): void }>()

const wrapRef = ref<HTMLDivElement | null>(null)
const canvasRef = ref<HTMLCanvasElement | null>(null)
const hoverPoint = ref<LinePoint | null>(null)

const MIN_SPAN = 30_000
const MAX_SPAN = 26 * 3600_000
const M = { top: 10, right: 14, bottom: 22, left: 44 }

let drag = { on: false, startX: 0, startT0: 0, startT1: 0 }
let ro: ResizeObserver | undefined

function size() {
  const w = wrapRef.value?.clientWidth ?? 80
  return { w, h: props.height }
}

function clamp(v: number, lo: number, hi: number) {
  return Math.min(hi, Math.max(lo, v))
}

/* ── 格式化 ── */
const pad2 = (n: number) => String(n).padStart(2, '0')

function fmtTime(ts: number) {
  const d = new Date(ts)
  return `${pad2(d.getHours())}:${pad2(d.getMinutes())}:${pad2(d.getSeconds())}`
}

function fmtAxisTime(ts: number, span: number) {
  const d = new Date(ts)
  if (span > 86400_000) return `${d.getMonth() + 1}/${d.getDate()} ${pad2(d.getHours())}:${pad2(d.getMinutes())}`
  return `${pad2(d.getHours())}:${pad2(d.getMinutes())}`
}

function fmtVal(v: number, unit: string, digits: number) {
  if (unit === '%') return v.toFixed(digits) + '%'
  const kb = v
  if (kb >= 1048576) return (kb / 1048576).toFixed(2) + ' GB/s'
  if (kb >= 1024) return (kb / 1024).toFixed(1) + ' MB/s'
  return kb.toFixed(0) + ' KB/s'
}

function fmtTick(v: number, unit: string) {
  if (unit === '%') return v.toFixed(0) + '%'
  if (unit.startsWith('KB')) {
    if (v >= 1048576) return (v / 1048576).toFixed(0) + 'G'
    if (v >= 1024) return (v / 1024).toFixed(0) + 'M'
    return v.toFixed(0) + 'K'
  }
  return v.toFixed(0)
}

const TIME_STEPS_MS = [
  1000, 2000, 5000, 10000, 15000, 30000, 60000, 120000, 300000, 600000,
  900000, 1800000, 3600000, 7200000, 10800000, 21600000, 43200000, 86400000,
]

function timeTicks(t0: number, t1: number): number[] {
  const span = t1 - t0
  let step = TIME_STEPS_MS[TIME_STEPS_MS.length - 1] ?? 86400_000
  for (const s of TIME_STEPS_MS) {
    if (span / s <= 7) {
      step = s
      break
    }
  }
  const out: number[] = []
  let t = Math.ceil(t0 / step) * step
  while (t <= t1 && out.length < 24) {
    out.push(t)
    t += step
  }
  return out
}

function roundRect(c: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, r: number) {
  c.beginPath()
  c.moveTo(x + r, y)
  c.arcTo(x + w, y, x + w, y + h, r)
  c.arcTo(x + w, y + h, x, y + h, r)
  c.arcTo(x, y + h, x, y, r)
  c.arcTo(x, y, x + w, y, r)
  c.closePath()
}

/* ── 绘制 ── */
function draw() {
  const cv = canvasRef.value
  if (!cv) return
  const { w, h } = size()
  const dpr = window.devicePixelRatio || 1
  cv.width = Math.max(1, Math.round(w * dpr))
  cv.height = Math.max(1, Math.round(h * dpr))
  cv.style.width = w + 'px'
  cv.style.height = h + 'px'
  const c = cv.getContext('2d')
  if (!c) return
  c.setTransform(dpr, 0, 0, dpr, 0, 0)
  c.clearRect(0, 0, w, h)

  const pw = w - M.left - M.right
  const ph = h - M.top - M.bottom
  const { t0, t1 } = props
  const span = Math.max(1, t1 - t0)
  const X = scaleLinear().domain([t0, t1]).range([M.left, M.left + pw])

  const vis = props.points.filter((p) => p.ts >= t0 && p.ts <= t1)
  let maxV = 0
  for (const s of props.series) {
    for (const p of vis) {
      const v = p[s.key]
      if (v != null && v > maxV) maxV = v
    }
  }
  const yMax = props.yMax > 0 ? props.yMax : (maxV > 0 ? maxV * 1.15 : 1)
  const Y = scaleLinear().domain([0, yMax]).range([M.top + ph, M.top])

  c.font = '10px Consolas, monospace'
  c.textBaseline = 'middle'

  // 横向网格 + Y 轴标签
  c.textAlign = 'right'
  const yTicks = ticks(0, yMax, 5)
  for (const tv of yTicks) {
    const y = Y(tv)
    c.strokeStyle = 'rgba(255,255,255,.06)'
    c.lineWidth = 1
    c.beginPath()
    c.moveTo(M.left, y)
    c.lineTo(M.left + pw, y)
    c.stroke()
    c.fillStyle = 'rgba(220,215,200,.45)'
    c.fillText(fmtTick(tv, props.series[0]?.unit || ''), M.left - 6, y)
  }

  // 纵向网格 + 时间轴标签
  c.textAlign = 'center'
  c.textBaseline = 'top'
  for (const tv of timeTicks(t0, t1)) {
    const x = X(tv)
    c.strokeStyle = 'rgba(255,255,255,.06)'
    c.beginPath()
    c.moveTo(x, M.top)
    c.lineTo(x, M.top + ph)
    c.stroke()
    c.fillStyle = 'rgba(220,215,200,.55)'
    c.fillText(fmtAxisTime(tv, span), x, M.top + ph + 6)
  }

  // 边框
  c.strokeStyle = 'rgba(255,255,255,.08)'
  c.lineWidth = 1
  c.strokeRect(M.left, M.top, pw, ph)

  // 折线（含面积渐变）
  for (const s of props.series) {
    const pts: Array<[number, number]> = []
    for (const p of vis) {
      const v = p[s.key]
      if (v == null) continue
      pts.push([X(p.ts), Y(clamp(v, 0, yMax))])
    }
    if (pts.length < 2) continue
    const pFirst = pts[0]!
    const pLast = pts[pts.length - 1]!
    const grad = c.createLinearGradient(0, M.top, 0, M.top + ph)
    grad.addColorStop(0, s.color + '44')
    grad.addColorStop(1, s.color + '00')
    c.beginPath()
    c.moveTo(pFirst[0], M.top + ph)
    for (const [x, y] of pts) c.lineTo(x, y)
    c.lineTo(pLast[0], M.top + ph)
    c.closePath()
    c.fillStyle = grad
    c.fill()

    c.beginPath()
    pts.forEach(([x, y], i) => (i ? c.lineTo(x, y) : c.moveTo(x, y)))
    c.strokeStyle = s.color
    c.lineWidth = 1.8
    c.lineJoin = 'round'
    c.shadowColor = s.color
    c.shadowBlur = 6
    c.stroke()
    c.shadowBlur = 0
  }

  // 无数据提示
  if (vis.length === 0) {
    c.fillStyle = 'rgba(200,195,180,.45)'
    c.textAlign = 'center'
    c.textBaseline = 'middle'
    c.font = '12px system-ui, sans-serif'
    c.fillText('无数据', w / 2, M.top + ph / 2)
    return
  }

  // 悬停：十字准星 + 数据点 + 提示框
  if (hoverPoint.value && hoverPoint.value.ts >= t0 && hoverPoint.value.ts <= t1) {
    const x = X(hoverPoint.value.ts)
    c.strokeStyle = 'rgba(255,255,255,.28)'
    c.setLineDash([3, 3])
    c.beginPath()
    c.moveTo(x, M.top)
    c.lineTo(x, M.top + ph)
    c.stroke()
    c.setLineDash([])
    for (const s of props.series) {
      const v = hoverPoint.value[s.key]
      if (v == null) continue
      c.beginPath()
      c.arc(x, Y(clamp(v, 0, yMax)), 3.2, 0, Math.PI * 2)
      c.fillStyle = s.color
      c.fill()
    }
    drawTooltip(c, w, h, x)
  }
}

function drawTooltip(c: CanvasRenderingContext2D, w: number, h: number, x: number) {
  const p = hoverPoint.value
  if (!p) return
  const lines = [fmtTime(p.ts)]
  const colors = ['rgba(230,225,210,.9)']
  for (const s of props.series) {
    const v = p[s.key]
    if (v == null) continue
    lines.push(`${s.label} ${fmtVal(v, s.unit, s.digits ?? (s.unit === '%' ? 0 : 1))}`)
    colors.push(s.color)
  }
  const lh = 15
  const pad = 7
  c.font = '11px Consolas, monospace'
  let bw = 0
  for (const l of lines) bw = Math.max(bw, c.measureText(l).width)
  bw += pad * 2
  const bh = lines.length * lh + pad
  let bx = x + 14
  if (bx + bw > w - 4) bx = x - 14 - bw
  const by = Math.max(4, M.top + 6)
  c.fillStyle = 'rgba(12,10,6,.94)'
  c.strokeStyle = 'rgba(255,255,255,.16)'
  c.lineWidth = 1
  roundRect(c, bx, by, bw, bh, 5)
  c.fill()
  c.stroke()
  c.textAlign = 'left'
  c.textBaseline = 'middle'
  for (let i = 0; i < lines.length; i++) {
    c.fillStyle = colors[i] ?? 'rgba(230,225,210,.9)'
    c.fillText(lines[i] ?? '', bx + pad, by + pad + lh / 2 + i * lh)
  }
}

/* ── 交互 ── */
function onMouseMove(e: MouseEvent) {
  const cv = canvasRef.value
  if (!cv || drag.on) return
  const rect = cv.getBoundingClientRect()
  const x = e.clientX - rect.left
  const { w } = size()
  const pw = w - M.left - M.right
  if (x < M.left || x > M.left + pw) {
    if (hoverPoint.value) {
      hoverPoint.value = null
      draw()
    }
    return
  }
  const frac = (x - M.left) / Math.max(1, pw)
  const ts = props.t0 + frac * (props.t1 - props.t0)
  const vis = props.points.filter((p) => p.ts >= props.t0 && p.ts <= props.t1)
  if (!vis.length) {
    if (hoverPoint.value) {
      hoverPoint.value = null
      draw()
    }
    return
  }
  const first = vis[0]
  if (!first) {
    if (hoverPoint.value) {
      hoverPoint.value = null
      draw()
    }
    return
  }
  let best: LinePoint = first
  let bd = Math.abs(first.ts - ts)
  for (const p of vis) {
    const d = Math.abs(p.ts - ts)
    if (d < bd) {
      bd = d
      best = p
    }
  }
  const next: LinePoint | null = bd <= (props.t1 - props.t0) * 0.35 ? best : null
  if (next !== hoverPoint.value) {
    hoverPoint.value = next
    draw()
  }
}

function onMouseLeave() {
  if (hoverPoint.value) {
    hoverPoint.value = null
    draw()
  }
}

function onDragMove(e: MouseEvent) {
  if (!drag.on) return
  const span = props.t1 - props.t0
  const { w } = size()
  const dts = ((e.clientX - drag.startX) / Math.max(1, w)) * span
  emit('viewport', { t0: drag.startT0 - dts, t1: drag.startT1 - dts })
}

function onDragEnd() {
  if (!drag.on) return
  drag.on = false
  const cv = canvasRef.value
  if (cv) cv.style.cursor = ''
  window.removeEventListener('mousemove', onDragMove)
  window.removeEventListener('mouseup', onDragEnd)
}

function onWheel(e: WheelEvent) {
  e.preventDefault()
  const span = props.t1 - props.t0
  const factor = e.deltaY > 0 ? 1.22 : 1 / 1.22
  const newSpan = clamp(span * factor, MIN_SPAN, MAX_SPAN)
  const rect = canvasRef.value?.getBoundingClientRect()
  if (!rect) return
  const { w } = size()
  const pw = w - M.left - M.right
  const frac = clamp((e.clientX - rect.left - M.left) / Math.max(1, pw), 0, 1)
  const cursorTs = props.t0 + frac * span
  let nt0 = cursorTs - newSpan * frac
  let nt1 = nt0 + newSpan
  const now = Date.now()
  if (nt1 > now) {
    nt1 = now
    nt0 = nt1 - newSpan
  }
  emit('viewport', { t0: nt0, t1: nt1 })
}

onMounted(() => {
  const cv = canvasRef.value
  if (cv) {
    cv.addEventListener('wheel', onWheel, { passive: false })
    cv.addEventListener('mousemove', onMouseMove)
    cv.addEventListener('mouseleave', onMouseLeave)
    cv.addEventListener('mousedown', (e) => {
      drag.on = true
      drag.startX = e.clientX
      drag.startT0 = props.t0
      drag.startT1 = props.t1
      cv.style.cursor = 'grabbing'
      window.addEventListener('mousemove', onDragMove)
      window.addEventListener('mouseup', onDragEnd)
    })
  }
  if (typeof ResizeObserver !== 'undefined' && wrapRef.value) {
    ro = new ResizeObserver(() => draw())
    ro.observe(wrapRef.value)
  }
  draw()
})

onBeforeUnmount(() => {
  const cv = canvasRef.value
  if (cv) {
    cv.removeEventListener('wheel', onWheel)
    cv.removeEventListener('mousemove', onMouseMove)
    cv.removeEventListener('mouseleave', onMouseLeave)
  }
  window.removeEventListener('mousemove', onDragMove)
  window.removeEventListener('mouseup', onDragEnd)
  ro?.disconnect()
})

watch([() => props.points, () => props.t0, () => props.t1, () => props.yMax, () => props.series], () => draw())
</script>

<template>
  <div ref="wrapRef" class="linechart" :style="{ height: height + 'px' }">
    <canvas ref="canvasRef"></canvas>
  </div>
</template>

<style scoped>
.linechart {
  width: 100%;
  position: relative;
}
.linechart canvas {
  display: block;
  width: 100%;
  height: 100%;
  cursor: crosshair;
  touch-action: none;
}
</style>
