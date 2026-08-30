/// <reference lib="webworker" />

import {
  makeTerrainWorkerCtx,
  type TerrainParams,
  type TerrainWorkerCtx,
  CHUNK_SIZE,
  CHUNK_SIZE_LOG2,
  clamp,
} from './terrain'

/** Worker 消息类型定义 */
interface ChunkRequest {
  id: number
  tx: number // chunk 瓦片 X（区块 = CHUNK_SIZE × CHUNK_SIZE 个方块）
  ty: number // chunk 瓦片 Y
  params: TerrainParams
}

interface BaseRequest {
  id: number
  cmd: 'base'
  params: TerrainParams
}

type WorkerRequest = ChunkRequest | BaseRequest

interface ChunkResponse {
  id: number
  tx: number
  ty: number
  elev: Int16Array // 海拔米数 ×5 存储（Int16 范围 ±32767，32767 表示无效）
  biome: Uint8Array // 生物群系 ID 0-21
  temp: Int16Array // 温度 ×10 存储（摄氏度）
  moist: Uint8Array // 湿度 0-100 存储
}

interface BaseResponse {
  id: number
  cmd: 'base'
  elev: Int16Array // 128×128
  biome: Uint8Array // 128×128
}

type WorkerResponse = ChunkResponse | BaseResponse

/** 当前激活的上下文 */
let ctx: TerrainWorkerCtx | null = null

/** 计算 chunk 世界坐标原点（区块 = CHUNK_SIZE 个方块，1 方块 = 1 世界单位） */
function getChunkOrigin(tx: number, ty: number): { x0: number; y0: number } {
  return {
    x0: tx * CHUNK_SIZE,
    y0: ty * CHUNK_SIZE,
  }
}

/** 生成单个 chunk 的完整数据（方块级精度，每格采样 1 个方块中心） */
function generateChunk(
  tx: number,
  ty: number,
  params: TerrainParams
): ChunkResponse {
  if (!ctx || ctx.params.seed !== params.seed) {
    ctx = makeTerrainWorkerCtx(params)
  }

  const { x0, y0 } = getChunkOrigin(tx, ty)

  const total = CHUNK_SIZE * CHUNK_SIZE
  const elev = new Int16Array(total)
  const biome = new Uint8Array(total)
  const temp = new Int16Array(total)
  const moist = new Uint8Array(total)

  for (let j = 0; j < CHUNK_SIZE; j++) {
    // 方块中心世界坐标（1 方块 = 1 世界单位）
    const wy = y0 + j + 0.5
    const baseIdx = j * CHUNK_SIZE

    for (let i = 0; i < CHUNK_SIZE; i++) {
      const wx = x0 + i + 0.5
      const idx = baseIdx + i

      const cell = ctx.sampleCell(wx, wy, 0)

      // 海拔：米 ×5 存入 Int16（±6553 米范围，足够）
      // 32767 保留给无效标记
      const elevScaled = Math.round(cell.elevM * 5)
      elev[idx] = clampInt16(elevScaled)

      // 生物群系
      biome[idx] = cell.biomeId

      // 温度 ×10
      temp[idx] = Math.round(cell.temp * 10)

      // 湿度 0-100
      moist[idx] = Math.round(clamp(cell.moist, 0, 1) * 100)
    }
  }

  return { id: 0, tx, ty, elev, biome, temp, moist }
}

/** 生成全球底图（128×128，低 LOD 概览） */
function generateBase(params: TerrainParams): BaseResponse {
  if (!ctx || ctx.params.seed !== params.seed) {
    ctx = makeTerrainWorkerCtx(params)
  }

  const BASE_SIZE = 128
  const total = BASE_SIZE * BASE_SIZE
  const elev = new Int16Array(total)
  const biome = new Uint8Array(total)

  // 底图采样：覆盖 ±2^30 范围的中心区域，用较大 step
  // 这里采用固定步长，覆盖约 ±800万 世界单位（足够概览）
  const WORLD_VIEW_RADIUS = 8_000_000
  const step = (WORLD_VIEW_RADIUS * 2) / BASE_SIZE

  for (let j = 0; j < BASE_SIZE; j++) {
    const wy = -WORLD_VIEW_RADIUS + (j + 0.5) * step
    const baseIdx = j * BASE_SIZE

    for (let i = 0; i < BASE_SIZE; i++) {
      const wx = -WORLD_VIEW_RADIUS + (i + 0.5) * step
      const idx = baseIdx + i

      // 底图用低 LOD (5) 加速
      const cell = ctx.sampleCell(wx, wy, 5)

      elev[idx] = clampInt16(Math.round(cell.elevM * 5))
      biome[idx] = cell.biomeId
    }
  }

  return { id: 0, cmd: 'base', elev, biome }
}

/** Int16 安全钳制 */
function clampInt16(v: number): number {
  return v < -32768 ? -32768 : v > 32767 ? 32767 : v
}

/** 消息处理主循环 */
self.onmessage = (e: MessageEvent<WorkerRequest>) => {
  const msg = e.data

  // 用 in 操作符做类型收窄：BaseRequest 有 cmd 字段，ChunkRequest 没有
  if ('cmd' in msg) {
    const resp = generateBase(msg.params)
    resp.id = msg.id
    // Transferable 传输：只传 buffer，零拷贝
    self.postMessage(resp, [resp.elev.buffer, resp.biome.buffer])
    return
  }

  // 普通 chunk 请求（此时 msg 已收窄为 ChunkRequest）
  const resp = generateChunk(msg.tx, msg.ty, msg.params)
  resp.id = msg.id
  self.postMessage(resp, [
    resp.elev.buffer,
    resp.biome.buffer,
    resp.temp.buffer,
    resp.moist.buffer,
  ])
}

// 类型导出供主线程 import type 使用
export type { WorkerRequest, WorkerResponse, ChunkRequest, ChunkResponse, BaseRequest, BaseResponse }