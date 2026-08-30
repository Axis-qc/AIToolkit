<script setup lang="ts">
import { onMounted, onUnmounted, ref, computed, watch, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import type { TerrainParams, CellData } from './terrain'
import {
  CHUNK_SIZE,
  CHUNK_SIZE_LOG2,
  BIOME_COLORS,
  BIOME_NAMES,
  LUT_H,
  LUT_T,
  LUT_M,
  LUT_O,
  LEGEND_ORDER,
  DEFAULT_PARAMS,
  type TerrainWorkerCtx,
  makeTerrainWorkerCtx,
  lutC,
  clamp,
  hash2,
} from './terrain'
import { RESOURCES, BUILDING_MAP } from './data'
import {
  gameState,
  tick,
  startNewGame,
  selectCity,
  selectedCity,
  totalResources,
  setTerrainCtx,
  togglePause,
  setSpeed,
} from './store'

const router = useRouter()

// ==================== 常量与配置 ====================

/** 视图模式 */
type ViewMode = 'terrain' | 'height' | 'temp' | 'moist'

const VIEW_MODES: ViewMode[] = ['terrain', 'height', 'temp', 'moist']
const VIEW_MODE_LABELS: Record<ViewMode, string> = {
  terrain: '地貌',
  height: '高程',
  temp: '温度',
  moist: '湿度',
}

/** 瓦片缓存条目（区块 = CHUNK_SIZE × CHUNK_SIZE 个方块，1 方块 = 1 世界单位） */
interface Tile {
  key: string
  tx: number
  ty: number
  x0: number // chunk 世界坐标起始 X
  y0: number // chunk 世界坐标起始 Y
  canvas: HTMLCanvasElement // 离屏渲染目标（1 像素 = 1 方块）
  ctx: CanvasRenderingContext2D
  elev: Int16Array // 海拔×5
  biome: Uint8Array
  temp: Int16Array // 温度×10
  moist: Uint8Array
  mips: (HTMLCanvasElement | null)[] // 颜色聚合 mip 链（缩小时 N 方块合 1 格）
  dirty: boolean // 需要重新上色
  done: boolean // 生成完成标记
  loaded: boolean // worker 高程数据是否已回填（未回填禁止上色）
  lastUsed: number // LRU 时间戳
  gen: number // 参数代际
  modeKey: string // 视图模式+阴影指纹
}

/** 待生成任务 */
interface GenTask {
  tx: number
  ty: number
  priority: number // 距屏幕中心距离平方
}

// ==================== 响应式状态 ====================

const canvasRef = ref<HTMLCanvasElement | null>(null)
const minimapRef = ref<HTMLCanvasElement | null>(null)
const hoverInfoRef = ref<HTMLDivElement | null>(null)

const viewMode = ref<ViewMode>('terrain')
const showShade = ref(true)
const showGrid = ref(true)

const camX = ref(0)
const camY = ref(0)
const scale = ref(0.1) // 世界单位/像素，越小越放大

const params = ref<TerrainParams>({ ...DEFAULT_PARAMS })

const tileCache = new Map<string, Tile>()
// 锚点瓦片总数 = 每城 (2R+1)^2，R=7 时 225/城；容量留多城扩展余量，否则 LRU 互相淘汰导致无限重生成
const MAX_CACHE = 512
// 锚点生成半径（区块数）：城市周围 R 圈区块全部按方块精度生成，与镜头视口无关（MC 式探索加载）
const ANCHOR_CHUNK_RADIUS = 7
// 颜色聚合 mip 因子：缩小时 N×N 个方块合成 1 显示格（从着色后 canvas 逐级 2×2 box-filter）
const MIP_FACTORS = [2, 4, 8]
const genQueue: GenTask[] = []
const queuedKeys = new Set<string>() // 已在 genQueue 排队的瓦片 key（防止每帧重复入队）
let worker: Worker | null = null
let requestId = 0
const pendingRequests = new Map<number, GenTask>()
const inFlightKeys = new Set<string>() // worker 已接收、正在生成的瓦片 key
let lastMipLevel = -1 // mip 级别滞回记录（0=方块级，1..3=聚合级）

const hoveredCell = ref<{ wx: number; wy: number; data: CellData } | null>(null)
const hoverVisible = ref(false)
const hoverStyle = ref({ transform: 'translate(0,0)' })

// baseImageData 需可重赋值，用 let
let baseImageData: { elev: Int16Array; biome: Uint8Array } | null = null
const baseCanvas = document.createElement('canvas')
baseCanvas.width = 128
baseCanvas.height = 128
const baseCtx = baseCanvas.getContext('2d')!

const statusText = ref('')
let lastFrameTime = 0
let frameId: number | null = null
let isDragging = false
let dragStart = { x: 0, y: 0, camX: 0, camY: 0 }
let pinchState: { dist: number; scale: number; cx: number; cy: number; camX: number; camY: number } | null = null
const pointers = new Map<number, { x: number; y: number }>()

// 主线程轻量采样上下文（用于悬停即时查询，不经过 worker）
let localCtx: TerrainWorkerCtx | null = null

// 当前参数代际（参数改变时递增，强制瓦片重建）
let currentGen = 0

// ==================== 生命周期 ====================

onMounted(async () => {
  await nextTick()
  initWorker()
  initLocalContext()
  if (localCtx) setTerrainCtx(localCtx)
  initCanvas()
  requestBaseMap()
  startRenderLoop()
  bindEvents()
  initGame()
})

onUnmounted(() => {
  stopRenderLoop()
  stopGameTick()
  worker?.terminate()
  unbindEvents()
  tileCache.clear()
  genQueue.length = 0
  queuedKeys.clear()
  pendingRequests.clear()
})

// ==================== 初始化 ====================

function initWorker() {
  // Vite ESM worker 导入
  worker = new Worker(new URL('./terrain.worker.ts', import.meta.url), { type: 'module' })
  worker.onmessage = handleWorkerMessage
  worker.onerror = (e) => {
    console.error('Worker error:', e)
  }
}

function initLocalContext() {
  localCtx = makeTerrainWorkerCtx(params.value)
}

function initCanvas() {
  const cv = canvasRef.value
  if (!cv) return
  resizeCanvas()
  window.addEventListener('resize', resizeCanvas)
}

function resizeCanvas() {
  const cv = canvasRef.value
  if (!cv) return
  const dpr = Math.min(window.devicePixelRatio || 1, 2)
  cv.width = window.innerWidth * dpr
  cv.height = window.innerHeight * dpr
  cv.style.width = window.innerWidth + 'px'
  cv.style.height = window.innerHeight + 'px'
  const ctx = cv.getContext('2d')!
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
  // 缩放下限：最小时整个世界在屏幕内（这里无限世界，设个合理最小值）
  const minScale = Math.min(window.innerWidth, window.innerHeight) / 2_000_000
  if (scale.value < minScale) scale.value = minScale
}

// ==================== Worker 通信 ====================

function handleWorkerMessage(e: MessageEvent) {
  const msg = e.data
  if (msg.cmd === 'base') {
    // 底图
    baseImageData = { elev: msg.elev, biome: msg.biome }
    renderBaseMap()
    return
  }
  // 普通 chunk
  const { id, tx, ty, elev, biome, temp, moist } = msg
  const key = `${tx}:${ty}`
  const tile = tileCache.get(key)
  if (tile) {
    tile.elev = elev
    tile.biome = biome
    tile.temp = temp
    tile.moist = moist
    tile.dirty = true
    tile.loaded = true
    tile.lastUsed = performance.now()
  }
  pendingRequests.delete(id)
  inFlightKeys.delete(key)
}

function requestBaseMap() {
  if (!worker) return
  const id = ++requestId
  worker.postMessage({ id, cmd: 'base', params: { ...params.value } })
}

function requestChunk(tx: number, ty: number) {
  if (!worker) return
  const key = `${tx}:${ty}`
  const tile = tileCache.get(key)
  if (tile && tile.done) return // 已生成完成
  if (inFlightKeys.has(key)) return // 生成中，避免重复请求
  if (pendingRequests.size > 32) return // 积压过多
  const id = ++requestId
  const task: GenTask = { tx, ty, priority: 0 }
  pendingRequests.set(id, task)
  inFlightKeys.add(key)
  worker.postMessage({ id, tx, ty, params: { ...params.value } })
}

// ==================== 渲染循环 ====================

function startRenderLoop() {
  lastFrameTime = performance.now()
  frameId = requestAnimationFrame(renderLoop)
}

function stopRenderLoop() {
  if (frameId !== null) {
    cancelAnimationFrame(frameId)
    frameId = null
  }
}

function renderLoop(now: number) {
  const dt = now - lastFrameTime
  lastFrameTime = now

  // 锚点驱动补全：围绕城市生成缺失区块（与镜头视口无关，MC 式）
  updateAnchorChunks()

  // 处理生成队列：每帧最多发 3 个请求，按优先级排序
  if (genQueue.length > 0) {
    genQueue.sort((a, b) => a.priority - b.priority)
    const budget = 3
    for (let i = 0; i < budget && genQueue.length > 0; i++) {
      const task = genQueue.shift()!
      queuedKeys.delete(`${task.tx}:${task.ty}`)
      requestChunk(task.tx, task.ty)
    }
  }

  // 为脏瓦片上色
  processDirtyTiles()

  if (needsRedraw()) {
    drawMainCanvas()
    drawMinimap()
    updateStatusText()
  }

  frameId = requestAnimationFrame(renderLoop)
}

function needsRedraw(): boolean {
  // 简化：始终重绘（实际可加脏标记优化）
  return true
}

/**
 * 锚点驱动区块补全（MC 式探索加载）
 * 围绕每个城市，按 ANCHOR_CHUNK_RADIUS 圈区块全部按方块精度生成。
 * 与镜头视口完全无关——镜头移动/缩放不触发生成。
 */
function updateAnchorChunks() {
  const modeKey = `${viewMode.value}|${showShade.value}`
  for (const city of gameState.cities) {
    const ctx0 = Math.floor(city.x / CHUNK_SIZE)
    const cty0 = Math.floor(city.y / CHUNK_SIZE)
    const tx0 = ctx0 - ANCHOR_CHUNK_RADIUS
    const tx1 = ctx0 + ANCHOR_CHUNK_RADIUS
    const ty0 = cty0 - ANCHOR_CHUNK_RADIUS
    const ty1 = cty0 + ANCHOR_CHUNK_RADIUS
    for (let ty = ty0; ty <= ty1; ty++) {
      for (let tx = tx0; tx <= tx1; tx++) {
        enqueueAnchorTile(tx, ty, modeKey)
      }
    }
  }
}

/**
 * 单个锚点瓦片的入队判定：
 * 已就绪跳过；生成中跳过；已排队跳过；否则创建瓦片并按距镜头中心距离入队
 */
function enqueueAnchorTile(tx: number, ty: number, modeKey: string) {
  const key = `${tx}:${ty}`
  let tile = tileCache.get(key)
  if (tile && tile.done && tile.gen === currentGen && tile.modeKey === modeKey) return
  if (inFlightKeys.has(key)) return
  if (queuedKeys.has(key)) return

  if (!tile) tile = createTile(tx, ty)
  tile.gen = currentGen
  tile.modeKey = modeKey
  tile.done = false
  tile.dirty = true

  // 距屏幕中心越近优先级越高（仅影响生成顺序，不影响是否生成）
  const awx = (tx + 0.5) * CHUNK_SIZE
  const awy = (ty + 0.5) * CHUNK_SIZE
  const dist2 = (awx - camX.value) ** 2 + (awy - camY.value) ** 2
  genQueue.push({ tx, ty, priority: dist2 })
  queuedKeys.add(key)
}

// ==================== 主画布渲染 ====================

function drawMainCanvas() {
  const cv = canvasRef.value
  if (!cv) return
  const ctx = cv.getContext('2d')!
  const vw = cv.width / (window.devicePixelRatio || 1)
  const vh = cv.height / (window.devicePixelRatio || 1)

  // 清屏
  ctx.fillStyle = '#04060a'
  ctx.fillRect(0, 0, vw, vh)

  // 视口世界坐标范围
  const wx0 = camX.value - vw / (2 * scale.value)
  const wy0 = camY.value - vh / (2 * scale.value)
  const wx1 = camX.value + vw / (2 * scale.value)
  const wy1 = camY.value + vh / (2 * scale.value)

  // 底图兜底：按视口世界范围裁剪底图源区域绘制（目标尺寸恒为视口大小，任意缩放不爆炸；兼作未探索区底色）
  if (baseImageData) {
    ctx.imageSmoothingEnabled = true
    const WORLD_VIEW_RADIUS = 8_000_000
    const worldW = WORLD_VIEW_RADIUS * 2
    const bw = baseCanvas.width
    const bh = baseCanvas.height
    const srcX = ((wx0 + WORLD_VIEW_RADIUS) / worldW) * bw
    const srcY = ((wy0 + WORLD_VIEW_RADIUS) / worldW) * bh
    const srcW = Math.max(0.5, (vw / scale.value / worldW) * bw)
    const srcH = Math.max(0.5, (vh / scale.value / worldW) * bh)
    ctx.drawImage(baseCanvas, srcX, srcY, srcW, srcH, 0, 0, vw, vh)
  }

  // 选 mip 级（滞回：每帧最多变化 1 级，减少缩放抖动闪烁）
  // 0=方块级（1 格 = 1 方块）；k=1..3 为聚合级（MIP_FACTORS[k-1]^2 个方块合 1 格）
  let level = scale.value >= 1 ? 0
    : scale.value >= 0.5 ? 1
    : scale.value >= 0.25 ? 2
    : 3
  if (lastMipLevel < 0) lastMipLevel = level
  else {
    level = clamp(level, lastMipLevel - 1, lastMipLevel + 1)
    level = clamp(level, 0, MIP_FACTORS.length)
    lastMipLevel = level
  }
  const modeKey = `${viewMode.value}|${showShade.value}`

  // 只画不生成：遍历缓存（≤ MAX_CACHE）做视口相交剔除，方块级/聚合级统一绘制
  for (const tile of tileCache.values()) {
    if (!tile.done || tile.gen !== currentGen || tile.modeKey !== modeKey) continue

    // 视口相交剔除（瓦片世界尺寸恒为 CHUNK_SIZE）
    if (tile.x0 > wx1 || tile.y0 > wy1) continue
    if (tile.x0 + CHUNK_SIZE < wx0 || tile.y0 + CHUNK_SIZE < wy0) continue

    // 触摸 LRU 时间戳：正在显示的瓦片不被淘汰
    tile.lastUsed = performance.now()

    const sx = (tile.x0 - wx0) * scale.value
    const sy = (tile.y0 - wy0) * scale.value
    const sw = CHUNK_SIZE * scale.value

    if (level === 0) {
      // 方块级：放大时关闭平滑保持 MC 式硬边方块；1 像素 = 1 方块，与网格/悬停严格对齐
      ctx.imageSmoothingEnabled = scale.value < 1
      ctx.drawImage(tile.canvas, 0, 0, CHUNK_SIZE, CHUNK_SIZE, sx, sy, sw, sw)
    } else {
      // 聚合级：颜色 mip（着色后逐级 2×2 box-filter），缩小时 N 方块合 1 格
      const mip = tile.mips[level - 1]
      ctx.imageSmoothingEnabled = true
      if (mip) {
        ctx.drawImage(mip, 0, 0, mip.width, mip.height, sx, sy, sw, sw)
      } else {
        ctx.drawImage(tile.canvas, 0, 0, CHUNK_SIZE, CHUNK_SIZE, sx, sy, sw, sw)
      }
    }
  }

  // 网格线
  if (showGrid.value && scale.value >= 5) {
    drawGrid(ctx, vw, vh, wx0, wy0, wx1, wy1)
  }

  // 悬停高亮
  if (hoveredCell.value && scale.value >= 1) {
    const hx = (hoveredCell.value.wx - wx0) * scale.value
    const hy = (hoveredCell.value.wy - wy0) * scale.value
    const gs = scale.value
    ctx.strokeStyle = 'rgba(143, 208, 198, 0.9)'
    ctx.lineWidth = 1.5
    ctx.strokeRect(hx, hy, gs, gs)
  }

  // 城市与领土绘制（游戏层）
  drawCityLayer(ctx, wx0, wy0, vw, vh)
}

function createTile(tx: number, ty: number): Tile {
  const x0 = tx * CHUNK_SIZE
  const y0 = ty * CHUNK_SIZE

  const canvas = document.createElement('canvas')
  canvas.width = CHUNK_SIZE
  canvas.height = CHUNK_SIZE
  const ctx = canvas.getContext('2d')!

  const tile: Tile = {
    key: `${tx}:${ty}`,
    tx,
    ty,
    x0,
    y0,
    canvas,
    ctx,
    elev: new Int16Array(CHUNK_SIZE * CHUNK_SIZE),
    biome: new Uint8Array(CHUNK_SIZE * CHUNK_SIZE),
    temp: new Int16Array(CHUNK_SIZE * CHUNK_SIZE),
    moist: new Uint8Array(CHUNK_SIZE * CHUNK_SIZE),
    mips: MIP_FACTORS.map(() => null),
    dirty: true,
    done: false,
    loaded: false,
    lastUsed: performance.now(),
    gen: currentGen,
    modeKey: '',
  }

  // LRU 淘汰：优先淘汰非生成中的瓦片；全部在生成中则放弃淘汰（避免重生成死循环）
  while (tileCache.size >= MAX_CACHE) {
    let oldest: Tile | null = null
    for (const t of tileCache.values()) {
      if (inFlightKeys.has(t.key)) continue
      if (!oldest || t.lastUsed < oldest.lastUsed) oldest = t
    }
    if (!oldest) break
    tileCache.delete(oldest.key)
  }

  tileCache.set(tile.key, tile)
  return tile
}

function recolorAllTiles() {
  currentGen++
  for (const tile of tileCache.values()) {
    tile.dirty = true
    tile.done = false
    tile.modeKey = `${viewMode.value}|${showShade.value}`
  }
  genQueue.length = 0 // 清空队列，重新按需请求
  queuedKeys.clear()
}

// ==================== 瓦片上色（主线程实时计算） ====================

/**
 * 根据视图模式为瓦片生成 ImageData 并绘制到离屏 canvas
 * 只在 tile.dirty=true 时调用
 */
function colorizeTile(tile: Tile) {
  const { elev, biome, temp, moist, canvas, ctx } = tile
  const imgData = ctx.createImageData(CHUNK_SIZE, CHUNK_SIZE)
  const data = imgData.data

  const mode = viewMode.value
  const shade = showShade.value && mode !== 'temp' && mode !== 'moist'
  const q = 0.1 // Int16×5 → 米 → 每方块（1 世界单位）梯度系数

  const out = new Float32Array(3)

  for (let j = 0; j < CHUNK_SIZE; j++) {
    for (let i = 0; i < CHUNK_SIZE; i++) {
      const idx = j * CHUNK_SIZE + i
      const o = idx * 4

      const rawElev = elev[idx]
      if (rawElev === 32767) {
        data[o + 3] = 0
        continue
      }

      const em = rawElev / 5 // 实际海拔米

      if (em <= 0) {
        // 海洋
        if (mode === 'moist') {
          out[0] = 18; out[1] = 50; out[2] = 74
        } else if (mode === 'temp') {
          // 海洋温度用表面温度近似（这里简化用固定值）
          lutC(LUT_T, 0.2, out)
        } else if (em < -4200) {
          lutC(LUT_O, 1, out) // 最深
        } else if (em < -2400) {
          lutC(LUT_O, 0.5, out)
        } else if (em < -1000) {
          lutC(LUT_O, 0.2, out)
        } else {
          lutC(LUT_O, 0.05, out)
        }
      } else {
        // 陆地
        if (mode === 'terrain') {
          const bid = biome[idx]
          const base = bid * 3
          out[0] = BIOME_COLORS[base]
          out[1] = BIOME_COLORS[base + 1]
          out[2] = BIOME_COLORS[base + 2]
        } else if (mode === 'height') {
          lutC(LUT_H, clamp(em / 4800, 0, 1), out)
        } else if (mode === 'temp') {
          const t = temp[idx] / 10
          lutC(LUT_T, clamp((t + 38) / 78, 0, 1), out)
        } else { // moist
          const m = moist[idx] / 100
          lutC(LUT_M, m, out)
        }

        // 山体阴影（光源左上）
        if (shade) {
          // 计算邻格高差
          let dzx = 0, dzy = 0
          if (i > 0 && i < CHUNK_SIZE - 1) {
            const r = elev[idx + 1], l = elev[idx - 1]
            if (r !== 32767 && l !== 32767) dzx = (r - l) * q
          }
          if (j > 0 && j < CHUNK_SIZE - 1) {
            const d = elev[idx + CHUNK_SIZE], u = elev[idx - CHUNK_SIZE]
            if (d !== 32767 && u !== 32767) dzy = (d - u) * q
          }
          const kx = -dzx * 0.09, ky = -dzy * 0.09
          const il = 1 / Math.sqrt(kx * kx + ky * ky + 1)
          const lam = (kx * -0.60 + ky * -0.65 + 0.46) * il
          const s = 0.60 + 0.55 * Math.max(0, lam)
          out[0] *= s; out[1] *= s; out[2] *= s
        }
      }

      // 色抖动（逐方块伪随机，1 方块 = 1 世界单位，与网格严格对齐）
      const wx = tile.x0 + i + 0.5
      const wy = tile.y0 + j + 0.5
      const jitter = 0.94 + 0.12 * hash2(Math.floor(wx), Math.floor(wy))
      data[o] = Math.min(255, out[0] * jitter)
      data[o + 1] = Math.min(255, out[1] * jitter)
      data[o + 2] = Math.min(255, out[2] * jitter)
      data[o + 3] = 255
    }
  }

  ctx.putImageData(imgData, 0, 0)
  tile.dirty = false
  tile.done = true
  buildMips(tile)
}

/**
 * 颜色聚合 mip 链：从着色后的瓦片 canvas 逐级 2×2 box-filter 降采样
 * 缩放显示时 N×N 个方块的颜色合为 1 格（含阴影与色抖动的平均），无需保留聚合数据
 */
function buildMips(tile: Tile) {
  let prev = tile.ctx.getImageData(0, 0, CHUNK_SIZE, CHUNK_SIZE)
  let size = CHUNK_SIZE
  for (let m = 0; m < tile.mips.length; m++) {
    const next = size >> 1
    const out = new ImageData(next, next)
    const sd = prev.data
    const od = out.data
    for (let j = 0; j < next; j++) {
      const row0 = j * 2 * size
      const row1 = row0 + size
      for (let i = 0; i < next; i++) {
        const a = (row0 + i * 2) * 4
        const b = a + 4
        const c = (row1 + i * 2) * 4
        const d = c + 4
        const o = (j * next + i) * 4
        od[o] = (sd[a] + sd[b] + sd[c] + sd[d]) >> 2
        od[o + 1] = (sd[a + 1] + sd[b + 1] + sd[c + 1] + sd[d + 1]) >> 2
        od[o + 2] = (sd[a + 2] + sd[b + 2] + sd[c + 2] + sd[d + 2]) >> 2
        od[o + 3] = (sd[a + 3] + sd[b + 3] + sd[c + 3] + sd[d + 3]) >> 2
      }
    }
    let mc = tile.mips[m]
    if (!mc) {
      mc = document.createElement('canvas')
      mc.width = next
      mc.height = next
      tile.mips[m] = mc
    }
    mc.getContext('2d')!.putImageData(out, 0, 0)
    prev = out
    size = next
  }
}

// 在渲染循环中为脏瓦片上色
function processDirtyTiles() {
  for (const tile of tileCache.values()) {
    if (tile.dirty && tile.loaded && tile.elev.length > 0) {
      colorizeTile(tile)
    }
  }
}

// ==================== 底图渲染 ====================

function renderBaseMap() {
  if (!baseImageData) return
  const { elev, biome } = baseImageData
  const imgData = baseCtx.createImageData(128, 128)
  const data = imgData.data
  const out = new Float32Array(3)

  for (let j = 0; j < 128; j++) {
    for (let i = 0; i < 128; i++) {
      const idx = j * 128 + i
      const o = idx * 4
      const rawElev = elev[idx]
      if (rawElev === 32767) {
        data[o + 3] = 0
        continue
      }
      const em = rawElev / 5
      const bid = biome[idx]

      if (em <= 0) {
        if (em < -4200) lutC(LUT_O, 1, out)
        else if (em < -2400) lutC(LUT_O, 0.5, out)
        else if (em < -1000) lutC(LUT_O, 0.2, out)
        else lutC(LUT_O, 0.05, out)
      } else {
        const base = bid * 3
        out[0] = BIOME_COLORS[base]
        out[1] = BIOME_COLORS[base + 1]
        out[2] = BIOME_COLORS[base + 2]
      }
      data[o] = out[0]
      data[o + 1] = out[1]
      data[o + 2] = out[2]
      data[o + 3] = 255
    }
  }
  baseCtx.putImageData(imgData, 0, 0)
}

// ==================== 网格线 ====================

function drawGrid(
  ctx: CanvasRenderingContext2D,
  vw: number,
  vh: number,
  wx0: number,
  wy0: number,
  wx1: number,
  wy1: number
) {
  ctx.strokeStyle = 'rgba(6,10,16,0.25)'
  ctx.lineWidth = 1
  ctx.beginPath()

  const gx0 = Math.ceil(wx0)
  const gx1 = Math.floor(wx1)
  const gy0 = Math.ceil(wy0)
  const gy1 = Math.floor(wy1)

  if (gx1 - gx0 < 600) {
    for (let x = gx0; x <= gx1; x++) {
      const sx = Math.round((x - wx0) * scale.value) + 0.5
      ctx.moveTo(sx, 0)
      ctx.lineTo(sx, vh)
    }
  }
  if (gy1 - gy0 < 600) {
    for (let y = gy0; y <= gy1; y++) {
      const sy = Math.round((y - wy0) * scale.value) + 0.5
      ctx.moveTo(0, sy)
      ctx.lineTo(vw, sy)
    }
  }
  ctx.stroke()
}

// ==================== 小地图 ====================

function drawMinimap() {
  const mm = minimapRef.value
  if (!mm || !baseImageData) return
  const mmctx = mm.getContext('2d')!
  const size = 132

  mmctx.imageSmoothingEnabled = true
  mmctx.drawImage(baseCanvas, 0, 0, size, size)

  // 视口矩形
  const vw = canvasRef.value?.width ?? window.innerWidth
  const vh = canvasRef.value?.height ?? window.innerHeight
  const wx0 = camX.value - vw / (2 * scale.value)
  const wy0 = camY.value - vh / (2 * scale.value)

  const WORLD_VIEW_RADIUS = 8_000_000
  const scaleFactor = size / (WORLD_VIEW_RADIUS * 2)

  const rx = (wx0 + WORLD_VIEW_RADIUS) * scaleFactor
  const ry = (wy0 + WORLD_VIEW_RADIUS) * scaleFactor
  const rw = (vw / scale.value) * scaleFactor
  const rh = (vh / scale.value) * scaleFactor

  mmctx.strokeStyle = 'rgba(255,255,255,0.9)'
  mmctx.lineWidth = 1.2
  mmctx.strokeRect(rx, ry, Math.min(rw, size), Math.min(rh, size))
}

// ==================== 悬停检查 ====================

function updateHover(mx: number, my: number) {
  const cv = canvasRef.value
  if (!cv) return
  const vw = cv.width / (window.devicePixelRatio || 1)
  const vh = cv.height / (window.devicePixelRatio || 1)

  const gx = Math.floor(camX.value + (mx - vw / 2) / scale.value)
  const gy = Math.floor(camY.value + (my - vh / 2) / scale.value)

  // 无限世界无边界检查
  if (!localCtx) return

  const cell = localCtx.sampleCell(gx + 0.5, gy + 0.5, 0)
  hoveredCell.value = { wx: gx, wy: gy, data: cell }
  hoverVisible.value = true

  // 浮层位置
  const tw = hoverInfoRef.value?.offsetWidth ?? 200
  const th = hoverInfoRef.value?.offsetHeight ?? 100
  let x = mx + 16
  let y = my + 18
  if (x + tw > vw - 8) x = mx - tw - 14
  if (y + th > vh - 8) y = my - th - 14
  hoverStyle.value = { transform: `translate(${x}px, ${y}px)` }
}

function hideHover() {
  hoverVisible.value = false
  hoveredCell.value = null
}

// ==================== 相机交互 ====================

function zoomAt(mx: number, my: number, factor: number) {
  const cv = canvasRef.value
  if (!cv) return
  const vw = cv.width / (window.devicePixelRatio || 1)
  const vh = cv.height / (window.devicePixelRatio || 1)

  const wx = camX.value + (mx - vw / 2) / scale.value
  const wy = camY.value + (my - vh / 2) / scale.value

  const newScale = clamp(scale.value * factor, 0.0005, 32)
  scale.value = newScale

  camX.value = wx - (mx - vw / 2) / scale.value
  camY.value = wy - (my - vh / 2) / scale.value
}

function handleWheel(e: WheelEvent) {
  e.preventDefault()
  const cv = canvasRef.value
  if (!cv) return
  const rect = cv.getBoundingClientRect()
  const mx = e.clientX - rect.left
  const my = e.clientY - rect.top
  zoomAt(mx, my, Math.exp(-e.deltaY * 0.0012))
}

function handlePointerDown(e: PointerEvent) {
  const cv = canvasRef.value
  if (!cv) return
  cv.setPointerCapture(e.pointerId)
  pointers.set(e.pointerId, { x: e.clientX, y: e.clientY })

  if (pointers.size === 1) {
    isDragging = true
    dragStart = { x: e.clientX, y: e.clientY, camX: camX.value, camY: camY.value }
    cv.style.cursor = 'grabbing'
  } else if (pointers.size === 2) {
    const pts = [...pointers.values()]
    const cx = (pts[0].x + pts[1].x) / 2
    const cy = (pts[0].y + pts[1].y) / 2
    const dist = Math.hypot(pts[0].x - pts[1].x, pts[0].y - pts[1].y)
    pinchState = { dist, scale: scale.value, cx, cy, camX: camX.value, camY: camY.value }
  }
  hideHover()
}

function handlePointerMove(e: PointerEvent) {
  if (!pointers.has(e.pointerId)) {
    // 纯悬停
    const cv = canvasRef.value
    if (!cv) return
    const rect = cv.getBoundingClientRect()
    updateHover(e.clientX - rect.left, e.clientY - rect.top)
    return
  }

  const p = pointers.get(e.pointerId)!
  const dx = e.clientX - p.x
  const dy = e.clientY - p.y
  p.x = e.clientX
  p.y = e.clientY

  if (pointers.size === 1 && isDragging) {
    // 使用按下时相机 + 总位移（dragStart.x/y 为按下点），避免单帧增量导致镜头被拉回
    camX.value = dragStart.camX - (e.clientX - dragStart.x) / scale.value
    camY.value = dragStart.camY - (e.clientY - dragStart.y) / scale.value
  } else if (pointers.size === 2 && pinchState) {
    const pts = [...pointers.values()]
    const dist = Math.hypot(pts[0].x - pts[1].x, pts[0].y - pts[1].y)
    const newScale = clamp(pinchState.scale * dist / pinchState.dist, 0.0005, 32)
    scale.value = newScale
    const cx = (pts[0].x + pts[1].x) / 2
    const cy = (pts[0].y + pts[1].y) / 2
    const cv = canvasRef.value
    if (!cv) return
    const vw = cv.width / (window.devicePixelRatio || 1)
    const vh = cv.height / (window.devicePixelRatio || 1)
    // 保持按下时两指中点的世界坐标在缩放前后固定
    const wx = pinchState.camX + (pinchState.cx - vw / 2) / pinchState.scale
    const wy = pinchState.camY + (pinchState.cy - vh / 2) / pinchState.scale
    camX.value = wx - (cx - vw / 2) / scale.value
    camY.value = wy - (cy - vh / 2) / scale.value
  }
}

function handlePointerUp(e: PointerEvent) {
  pointers.delete(e.pointerId)
  if (pointers.size < 2) pinchState = null
  if (pointers.size === 0) {
    isDragging = false
    const cv = canvasRef.value
    if (cv) cv.style.cursor = 'crosshair'
  }
}

function handleDblClick(e: MouseEvent) {
  const cv = canvasRef.value
  if (!cv) return
  const rect = cv.getBoundingClientRect()
  zoomAt(e.clientX - rect.left, e.clientY - rect.top, 2)
}

function handleMinimapClick(e: MouseEvent) {
  const mm = minimapRef.value
  if (!mm) return
  const rect = mm.getBoundingClientRect()
  const x = e.clientX - rect.left
  const y = e.clientY - rect.top
  const WORLD_VIEW_RADIUS = 8_000_000
  camX.value = clamp(x / rect.width, 0, 1) * WORLD_VIEW_RADIUS * 2 - WORLD_VIEW_RADIUS
  camY.value = clamp(y / rect.height, 0, 1) * WORLD_VIEW_RADIUS * 2 - WORLD_VIEW_RADIUS
}

// ==================== 事件绑定 ====================

function bindEvents() {
  const cv = canvasRef.value
  if (!cv) return
  cv.addEventListener('wheel', handleWheel, { passive: false })
  cv.addEventListener('pointerdown', handlePointerDown)
  cv.addEventListener('pointermove', handlePointerMove)
  cv.addEventListener('pointerup', handlePointerUp)
  cv.addEventListener('pointercancel', handlePointerUp)
  cv.addEventListener('pointerleave', hideHover)
  cv.addEventListener('dblclick', handleDblClick)
  cv.addEventListener('click', handleCanvasClick)

  const mm = minimapRef.value
  if (mm) {
    mm.addEventListener('click', handleMinimapClick)
  }
  window.addEventListener('keydown', handleKeydown)

  // 参数变化监听
  watch(viewMode, recolorAllTiles)
  watch(showShade, recolorAllTiles)
  watch(() => params.value, () => {
    currentGen++
    initLocalContext()
    requestBaseMap()
    tileCache.clear()
    genQueue.length = 0
    queuedKeys.clear()
  }, { deep: true })
}

function unbindEvents() {
  const cv = canvasRef.value
  if (!cv) return
  cv.removeEventListener('wheel', handleWheel)
  cv.removeEventListener('pointerdown', handlePointerDown)
  cv.removeEventListener('pointermove', handlePointerMove)
  cv.removeEventListener('pointerup', handlePointerUp)
  cv.removeEventListener('pointercancel', handlePointerUp)
  cv.removeEventListener('pointerleave', hideHover)
  cv.removeEventListener('dblclick', handleDblClick)
  cv.removeEventListener('click', handleCanvasClick)

  const mm = minimapRef.value
  if (mm) {
    mm.removeEventListener('click', handleMinimapClick)
  }
  window.removeEventListener('keydown', handleKeydown)
  window.removeEventListener('resize', resizeCanvas)
}

// ==================== 状态栏 ====================

function updateStatusText() {
  const cacheSize = tileCache.size
  const queueSize = genQueue.length
  statusText.value = `中心 ${Math.round(camX.value)}, ${Math.round(camY.value)} · 1格=${scale.value.toFixed(3)}px · 瓦片 ${cacheSize}(待${queueSize}) · 种子 ${params.value.seed}`
}

// ==================== UI 事件处理 ====================

function setViewMode(m: ViewMode) {
  viewMode.value = m
}

function randomizeSeed() {
  params.value = { ...params.value, seed: Math.floor(Math.random() * 1e9) }
}

function goBack() {
  router.push('/games')
}

// ==================== ESC 设置界面 ====================

const settingsOpen = ref(false)
let pausedBeforeSettings = false

function openSettings() {
  if (settingsOpen.value) return
  settingsOpen.value = true
  // MC 式：打开设置自动暂停，关闭时恢复进入前状态
  pausedBeforeSettings = gameState.isPaused
  gameState.isPaused = true
}

function closeSettings() {
  if (!settingsOpen.value) return
  settingsOpen.value = false
  gameState.isPaused = pausedBeforeSettings
}

function toggleSettings() {
  if (settingsOpen.value) closeSettings()
  else openSettings()
}

function handleKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape') {
    e.preventDefault()
    toggleSettings()
  }
}

// ==================== 游戏层（阶段 2：城市经济） ====================

let gameTickTimer: number | null = null

/** 初始化游戏：开局建城 + 启动每秒 tick */
function initGame() {
  if (gameState.cities.length === 0) {
    startNewGame()
  }
  startGameTick()
}

function startGameTick() {
  if (gameTickTimer !== null) return
  gameTickTimer = window.setInterval(() => {
    tick(1)
  }, 1000)
}

function stopGameTick() {
  if (gameTickTimer !== null) {
    window.clearInterval(gameTickTimer)
    gameTickTimer = null
  }
}

/** 数字格式化（千/百万缩写） */
function fmtNum(n: number): string {
  if (!isFinite(n)) return '0'
  const abs = Math.abs(n)
  if (abs >= 1e6) return (n / 1e6).toFixed(1) + 'M'
  if (abs >= 1e3) return (n / 1e3).toFixed(1) + 'k'
  return Math.floor(n).toString()
}

/** 建筑名（面板显示） */
function buildingName(id: string): string {
  return BUILDING_MAP[id as keyof typeof BUILDING_MAP]?.name ?? id
}

/** 在画布上绘制城市中心与领土格覆盖（游戏层） */
function drawCityLayer(
  ctx: CanvasRenderingContext2D,
  wx0: number,
  wy0: number,
  vw: number,
  vh: number
) {
  for (const city of gameState.cities) {
    const isSelected = city.id === gameState.selectedCityId
    // 领土格半透明覆盖
    ctx.fillStyle = isSelected
      ? 'rgba(212, 175, 55, 0.26)'
      : 'rgba(212, 175, 55, 0.13)'
    const gs = scale.value
    for (const key of city.territory) {
      const idx = key.indexOf(',')
      const cx = Number(key.slice(0, idx))
      const cy = Number(key.slice(idx + 1))
      const sx = (cx - wx0) * scale.value
      const sy = (cy - wy0) * scale.value
      if (sx > -gs && sy > -gs && sx < vw + gs && sy < vh + gs) {
        ctx.fillRect(sx, sy, gs, gs)
      }
    }
    // 城市中心图标
    const sx = (city.x - wx0) * scale.value
    const sy = (city.y - wy0) * scale.value
    const r = Math.max(5, Math.min(14, scale.value * 0.5))
    if (sx > -r && sy > -r && sx < vw + r && sy < vh + r) {
      ctx.beginPath()
      ctx.arc(sx, sy, r, 0, Math.PI * 2)
      ctx.fillStyle = isSelected ? '#f0d678' : '#d4af37'
      ctx.strokeStyle = '#7a5c10'
      ctx.lineWidth = 2
      ctx.fill()
      ctx.stroke()
      if (scale.value >= 0.55) {
        ctx.font = '11px "Microsoft YaHei", sans-serif'
        ctx.fillStyle = '#fff'
        ctx.textAlign = 'left'
        ctx.fillText(city.name, sx + r + 4, sy + 4)
      }
    }
  }
}

/** 画布点击：选中城市 */
function handleCanvasClick(e: MouseEvent) {
  const cv = canvasRef.value
  if (!cv) return
  const rect = cv.getBoundingClientRect()
  const mx = e.clientX - rect.left
  const my = e.clientY - rect.top
  const vw = cv.width / (window.devicePixelRatio || 1)
  const vh = cv.height / (window.devicePixelRatio || 1)
  const gx = camX.value + (mx - vw / 2) / scale.value
  const gy = camY.value + (my - vh / 2) / scale.value
  const thr = 18 / scale.value
  let best: number | null = null
  let bestDist = thr
  for (const city of gameState.cities) {
    const d = Math.hypot(city.x - gx, city.y - gy)
    if (d < bestDist) {
      bestDist = d
      best = city.id
    }
  }
  if (best !== null) {
    selectCity(best)
  }
}

/** 游戏速度选择（HUD 用） */
const speedSel = ref(1)
function onSpeedChange() {
  setSpeed(speedSel.value)
}

// ==================== 图例数据 ====================

const legendItems = computed(() =>
  LEGEND_ORDER.map((i) => ({
    name: BIOME_NAMES[i],
    color: `rgb(${BIOME_COLORS[i * 3]}, ${BIOME_COLORS[i * 3 + 1]}, ${BIOME_COLORS[i * 3 + 2]})`,
    desc: BIOME_NAMES[i], // 简化：描述用名称
  }))
)
</script>

<template>
  <div class="voxel4x">
    <!-- 主画布 -->
    <canvas
      ref="canvasRef"
      class="main-canvas"
      @contextmenu.prevent
    ></canvas>

    <!-- 顶栏 -->
    <header class="top-bar">
      <div class="title-section">
        <div class="kicker">4X GRAND STRATEGY // PHASE 1</div>
        <h1>大陆纪元 · Voxel 4X</h1>
      </div>
      <div class="top-actions">
        <button class="btn ghost" @click="goBack">返回</button>
      </div>
    </header>

    <!-- 顶部资源 HUD（游戏层） -->
    <div class="hud" v-if="gameState.cities.length > 0">
      <div class="hud-item gold">
        <span>金币</span><b>{{ fmtNum(totalResources.money) }}</b>
      </div>
      <div class="hud-item" v-for="r in RESOURCES" :key="r.id">
        <span>{{ r.name }}</span><b>{{ fmtNum(totalResources[r.id]) }}</b>
      </div>
      <div class="hud-item">
        <span>人口</span><b>{{ fmtNum(totalResources.population) }}</b>
      </div>
      <div class="hud-actions">
        <button class="btn mini" @click="togglePause">{{ gameState.isPaused ? '继续' : '暂停' }}</button>
        <select class="speed-sel" v-model.number="speedSel" @change="onSpeedChange">
          <option :value="1">1x</option>
          <option :value="2">2x</option>
          <option :value="4">4x</option>
        </select>
      </div>
    </div>

    <!-- 悬停信息浮层 -->
    <div
      ref="hoverInfoRef"
      v-if="hoverVisible && hoveredCell"
      class="hover-tip"
      :style="hoverStyle"
    >
      <b>{{ hoveredCell.wx }}, {{ hoveredCell.wy }}</b>
      <span>{{ BIOME_NAMES[hoveredCell.data.biomeId] }}</span>
      <span v-if="hoveredCell.data.elevM > 0">· {{ Math.round(hoveredCell.data.elevM) }} m</span>
      <span v-else>· 海洋</span>
      <span>· {{ hoveredCell.data.temp.toFixed(1) }} °C</span>
      <span>· {{ Math.round(hoveredCell.data.moist * 100) }}%</span>
    </div>

    <!-- 右下：小地图 + 图例 -->
    <div class="right-panel">
      <div class="card minimap-card">
        <canvas ref="minimapRef" class="minimap-canvas" width="132" height="132" @click="handleMinimapClick"></canvas>
        <div class="minimap-caption">世界全图 · 点击跳转</div>
      </div>
      <div class="card legend-card">
        <div class="legend-title">地貌图例</div>
        <div class="legend-grid">
          <div class="legend-item" v-for="item in legendItems" :key="item.name" :title="item.desc">
            <i :style="{ background: item.color }"></i>
            <span>{{ item.name }}</span>
          </div>
        </div>
      </div>
    </div>

    <!-- 城市面板（游戏层） -->
    <div class="city-panel" v-if="selectedCity">
      <div class="cp-head">
        <div>
          <div class="kicker">CITY / {{ selectedCity.id }}</div>
          <h3>{{ selectedCity.name }}</h3>
        </div>
        <div class="cp-stat">
          <span>人口</span><b>{{ fmtNum(selectedCity.population) }}</b>
        </div>
      </div>
      <div class="cp-sec-title">本地市场（价格 / 库存 / 需给）</div>
      <div class="cp-market">
        <div class="cp-row" v-for="r in RESOURCES" :key="r.id">
          <span class="cp-res">{{ r.name }}</span>
          <span class="cp-price">{{ selectedCity.market[r.id].price.toFixed(2) }}</span>
          <span class="cp-stock">{{ fmtNum(selectedCity.market[r.id].stock) }}</span>
          <span class="cp-bs">
            {{ selectedCity.market[r.id].buy.toFixed(1) }}/{{ selectedCity.market[r.id].sell.toFixed(1) }}
          </span>
        </div>
      </div>
      <div class="cp-sec-title">建筑 {{ selectedCity.buildings.length }} · 领土 {{ selectedCity.territory.length }} 格</div>
      <div class="cp-buildings">
        <div class="cp-b" v-for="b in selectedCity.buildings" :key="b.cellKey">
          <span class="cp-b-name">{{ buildingName(b.defId) }}</span>
          <span class="cp-b-cell">{{ b.cellKey }}</span>
        </div>
        <div class="cp-empty" v-if="!selectedCity.buildings.length">尚无建筑，城市正在自动开发周边土地</div>
      </div>
      <div class="cp-dev" v-if="selectedCity.devQueue.length">
        <div class="cp-sec-title">开发中</div>
        <div class="cp-b" v-for="t in selectedCity.devQueue" :key="t.cellKey">
          <span class="cp-b-name">{{ buildingName(t.buildingId) }}</span>
          <span class="cp-b-cell">{{ t.cellKey }}</span>
          <span class="cp-progress">{{ (t.progress * 100).toFixed(0) }}%</span>
        </div>
      </div>
    </div>

    <!-- 底部状态栏 -->
    <footer class="status-bar">
      <span v-text="statusText"></span>
      <span class="status-hint">ESC 设置</span>
    </footer>

    <!-- ESC 设置界面 -->
    <div class="settings-overlay" v-if="settingsOpen" @click.self="closeSettings">
      <div class="settings-modal">
        <div class="set-head">
          <div>
            <div class="kicker">GAME SETTINGS</div>
            <h3>设置</h3>
          </div>
          <button class="btn ghost" @click="closeSettings">继续游戏</button>
        </div>

        <div class="set-sec">
          <div class="set-sec-title">视图模式</div>
          <div class="set-modes">
            <button
              v-for="m in VIEW_MODES"
              :key="m"
              :class="['seg-btn', { on: viewMode === m }]"
              @click="setViewMode(m)"
            >{{ VIEW_MODE_LABELS[m] }}</button>
          </div>
        </div>

        <div class="set-sec">
          <div class="set-sec-title">图层</div>
          <label class="toggle">
            <input type="checkbox" v-model="showShade" />
            <span class="sw"></span>山体阴影
          </label>
          <label class="toggle">
            <input type="checkbox" v-model="showGrid" />
            <span class="sw"></span>体素网格
          </label>
        </div>

        <div class="set-sec">
          <div class="set-sec-title">世界</div>
          <div class="set-world">
            <span class="set-seed">种子 {{ params.seed }}</span>
            <button class="btn mini" @click="randomizeSeed">随机种子</button>
          </div>
        </div>

        <div class="set-sec">
          <div class="set-sec-title">操作</div>
          <div class="set-help">
            <span>左键拖拽 平移</span>
            <span>滚轮 缩放</span>
            <span>双击 放大</span>
            <span>点击 选中城市</span>
            <span>ESC 设置</span>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.voxel4x {
  position: fixed;
  inset: 0;
  width: 100%;
  height: 100%;
  background: #000;
  overflow: hidden;
  color: var(--text);
  font-family: 'Inter', 'SF Pro Display', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
}

.main-canvas {
  position: fixed;
  inset: 0;
  display: block;
  cursor: crosshair;
  touch-action: none;
  z-index: 1;
}

.main-canvas.dragging {
  cursor: grabbing;
}

/* 顶栏 */
.top-bar {
  position: fixed;
  top: 12px;
  left: 12px;
  right: 12px;
  z-index: 20;
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  pointer-events: none;
}

.title-section {
  pointer-events: auto;
}

.kicker {
  color: var(--accent);
  font: 10px Consolas, monospace;
  letter-spacing: 0.18em;
  margin-bottom: 4px;
}

.title-section h1 {
  margin: 0;
  font-size: 22px;
  font-weight: 600;
  letter-spacing: -0.02em;
  background: linear-gradient(90deg, var(--accent-bright), var(--accent) 55%, var(--accent-deep));
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
}

.top-actions {
  pointer-events: auto;
  display: flex;
  gap: 10px;
}

/* 视图模式按钮（设置界面内使用） */
.seg-btn {
  background: #101827;
  border: 1px solid var(--line);
  color: var(--dim);
  border-radius: 7px;
  padding: 6px 0;
  font-size: 12px;
  cursor: pointer;
  transition: 150ms ease;
}

.seg-btn:hover {
  color: var(--text);
}

.seg-btn.on {
  background: #16242f;
  color: var(--accent);
  border-color: rgba(143, 208, 198, 0.45);
}

/* 开关（设置界面内使用） */
.toggle {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: #b9c5d6;
  cursor: pointer;
  padding: 3px 0;
}

.toggle input {
  display: none;
}

.toggle .sw {
  width: 30px;
  height: 16px;
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.13);
  position: relative;
  transition: 150ms ease;
  flex: none;
}

.toggle .sw::after {
  content: '';
  position: absolute;
  left: 2px;
  top: 2px;
  width: 12px;
  height: 12px;
  border-radius: 50%;
  background: #9fb0c4;
  transition: 150ms ease;
}

.toggle input:checked + .sw {
  background: rgba(143, 208, 198, 0.4);
}

.toggle input:checked + .sw::after {
  left: 16px;
  background: var(--accent);
}

/* 悬浮提示 */
.hover-tip {
  position: fixed;
  z-index: 40;
  pointer-events: none;
  background: rgba(8, 12, 19, 0.92);
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 6px 10px;
  font-family: Consolas, monospace;
  font-size: 11.5px;
  line-height: 1.6;
  box-shadow: 0 6px 20px rgba(0, 0, 0, 0.5);
  white-space: nowrap;
}

.hover-tip b {
  color: var(--accent);
  font-weight: 600;
  display: block;
  margin-bottom: 2px;
}

.hover-tip span {
  color: #c4d0de;
  margin-right: 8px;
}

/* 右侧面板 */
.right-panel {
  position: fixed;
  right: 12px;
  bottom: 12px;
  z-index: 20;
  display: flex;
  flex-direction: column;
  gap: 10px;
  align-items: flex-end;
  pointer-events: auto;
}

.card {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 13px;
  backdrop-filter: blur(12px);
  box-shadow: 0 12px 36px rgba(0, 0, 0, 0.55);
}

.minimap-card {
  padding: 9px 9px 7px;
}

.minimap-canvas {
  display: block;
  width: 132px;
  height: 132px;
  border-radius: 8px;
  cursor: pointer;
  background: #0a0f16;
  image-rendering: pixelated;
}

.minimap-caption {
  font-size: 10px;
  color: var(--dim);
  text-align: center;
  margin-top: 6px;
  letter-spacing: 0.08em;
}

.legend-card {
  width: 220px;
  padding: 11px 13px 13px;
}

.legend-title {
  font-size: 10px;
  letter-spacing: 0.18em;
  color: var(--dim);
  margin-bottom: 8px;
}

.legend-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 3px 10px;
}

.legend-item {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 11px;
  color: #aebac9;
  cursor: default.
}

.legend-item i {
  width: 11px;
  height: 11px;
  border-radius: 3px;
  flex: none;
  box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.12);
}

.legend-item span {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* 底部状态栏 */
.status-bar {
  position: fixed;
  left: 12px;
  right: 12px;
  bottom: 12px;
  z-index: 20;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 5px 12px;
  font-family: Consolas, monospace;
  font-size: 11px;
  color: #8a9bb0;
  backdrop-filter: blur(8px);
  pointer-events: auto;
}

/* ESC 设置界面 */
.settings-overlay {
  position: fixed;
  inset: 0;
  z-index: 60;
  display: grid;
  place-items: center;
  background: rgba(2, 4, 8, 0.62);
  backdrop-filter: blur(4px);
}

.settings-modal {
  width: min(420px, calc(100vw - 32px));
  max-height: calc(100vh - 48px);
  overflow-y: auto;
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 14px;
  box-shadow: 0 24px 64px rgba(0, 0, 0, 0.6);
  padding: 16px 18px 18px;
}

.set-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 6px;
}

.set-head h3 {
  margin: 4px 0 0;
  font-size: 18px;
  letter-spacing: -0.01em;
}

.set-sec {
  border-top: 1px solid var(--line);
  margin-top: 12px;
  padding-top: 10px;
}

.set-sec-title {
  font-size: 10px;
  letter-spacing: 0.16em;
  color: var(--dim);
  margin-bottom: 8px;
}

.set-modes {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 4px;
}

.set-sec .toggle {
  padding: 5px 0;
}

.set-world {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.set-seed {
  font: 12px Consolas, monospace;
  color: var(--muted);
}

.set-help {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 14px;
  font-size: 11px;
  color: var(--muted);
}

/* 按钮基础 */
.btn {
  border: 1px solid var(--line-strong);
  border-radius: 7px;
  padding: 7px 13px;
  color: var(--text);
  background: color-mix(in srgb, var(--accent) 10%, transparent);
  cursor: pointer;
  font-size: 12px;
  transition: 160ms ease;
}

.btn:hover:not(:disabled) {
  background: color-mix(in srgb, var(--accent) 22%, transparent);
  box-shadow: 0 0 14px color-mix(in srgb, var(--accent) 22%, transparent);
}

.btn:disabled {
  opacity: 0.4;
  cursor: not-allowed.
}

.btn.ghost {
  background: transparent;
  color: var(--muted).
}

.btn.ghost:hover:not(:disabled) {
  color: var(--text);
  background: transparent.
}

/* 响应式 */
@media (max-width: 760px) {
  .right-panel {
    left: 8px;
    right: 8px;
    bottom: 8px;
    flex-direction: row;
    justify-content: space-between;
    align-items: flex-end;
  }
  .legend-card {
    width: auto;
    max-width: 50vw;
  }
  .status-bar {
    left: 8px;
    right: 8px;
    bottom: 8px;
    font-size: 10px;
  }
}

/* ─────────── 游戏层：HUD 与城市面板（阶段 2） ─────────── */
.hud {
  position: fixed;
  top: 12px;
  left: 50%;
  transform: translateX(-50%);
  z-index: 30;
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 7px 12px;
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 10px;
  backdrop-filter: blur(12px);
  box-shadow: 0 10px 28px rgba(0, 0, 0, 0.5);
  pointer-events: auto;
  max-width: calc(100vw - 260px);
  flex-wrap: wrap;
  justify-content: center;
}
.hud-item {
  display: flex;
  align-items: baseline;
  gap: 4px;
  padding: 2px 8px;
  border-radius: 6px;
  background: rgba(255, 255, 255, 0.04);
  white-space: nowrap;
}
.hud-item span {
  font-size: 10px;
  color: var(--muted);
}
.hud-item b {
  font: 12px Consolas, monospace;
  color: var(--text);
}
.hud-item.gold b {
  color: var(--accent-bright);
}
.hud-actions {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-left: 4px;
}
.btn.mini {
  padding: 4px 10px;
  font-size: 11px;
}
.speed-sel {
  background: #141d2b;
  border: 1px solid var(--line);
  border-radius: 6px;
  color: var(--text);
  font-size: 11px;
  padding: 3px 6px;
  outline: none;
  cursor: pointer;
}

/* 城市面板 */
.city-panel {
  position: fixed;
  right: 12px;
  top: 80px;
  bottom: 200px;
  z-index: 30;
  width: 260px;
  display: flex;
  flex-direction: column;
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 12px;
  backdrop-filter: blur(12px);
  box-shadow: 0 12px 36px rgba(0, 0, 0, 0.55);
  overflow: hidden;
}
.cp-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 8px;
  padding: 12px 14px 8px;
}
.cp-head h3 {
  margin: 4px 0 0;
  font-size: 17px;
  letter-spacing: -0.01em;
}
.cp-stat {
  text-align: right;
  display: grid;
  gap: 1px;
}
.cp-stat span {
  font-size: 10px;
  color: var(--muted);
}
.cp-stat b {
  font: 13px Consolas, monospace;
  color: var(--accent-bright);
}
.cp-sec-title {
  padding: 8px 14px 5px;
  font-size: 10px;
  letter-spacing: 0.16em;
  color: var(--dim);
  border-top: 1px solid var(--line);
  margin-top: 6px;
}
.cp-market {
  padding: 2px 14px 6px;
}
.cp-row {
  display: grid;
  grid-template-columns: 44px 48px 1fr 64px;
  align-items: baseline;
  gap: 6px;
  padding: 3px 0;
  font-size: 11px;
  border-bottom: 1px dashed rgba(255, 255, 255, 0.06);
}
.cp-row:last-child {
  border-bottom: 0;
}
.cp-res {
  color: var(--muted);
}
.cp-price {
  font: 11px Consolas, monospace;
  color: var(--accent-bright);
  text-align: right;
}
.cp-stock {
  font: 11px Consolas, monospace;
  color: var(--text);
  text-align: right;
}
.cp-bs {
  font: 10px Consolas, monospace;
  color: var(--dim);
  text-align: right;
}
.cp-buildings {
  padding: 2px 14px 8px;
  max-height: 130px;
  overflow-y: auto;
  display: grid;
  gap: 3px;
}
.cp-b {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 11px;
  padding: 3px 6px;
  border-radius: 5px;
  background: rgba(255, 255, 255, 0.035);
}
.cp-b-name {
  color: var(--text);
}
.cp-b-cell {
  font: 10px Consolas, monospace;
  color: var(--dim);
  margin-left: auto;
}
.cp-progress {
  font: 10px Consolas, monospace;
  color: var(--green);
}
.cp-empty {
  color: var(--dim);
  font-size: 11px;
  padding: 8px 0;
}
.cp-dev {
  padding: 0 14px 10px;
}
</style>