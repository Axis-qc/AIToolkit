/**
 * 无限体素地形噪声引擎
 * 从 map.html 移植并改造为真正无限坐标（支持任意正负值，范围 ±2^30）
 * 纯函数设计，主线程与 Worker 共用，无副作用状态
 */

// ==================== 基础工具 ====================

/** 限制值在 [a, b] 范围内 */
export function clamp(v: number, a: number, b: number): number {
  return v < a ? a : v > b ? b : v
}

/** 平滑步进插值 */
export function sstep(a: number, b: number, x: number): number {
  x = clamp((x - a) / (b - a), 0, 1)
  return x * x * (3 - 2 * x)
}

/** Mulberry32 伪随机数生成器（种子确定性） */
export function mulberry32(seed: number): () => number {
  let a = seed | 0
  return function (): number {
    a = (a + 0x6d2b79f5) | 0
    let t = Math.imul(a ^ (a >>> 15), 1 | a)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

/**
 * 2D 整数哈希，用于逐格色抖动、河流判定等
 * 注意：Math.imul 对负整数天然得到正确的无符号取模结果
 * Math.floor 负数向下取整，& 255 得到 0-255 的正索引
 * 因此 hash2/fbm 对负坐标完全正确，无需额外处理
 */
export function hash2(x: number, y: number): number {
  let h = (Math.imul(x, 374761393) + Math.imul(y, 668265263)) | 0
  h = Math.imul(h ^ (h >>> 13), 1274126177)
  h ^= h >>> 16
  return (h >>> 0) / 4294967296
}

/**
 * Perlin 梯度噪声（按种子洗牌梯度表）
 * 使用标准 256 阶排列表 + 512 长度镜像，支持任意浮点坐标（含负值）
 */
export function makePerlin(seed: number): (x: number, y: number) => number {
  const r = mulberry32(seed)
  const p = new Uint8Array(512)
  const G = new Float32Array(512)
  const t = new Uint8Array(256)

  for (let i = 0; i < 256; i++) t[i] = i
  for (let i = 255; i > 0; i--) {
    const j = (r() * (i + 1)) | 0
    const s = t[i]
    t[i] = t[j]
    t[j] = s
  }
  for (let i = 0; i < 512; i++) p[i] = t[i & 255]
  for (let i = 0; i < 256; i++) {
    const a = r() * 6.283185307179586
    G[i * 2] = Math.cos(a)
    G[i * 2 + 1] = Math.sin(a)
  }

  return function (x: number, y: number): number {
    const X = Math.floor(x)
    const Y = Math.floor(y)
    const xf = x - X
    const yf = y - Y
    const xi = X & 255
    const yi = Y & 255

    const u = xf * xf * xf * (xf * (xf * 6 - 15) + 10)
    const v = yf * yf * yf * (yf * (yf * 6 - 15) + 10)

    const aa = p[p[xi] + yi]
    const ab = p[p[xi] + yi + 1]
    const ba = p[p[xi + 1] + yi]
    const bb = p[p[xi + 1] + yi + 1]

    const d1 = G[aa * 2] * xf + G[aa * 2 + 1] * yf
    const d2 = G[ba * 2] * (xf - 1) + G[ba * 2 + 1] * yf
    const d3 = G[ab * 2] * xf + G[ab * 2 + 1] * (yf - 1)
    const d4 = G[bb * 2] * (xf - 1) + G[bb * 2 + 1] * (yf - 1)

    const a = d1 + u * (d2 - d1)
    const b = d3 + u * (d4 - d3)
    return a + v * (b - a)
  }
}

/** 分形布朗运动 (fBm) 多层叠加 */
export function fbm(
  n: (x: number, y: number) => number,
  x: number,
  y: number,
  octaves: number
): number {
  let a = 0
  let amp = 1
  let f = 1
  let nm = 0
  for (let i = 0; i < octaves; i++) {
    a += n(x * f, y * f) * amp
    nm += amp
    amp *= 0.5
    f *= 2.03
  }
  return a / nm
}

/** 岩脊噪声：取绝对值翻转，产生锐利山脊 */
export function ridged(
  n: (x: number, y: number) => number,
  x: number,
  y: number,
  octaves: number
): number {
  let s = 0
  let amp = 0.62
  let f = 1
  let nm = 0
  for (let i = 0; i < octaves; i++) {
    const v = 1 - Math.abs(n(x * f, y * f))
    s += v * v * amp
    nm += amp
    amp *= 0.48
    f *= 2.05
  }
  return s / nm
}

// ==================== 色带 LUT ====================

/** 从色标点构建 256×3 RGB 查找表 */
function makeLUT(
  stops: Array<[number, number, number, number]>
): Uint8Array {
  const L = new Uint8Array(768)
  for (let i = 0; i < 256; i++) {
    const v = i / 255
    let k = 0
    while (k < stops.length - 2 && v > stops[k + 1][0]) k++
    const a = stops[k]
    const b = stops[k + 1]
    const denom = b[0] - a[0]
    const t = denom !== 0 ? (v - a[0]) / denom : 0
    L[i * 3] = a[1] + (b[1] - a[1]) * t
    L[i * 3 + 1] = a[2] + (b[2] - a[2]) * t
    L[i * 3 + 2] = a[3] + (b[3] - a[3]) * t
  }
  return L
}

/** 从 LUT 采样颜色到输出数组 */
export function lutC(
  L: Uint8Array,
  t: number,
  out: Float32Array | number[]
): void {
  const i = (clamp(t, 0, 1) * 255) | 0
  out[0] = L[i * 3]
  out[1] = L[i * 3 + 1]
  out[2] = L[i * 3 + 2]
}

/** 海深色带：0=浅海 → 1=深渊 */
export const LUT_O = makeLUT([
  [0, 63, 138, 151],
  [0.02, 44, 106, 125],
  [0.09, 28, 74, 99],
  [0.28, 18, 51, 73],
  [0.55, 11, 33, 54],
  [1, 7, 21, 34],
])

/** 高程色带：0=海平面 → 1=高山雪峰 */
export const LUT_H = makeLUT([
  [0, 71, 111, 62],
  [0.03, 111, 138, 70],
  [0.09, 169, 165, 92],
  [0.19, 185, 143, 82],
  [0.31, 154, 111, 69],
  [0.46, 138, 106, 82],
  [0.62, 148, 135, 122],
  [0.83, 207, 207, 201],
  [1, 243, 245, 244],
])

/** 温度色带：0=极寒 → 1=酷热 */
export const LUT_T = makeLUT([
  [0, 58, 63, 125],
  [0.23, 71, 104, 168],
  [0.38, 107, 157, 192],
  [0.49, 159, 198, 196],
  [0.59, 183, 207, 141],
  [0.69, 217, 206, 110],
  [0.79, 216, 160, 90],
  [0.89, 194, 106, 69],
  [1, 150, 56, 62],
])

/** 湿度色带：0=极干 → 1=极湿 */
export const LUT_M = makeLUT([
  [0, 145, 107, 72],
  [0.22, 196, 168, 105],
  [0.42, 163, 169, 92],
  [0.62, 95, 151, 85],
  [0.82, 61, 143, 125],
  [1, 47, 118, 168],
])

// ==================== 生物群系定义 ====================

/** 生物群系名称（22 种，索引 0-21） */
export const BIOME_NAMES: readonly string[] = [
  '深海平原',
  '深海',
  '浅海',
  '大陆架',
  '海冰',
  '河流',
  '海滩',
  '沙漠',
  '荒漠',
  '干旱草原',
  '稀树草原',
  '草原',
  '温带森林',
  '温带雨林',
  '热带雨林',
  '泰加林',
  '灌木苔原',
  '苔原',
  '高山草甸',
  '高山裸岩',
  '高山积雪',
  '极地冰原',
] as const

/** 生物群系 RGB 颜色表（扁平 Uint8Array 兼容 Worker 传输） */
export const BIOME_COLORS = new Uint8Array([
  7, 21, 34, // 0 深海平原
  12, 36, 58, // 1 深海
  21, 61, 85, // 2 浅海
  32, 82, 106, // 3 大陆架
  213, 226, 232, // 4 海冰
  46, 126, 161, // 5 河流
  216, 197, 144, // 6 海滩
  217, 188, 125, // 7 沙漠
  185, 159, 116, // 8 荒漠
  196, 178, 113, // 9 干旱草原
  176, 167, 94, // 10 稀树草原
  152, 160, 82, // 11 草原
  77, 122, 67, // 12 温带森林
  47, 106, 64, // 13 温带雨林
  31, 90, 48, // 14 热带雨林
  61, 96, 69, // 15 泰加林
  112, 128, 92, // 16 灌木苔原
  151, 164, 141, // 17 苔原
  124, 143, 88, // 18 高山草甸
  139, 128, 113, // 19 高山裸岩
  241, 244, 246, // 20 高山积雪
  228, 235, 238, // 21 极地冰原
])

/** 生物群系描述文本 */
export const BIOME_DESCRIPTIONS: readonly string[] = [
  '平均深度逾 4 200 m 的深海盆地',
  '2 400–4 200 m 深海',
  '1 000–2 400 m 开阔陆缘海',
  '1 000 m 以内浅海台地',
  '高纬度封冻海面',
  '蜿蜒的常年地表径流',
  '海陆交界的砂质岸线',
  '极端干旱沙质荒漠',
  '砾石戈壁与荒漠',
  '半干旱灌木草原',
  '热带干旱季节性草原',
  '温带半湿润草甸草原',
  '落叶阔叶林',
  '多雨温带常绿森林',
  '赤道雨林',
  '寒温带针叶林（泰加）',
  '矮灌木与苔原过渡带',
  '极地苔原',
  '林线以上山地草甸',
  '雪线附近风化裸岩',
  '常年积雪高山冰缘',
  '极地大陆冰盖',
] as const

/** 图例显示顺序（索引指向 BIOME_NAMES） */
export const LEGEND_ORDER: readonly number[] = [
  3, 2, 1, 0, 4, 5, 6, 11, 10, 9, 7, 8, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21,
]

// ==================== 地形参数类型 ====================

/** 地形生成参数（可序列化传给 Worker） */
export interface TerrainParams {
  seed: number
  sea: number // 海平面 [0.48, 0.62]
  mount: number // 山脉隆起 [0, 1.2]
  tempBias: number // 全球温度偏移 [-1, 1]
  moistBias: number // 全球湿度偏移 [-1, 1]
  river: number // 河流密度 [0, 1]，≤0.02 视为关闭
}

/** 单格采样返回数据（纯函数，无副作用） */
export interface CellData {
  elevM: number // 海拔（米），负值为海洋
  biomeId: number // 0-21，已包含河流判定（河流优先级最高）
  temp: number // 气温（摄氏度）
  moist: number // 湿度 0-1
}

/** Worker 上下文：持有噪声函数闭包与参数 */
export interface TerrainWorkerCtx {
  params: TerrainParams
  /** 单格完整采样：返回海拔、生物群系、温度、湿度 */
  sampleCell: (wx: number, wy: number, lod: number) => CellData
  /** 仅采海拔（米），用于底图等轻量场景 */
  sampleElevOnly: (wx: number, wy: number, lod: number) => number
}

// ==================== 内部噪声场持有者 ====================

interface NoiseFields {
  nw1: (x: number, y: number) => number
  nw2: (x: number, y: number) => number
  nc: (x: number, y: number) => number
  nm: (x: number, y: number) => number
  nb: (x: number, y: number) => number
  nd: (x: number, y: number) => number
  nt: (x: number, y: number) => number
  nh: (x: number, y: number) => number
  nr: (x: number, y: number) => number
}

/** 由种子构建全部噪声场 */
function buildNoiseFields(seed: number): NoiseFields {
  return {
    nw1: makePerlin(seed ^ 0x9e3779),
    nw2: makePerlin(seed ^ 0x51ab33),
    nc: makePerlin(seed ^ 0x2f13d7),
    nm: makePerlin(seed ^ 0x7f4a3c),
    nb: makePerlin(seed ^ 0x41c6b2),
    nd: makePerlin(seed ^ 0x1b8735),
    nt: makePerlin(seed ^ 0x68e31f),
    nh: makePerlin(seed ^ 0x3ad182),
    nr: makePerlin(seed ^ 0x5c9e07),
  }
}

// ==================== 纬度温度带修正 ====================

function latBelt(a: number): number {
  const s: Array<[number, number]> = [
    [0, 1],
    [18, 0.72],
    [30, -0.62],
    [50, 0.5],
    [68, 0.12],
    [90, -0.45],
  ]
  if (a <= 0) return 1
  for (let i = 1; i < s.length; i++) {
    if (a <= s[i][0]) {
      const p = s[i - 1]
      const q = s[i]
      return p[1] + (q[1] - p[1]) * ((a - p[0]) / (q[0] - p[0]))
    }
  }
  return -0.45
}

// ==================== 核心采样逻辑（纯函数） ====================

/**
 * 海拔归一化 0-1 → 实际海拔米数
 * >0 陆地，<0 海洋
 */
function emFromE(e: number, sea: number): number {
  return e > sea
    ? 4800 * Math.pow((e - sea) / (1 - sea), 1.55)
    : -6200 * Math.pow((sea - e) / sea, 1.35)
}

/**
 * 计算扭曲坐标（用于大陆形变与河流判定）
 * 返回 { qx, qy }，无副作用
 */
function computeDistortedCoords(
  wx: number,
  wy: number,
  nw1: (x: number, y: number) => number,
  nw2: (x: number, y: number) => number,
  lod: number
): { qx: number; qy: number } {
  const wo = lod < 2 ? 2 : 1
  const q1 = fbm(nw1, wx * 0.0026, wy * 0.0026, wo)
  const q2 = fbm(nw2, wx * 0.0026, wy * 0.0026, wo)
  return {
    qx: wx + q1 * 230,
    qy: wy + q2 * 230,
  }
}

/**
 * 采样海拔归一化值 [0,1]（无限坐标，无 WORLD 钳制）
 * 可选接收预计算的扭曲坐标以避免重复计算
 */
function sampleElevNormalized(
  wx: number,
  wy: number,
  lod: number,
  nf: NoiseFields,
  params: TerrainParams,
  distorted?: { qx: number; qy: number }
): number {
  // 无边界限制：map.html 用 WORLD=10000 边缘衰减，这里去掉，世界无限延展
  // 只保留大陆核心噪声与山脉分布

  const { qx, qy } = distorted ?? computeDistortedCoords(wx, wy, nf.nw1, nf.nw2, lod)

  // 大陆基底噪声
  const lodC = lod < 2 ? 5 : lod < 4 ? 4 : 3
  let c = fbm(nf.nc, qx * 0.00046, qy * 0.00046, lodC) * 0.5 + 0.5

  // 没有 WORLD 边界，不做 edge 沉降，改用纯噪声分布
  // 海陆分界由 sea 参数直接钳制
  const mask = sstep(params.sea - 0.03, params.sea + 0.14, c)

  if (mask > 0) {
    // 山脉只在大陆内部隆起，沿褶皱带分布
    const rv = ridged(nf.nm, qx * 0.00115, qy * 0.00115, lod < 2 ? 4 : 3)
    const belt = fbm(nf.nb, qx * 0.0005, qy * 0.0005, 2) * 0.5 + 0.5
    c += Math.pow(rv, 2.6) * 0.52 * params.mount * mask * (0.3 + 0.7 * belt * belt)
  }

  // 海岸侵蚀细节
  c += fbm(nf.nd, wx * 0.013, wy * 0.013, lod < 2 ? 2 : 1) * 0.028

  return clamp(c, 0, 1)
}

/**
 * 计算气候（温度、湿度）——无副作用，返回对象
 */
function computeClimate(
  wx: number,
  wy: number,
  em: number,
  lod: number,
  nf: NoiseFields,
  params: TerrainParams
): { temp: number; moist: number; tS: number } {
  // 纬度：假设 wy=0 为赤道，正北负南（与 map.html 一致）
  // 这里将世界坐标映射为伪纬度：[-1, 1] → [-90°, 90°]
  // 使用固定比例尺：每 10000 格 ≈ 180°，即 1 格 ≈ 0.018°
  const lat = (wy * 0.018) * -1 // 正 y 向南，取反得到北纬正
  const a = Math.abs(lat) / 90

  // 基础海平面温度 + 噪声微扰 + 全球偏移
  const tS = 30 - 60 * Math.pow(a, 1.7) + nf.nt(wx * 0.0021, wy * 0.0021) * 5 + params.tempBias * 14

  // 海拔递减率 6.8°C/km
  const temp = tS - Math.max(0, em) * 0.0068

  // 湿度：纬度带 + 噪声 + 高度干燥效应 + 全球偏移
  const moist = clamp(
    0.52 +
      latBelt(a * 90) * 0.3 +
      fbm(nf.nh, wx * 0.0014, wy * 0.0014, lod < 3 ? 3 : 2) * 0.6 -
      Math.max(0, em - 1000) * 0.00042 +
      params.moistBias * 0.5,
    0,
    1
  )

  return { temp, moist, tS }
}

/**
 * 河流判定：基于扭曲坐标的噪声阈值
 */
function isRiver(
  qx: number,
  qy: number,
  em: number,
  moist: number,
  lod: number,
  nr: (x: number, y: number) => number,
  river: number
): boolean {
  if (river <= 0.02 || em <= 3 || em > 2600 || moist < 0.27) return false
  const t = 0.0035 * (0.25 + 0.75 * river) * (1.6 - em / 2600) * (1 + lod * 0.45)
  return Math.abs(nr(qx * 0.0027, qy * 0.0027)) < t
}

/**
 * Whittaker 简化生物群系分类矩阵
 * 返回 0-21 生物群系 ID（不含河流，河流在上层判定）
 */
function classifyBiome(em: number, temp: number, moist: number): number {
  if (temp < -5) return em < 500 ? 21 : 20
  if (em < 14 && temp > -6) return 6 // 海滩
  if (em > 2700 && temp < 3) return 19
  if (em > 2200 && temp < 9) return 18
  if (temp < -1) return 17
  if (temp < 6) return moist > 0.34 ? 15 : moist > 0.15 ? 16 : 8
  if (temp < 14) return moist > 0.6 ? 13 : moist > 0.36 ? 12 : moist > 0.18 ? 11 : 8
  if (temp < 23) return moist > 0.58 ? 13 : moist > 0.34 ? 12 : moist > 0.17 ? 10 : 7
  return moist > 0.52 ? 14 : moist > 0.3 ? 10 : moist > 0.15 ? 9 : 7
}

/**
 * 单格完整采样（核心纯函数）
 * 一次性计算海拔、扭曲坐标、气候、河流、生物群系
 * 无任何全局副作用状态，完全可并行
 */
function sampleCellInternal(
  wx: number,
  wy: number,
  lod: number,
  nf: NoiseFields,
  params: TerrainParams
): CellData {
  // 1. 计算扭曲坐标（供海拔与河流共用，只算一次）
  const distorted = computeDistortedCoords(wx, wy, nf.nw1, nf.nw2, lod)

  // 2. 采样海拔归一化（复用扭曲坐标）
  const eNorm = sampleElevNormalized(wx, wy, lod, nf, params, distorted)

  // 3. 转实际海拔（米）
  const em = emFromE(eNorm, params.sea)

  // 4. 气候
  const { temp, moist } = computeClimate(wx, wy, em, lod, nf, params)

  // 5. 河流判定（优先级最高，复用扭曲坐标）
  let biomeId: number
  if (isRiver(distorted.qx, distorted.qy, em, moist, lod, nf.nr, params.river)) {
    biomeId = 5 // 河流
  } else {
    // 6. 生物群系分类
    biomeId = classifyBiome(em, temp, moist)
    // 海洋分支细分（按深度/温度）
    if (em <= 0) {
      if (temp < -9) biomeId = 4 // 海冰
      else if (-em > 4200) biomeId = 0 // 深海平原
      else if (-em > 2400) biomeId = 1 // 深海
      else if (-em > 1000) biomeId = 2 // 浅海
      else biomeId = 3 // 大陆架
    }
  }

  return { elevM: em, biomeId, temp, moist }
}

// ==================== 公共导出 API ====================

/**
 * 创建地形 Worker 上下文（在 Worker 内调用一次，随后反复用 sampleCell）
 * 参数 params 会被闭包捕获，后续采样无需再传递
 */
export function makeTerrainWorkerCtx(params: TerrainParams): TerrainWorkerCtx {
  const nf = buildNoiseFields(params.seed)

  const sampleCell = (wx: number, wy: number, lod: number): CellData => {
    return sampleCellInternal(wx, wy, lod, nf, params)
  }

  const sampleElevOnly = (wx: number, wy: number, lod: number): number => {
    const eNorm = sampleElevNormalized(wx, wy, lod, nf, params)
    return emFromE(eNorm, params.sea)
  }

  return { params, sampleCell, sampleElevOnly }
}

/** 默认参数（可作为初始值） */
export const DEFAULT_PARAMS: TerrainParams = {
  seed: Math.floor(Math.random() * 1e9),
  sea: 0.545,
  mount: 0.7,
  tempBias: 0,
  moistBias: 0,
  river: 0.55,
}

/** Chunk 尺寸常量（128×128，与渲染、Worker 协议保持一致） */
export const CHUNK_SIZE = 128
export const CHUNK_SIZE_LOG2 = 7 // 2^7 = 128