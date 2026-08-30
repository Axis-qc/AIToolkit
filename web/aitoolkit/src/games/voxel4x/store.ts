/**
 * Voxel 4X 阶段 2 核心状态管理
 * 城市模型、市场供需价格、生产建筑、自动开发扩张、tick 循环
 * 使用 Vue reactive，数据结构可序列化（为阶段 4 IndexedDB 做准备）
 */

import { computed, reactive } from 'vue'
import type { ResourceId, BuildingId, ProductionMethod, BuildingDef, CellKey, Cost, UnitId, TechId } from './data'
import {
  RESOURCES,
  RESOURCE_MAP,
  BUILDINGS,
  BUILDING_MAP,
  UNITS,
  TECHS,
  TECH_MAP,
  INITIAL_CITY_CONFIG,
  POPULATION_FOOD_CONSUMPTION_PER_SEC,
  POPULATION_GROWTH_RATE,
  POPULATION_DECLINE_RATE,
  AUTO_DEVELOP_BASE_COOLDOWN,
  AUTO_DEVELOP_DISTANCE_FACTOR,
  PRICE_FORMULA,
  cellKey,
  parseCellKey,
  getNeighborKeys,
} from './data'
import type { TerrainWorkerCtx } from './terrain'

// ==================== 类型定义 ====================

/** 建筑实例：挂在具体格子上 */
export interface BuildingInst {
  defId: BuildingId
  cellKey: CellKey
  methodId: string // 当前生产方式 ID
}

/** 城市独立市场单项 */
export interface MarketItem {
  stock: number // 库存
  buy: number // 买入需求（每秒）
  sell: number // 卖出供给（每秒）
  price: number // 当前价格
  capacity: number // 该资源库存容量
}

/** 城市模型 */
export interface City {
  id: number
  name: string
  x: number // 中心格世界坐标 X
  y: number // 中心格世界坐标 Y
  population: number // 人口（劳动力池）
  foodStock: number // 食物储备（喂人口）
  treasury: number // 金币
  territory: CellKey[] // 已开发格子集合（含中心格）
  buildings: BuildingInst[] // 城内生产建筑
  market: Record<ResourceId, MarketItem> // 独立本地市场
  devQueue: DevTask[] // 开发/建造队列
  lastDevTime: number // 上次开发完成时间戳（用于冷却）
}

/** 开发任务 */
export interface DevTask {
  cellKey: CellKey
  buildingId: BuildingId
  progress: number // 0-1
  startTime: number
}

/** 移动单位 */
export interface Unit {
  id: number
  type: UnitId
  name: string
  x: number
  y: number
  target?: { x: number; y: number }
}

/** 游戏全局状态 */
interface GameState {
  cities: City[]
  selectedCityId: number | null
  selectedUnitId: number | null
  units: Unit[]
  explored: Set<CellKey> // 已探索格子（迷雾）
  science: number // 全局科技点
  researched: TechId[] // 已研究科技
  speed: number // 游戏速度倍率（1 = 正常，0 = 暂停，2 = 2倍等）
  isPaused: boolean
  gameTime: number // 游戏内时间（秒）
  lastTickRealMs: number // 上次 tick 真实时间
}

// ==================== 常量 ====================

/** 基础库存容量 */
const BASE_STOCK_CAPACITY = 500

/** 资源容量加成：粮仓 +500 食物，市场 +1000 所有资源 */
function getResourceCapacity(city: City, resId: ResourceId): number {
  let cap = BASE_STOCK_CAPACITY
  for (const b of city.buildings) {
    const def = BUILDING_MAP[b.defId]
    if (def.isInfrastructure) {
      if (b.defId === 'granary' && resId === 'food') cap += 500
      if (b.defId === 'market') cap += 1000
    }
  }
  return cap
}

/** 获取城市中心格键 */
function getCityCenterKey(city: City): CellKey {
  return cellKey(city.x, city.y)
}

/** 计算两格间的切比雪夫距离（八方向移动） */
function chebyshevDistance(keyA: CellKey, keyB: CellKey): number {
  const a = parseCellKey(keyA)
  const b = parseCellKey(keyB)
  return Math.max(Math.abs(a.wx - b.wx), Math.abs(a.wy - b.wy))
}

// ==================== 响应式状态 ====================

const state = reactive<GameState>({
  cities: [],
  selectedCityId: null,
  selectedUnitId: null,
  units: [],
  explored: new Set<CellKey>(),
  science: 0,
  researched: [],
  speed: 1,
  isPaused: false,
  gameTime: 0,
  lastTickRealMs: Date.now(),
})

// ==================== 辅助函数 ====================

/** 创建空市场项 */
function createEmptyMarketItem(resId: ResourceId, city: City): MarketItem {
  return {
    stock: 0,
    buy: 0,
    sell: 0,
    price: RESOURCE_MAP[resId].basePrice,
    capacity: getResourceCapacity(city, resId),
  }
}

/** 初始化城市市场 */
function initCityMarket(city: City): Record<ResourceId, MarketItem> {
  const market = {} as Record<ResourceId, MarketItem>
  for (const res of RESOURCES) {
    market[res.id] = createEmptyMarketItem(res.id, city)
  }
  // 设置初始库存
  for (const [resId, qty] of Object.entries(INITIAL_CITY_CONFIG.resources)) {
    if (market[resId as ResourceId]) {
      market[resId as ResourceId].stock = qty
    }
  }
  city.foodStock = INITIAL_CITY_CONFIG.foodStock
  return market
}

/** Victoria 3 式价格公式 */
function calculatePrice(basePrice: number, buy: number, sell: number): number {
  const total = buy + sell
  if (total === 0) return basePrice
  const ratio = (buy - sell) / total
  const multiplier = 1 + PRICE_FORMULA.elasticity * ratio
  const clamped = Math.max(PRICE_FORMULA.minMultiplier, Math.min(PRICE_FORMULA.maxMultiplier, multiplier))
  return basePrice * clamped
}

/** 获取建筑当前生产方式 */
function getBuildingMethod(building: BuildingInst): ProductionMethod {
  const def = BUILDING_MAP[building.defId]
  return def.methods.find((m) => m.id === building.methodId) ?? def.methods[0]
}

/** 检查城市是否有足够资源支付成本 */
function canAfford(city: City, cost: Cost): boolean {
  if (cost.money && city.treasury < cost.money) return false
  for (const [resId, amount] of Object.entries(cost.resources)) {
    const rid = resId as ResourceId
    if (city.market[rid].stock < amount) return false
  }
  return true
}

/** 扣除城市资源（金币扣 treasury，其余扣市场库存） */
function deductResources(city: City, cost: Cost): void {
  if (cost.money) city.treasury -= cost.money
  for (const [resId, amount] of Object.entries(cost.resources)) {
    const rid = resId as ResourceId
    city.market[rid].stock -= amount
  }
}

/** 获取地形 worker 上下文（用于自动开发时查询生物群系） */
let terrainCtx: TerrainWorkerCtx | null = null
function getTerrainCtx(): TerrainWorkerCtx | null {
  return terrainCtx
}

export function setTerrainCtx(ctx: TerrainWorkerCtx): void {
  terrainCtx = ctx
}

/** 获取格子生物群系（用于自动开发评分） */
function getBiomeAt(wx: number, wy: number): number {
  if (!terrainCtx) return 11 // 默认草原
  const cell = terrainCtx.sampleCell(wx, wy, 0)
  return cell.biomeId
}

/** 计算建筑对地形的适配度（0-1，越高越适合） */
function getBuildingTerrainSuitability(buildingId: BuildingId, biomeId: number): number {
  const building = BUILDING_MAP[buildingId]
  if (building.allowedBiomes.length === 0) return 0.5 // 无限制
  if (building.allowedBiomes.includes(biomeId)) return 1.0
  return 0
}

/** 自动开发评分：资源缺口权重 + 地形匹配度 */
function scoreDevCandidate(city: City, targetKey: CellKey, buildingId: BuildingId): number {
  const building = BUILDING_MAP[buildingId]
  const target = parseCellKey(targetKey)
  const biomeId = getBiomeAt(target.wx, target.wy)

  // 地形匹配度
  const terrainScore = getBuildingTerrainSuitability(buildingId, biomeId)
  if (terrainScore === 0) return -1 // 完全不适合

  // 资源缺口评分
  let shortageScore = 0
  const method = building.methods[0] // 用默认方式评估

  // 计算城市当前该资源的净产出（产出 - 消耗）
  for (const [resId, output] of Object.entries(method.outputs)) {
    const rid = resId as ResourceId
    const marketItem = city.market[rid]
    // 缺口 = 需求 - 供给，需求包括人口消耗（仅食物）
    let demand = marketItem.buy
    if (rid === 'food') demand += city.population * POPULATION_FOOD_CONSUMPTION_PER_SEC
    const supply = marketItem.sell
    const gap = demand - supply
    if (gap > 0 && output > 0) {
      // 该建筑能缓解这个缺口
      shortageScore += gap * output * 10
    }
  }

  // 距离惩罚：离中心越远分越低
  const centerKey = getCityCenterKey(city)
  const dist = chebyshevDistance(centerKey, targetKey)
  const distancePenalty = dist * 2

  return terrainScore * 100 + shortageScore - distancePenalty
}

/** 根据城市资源缺口选择最优建筑类型 */
function selectBestBuildingForCity(city: City, biomeId: number): BuildingId | null {
  const candidates = BUILDINGS.filter((b) => getBuildingTerrainSuitability(b.id, biomeId) > 0)
  if (candidates.length === 0) return null

  // 计算各资源缺口
  const gaps: Record<ResourceId, number> = {} as Record<ResourceId, number>
  for (const res of RESOURCES) {
    const item = city.market[res.id]
    let demand = item.buy
    if (res.id === 'food') demand += city.population * POPULATION_FOOD_CONSUMPTION_PER_SEC
    const gap = demand - item.sell
    gaps[res.id] = Math.max(0, gap)
  }

  // 优先级：食物 > 木材 > 铁/煤 > 工具 > 布 > 石料
  const priorityOrder: BuildingId[] = ['farm', 'lumber', 'iron_mine', 'coal_mine', 'quarry', 'workshop', 'weaver', 'granary', 'market', 'school']

  for (const bid of priorityOrder) {
    const building = BUILDING_MAP[bid]
    if (!building) continue
    if (getBuildingTerrainSuitability(bid, biomeId) === 0) continue

    // 检查该建筑是否能缓解主要缺口
    const method = building.methods[0]
    for (const [resId, output] of Object.entries(method.outputs)) {
      const rid = resId as ResourceId
      if (gaps[rid] > 0 && output > 0) {
        // 还要检查能否负担开发成本
        if (canAfford(city, building.developCost)) {
          return bid
        }
      }
    }
  }

  // 如果没有缺口导向的建筑，按优先级选第一个能建的
  for (const bid of priorityOrder) {
    if (getBuildingTerrainSuitability(bid, biomeId) > 0 && canAfford(city, BUILDING_MAP[bid].developCost)) {
      return bid
    }
  }

  return null
}

/** 获取城市领土边缘的候选格子 */
function getTerritoryBorderCandidates(city: City): CellKey[] {
  const territorySet = new Set(city.territory)
  const candidates: CellKey[] = []

  for (const key of city.territory) {
    const neighbors = getNeighborKeys(key)
    for (const n of neighbors) {
      if (!territorySet.has(n)) {
        candidates.push(n)
      }
    }
  }

  return [...new Set(candidates)] // 去重
}

// ==================== 核心 Tick 逻辑 ====================

/** 主 tick 函数：每秒调用一次 */
export function tick(dt: number = 1): void {
  if (state.isPaused) return
  if (dt <= 0) return

  const effectiveDt = dt * state.speed

  for (const city of state.cities) {
    tickCity(city, effectiveDt)
  }

  state.gameTime += effectiveDt
  state.lastTickRealMs = Date.now()
}

/** 单城市 tick */
function tickCity(city: City, dt: number): void {
  // 1. 建筑生产
  processBuildingProduction(city, dt)

  // 2. 人口与食物消耗
  processPopulation(city, dt)

  // 3. 市场结算（计算 buy/sell/price）
  processMarket(city)

  // 4. 自动开发扩张
  processAutoDevelop(city, dt)

  // 5. 处理开发队列
  processDevQueue(city, dt)
}

/** 建筑生产：按当前生产方式，输入充足则消耗输入、产出输出 */
function processBuildingProduction(city: City, dt: number): void {
  let totalLabor = 0

  // 先计算总雇佣劳动力
  for (const building of city.buildings) {
    const method = getBuildingMethod(building)
    totalLabor += method.labor
  }

  // 劳动力不足：按比例缩减产出（简单处理：超过人口上限的建筑不生产）
  const laborRatio = totalLabor > city.population ? city.population / totalLabor : 1

  for (const building of city.buildings) {
    const method = getBuildingMethod(building)
    const effectiveLabor = method.labor * laborRatio

    // 检查输入是否充足
    let canProduce = true
    for (const [resId, rate] of Object.entries(method.inputs)) {
      const rid = resId as ResourceId
      const needed = rate * dt * (effectiveLabor / method.labor)
      if (city.market[rid].stock < needed) {
        canProduce = false
        break
      }
    }

    if (!canProduce) continue

    // 消耗输入
    for (const [resId, rate] of Object.entries(method.inputs)) {
      const rid = resId as ResourceId
      const amount = rate * dt * (effectiveLabor / method.labor)
      city.market[rid].stock -= amount
    }

    // 产出输出（受容量限制）
    for (const [resId, rate] of Object.entries(method.outputs)) {
      const rid = resId as ResourceId
      const amount = rate * dt * (effectiveLabor / method.labor)
      const capacity = city.market[rid].capacity
      const newStock = Math.min(capacity, city.market[rid].stock + amount)
      city.market[rid].stock = newStock

      // 食物特殊：同时增加 foodStock（用于人口增长判定）
      if (rid === 'food') {
        city.foodStock = Math.min(capacity, city.foodStock + amount)
      }
    }
  }
}

/** 人口与食物消耗 */
function processPopulation(city: City, dt: number): void {
  const foodNeeded = city.population * POPULATION_FOOD_CONSUMPTION_PER_SEC * dt

  if (city.foodStock >= foodNeeded) {
    // 食物充足：消耗食物，人口增长
    city.foodStock -= foodNeeded
    city.market.food.stock = city.foodStock // 同步
    const growth = city.population * POPULATION_GROWTH_RATE * dt
    city.population += growth
  } else {
    // 食物不足：消耗现有食物，人口小幅下降
    city.foodStock = 0
    city.market.food.stock = 0
    const decline = city.population * POPULATION_DECLINE_RATE * dt
    city.population = Math.max(1, city.population - decline) // 至少保留 1 人口
  }
}

/** 市场结算：计算 buy/sell，更新价格 */
function processMarket(city: City): void {
  // 重置 buy/sell
  for (const res of RESOURCES) {
    city.market[res.id].buy = 0
    city.market[res.id].sell = 0
  }

  // 累加建筑输入需求到 buy，产出到 sell
  for (const building of city.buildings) {
    const method = getBuildingMethod(building)
    for (const [resId, rate] of Object.entries(method.inputs)) {
      city.market[resId as ResourceId].buy += rate
    }
    for (const [resId, rate] of Object.entries(method.outputs)) {
      city.market[resId as ResourceId].sell += rate
    }
  }

  // 人口食物需求
  city.market.food.buy += city.population * POPULATION_FOOD_CONSUMPTION_PER_SEC

  // 库存可卖量（简化：库存 > 0 视为可卖，按库存比例）
  for (const res of RESOURCES) {
    const item = city.market[res.id]
    if (item.stock > 0) {
      item.sell += item.stock * 0.1 // 每秒最多卖出库存 10%
    }
  }

  // 更新价格
  for (const res of RESOURCES) {
    const item = city.market[res.id]
    item.price = calculatePrice(res.basePrice, item.buy, item.sell)
    // 更新容量（建筑可能变动）
    item.capacity = getResourceCapacity(city, res.id)
    // 钳制库存不超过容量
    if (item.stock > item.capacity) item.stock = item.capacity
  }
}

/** 自动开发扩张：检查边缘格，评分选最优，扣费计时 */
function processAutoDevelop(city: City, dt: number): void {
  // 冷却检查
  const now = Date.now()
  const centerKey = getCityCenterKey(city)
  const lastDevDist = city.devQueue.length > 0 ? chebyshevDistance(centerKey, city.devQueue[city.devQueue.length - 1].cellKey) : 0
  const cooldown = AUTO_DEVELOP_BASE_COOLDOWN + lastDevDist * AUTO_DEVELOP_DISTANCE_FACTOR
  if ((now - city.lastDevTime) / 1000 < cooldown) return

  // 获取边缘候选格
  const candidates = getTerritoryBorderCandidates(city)
  if (candidates.length === 0) return

  // 为每个候选格选最优建筑并评分
  let bestCandidate: { key: CellKey; buildingId: BuildingId; score: number } | null = null

  for (const key of candidates) {
    const target = parseCellKey(key)
    const biomeId = getBiomeAt(target.wx, target.wy)
    const buildingId = selectBestBuildingForCity(city, biomeId)
    if (!buildingId) continue

    const score = scoreDevCandidate(city, key, buildingId)
    if (score > 0 && (!bestCandidate || score > bestCandidate.score)) {
      bestCandidate = { key, buildingId, score }
    }
  }

  if (!bestCandidate) return

  // 扣除开发成本
  const buildingDef = BUILDING_MAP[bestCandidate.buildingId]
  if (!canAfford(city, buildingDef.developCost)) return

  deductResources(city, buildingDef.developCost)

  // 创建开发任务
  const task: DevTask = {
    cellKey: bestCandidate.key,
    buildingId: bestCandidate.buildingId,
    progress: 0,
    startTime: now,
  }
  city.devQueue.push(task)
  city.lastDevTime = now
}

/** 处理开发队列：进度推进，完成则纳入领土并建建筑 */
function processDevQueue(city: City, dt: number): void {
  const completed: DevTask[] = []

  for (const task of city.devQueue) {
    const buildingDef = BUILDING_MAP[task.buildingId]
    task.progress += dt / buildingDef.developTime

    if (task.progress >= 1) {
      completed.push(task)
    }
  }

  // 完成的任务：纳入领土，建建筑
  for (const task of completed) {
    const idx = city.devQueue.indexOf(task)
    if (idx >= 0) city.devQueue.splice(idx, 1)

    // 加入领土
    if (!city.territory.includes(task.cellKey)) {
      city.territory.push(task.cellKey)
    }

    // 建建筑（默认第一种生产方式）
    const buildingDef = BUILDING_MAP[task.buildingId]
    const inst: BuildingInst = {
      defId: task.buildingId,
      cellKey: task.cellKey,
      methodId: buildingDef.methods[0].id,
    }
    city.buildings.push(inst)
  }
}

// ==================== 公共 API ====================

/** 开始新游戏：在 (0,0) 附近建首城 */
export function startNewGame(): void {
  state.cities = []
  state.selectedCityId = null
  state.speed = 1
  state.isPaused = false
  state.gameTime = 0
  state.lastTickRealMs = Date.now()

  const city: City = {
    id: 1,
    name: '初始之地',
    x: 0,
    y: 0,
    population: INITIAL_CITY_CONFIG.population,
    foodStock: INITIAL_CITY_CONFIG.foodStock,
    treasury: INITIAL_CITY_CONFIG.treasury,
    territory: [cellKey(0, 0)], // 中心格自动算作领土
    buildings: [],
    market: {} as Record<ResourceId, MarketItem>,
    devQueue: [],
    lastDevTime: 0,
  }

  city.market = initCityMarket(city)
  state.cities.push(city)
  state.selectedCityId = city.id
}

/** 选中城市 */
export function selectCity(id: number): void {
  const city = state.cities.find((c) => c.id === id)
  if (city) state.selectedCityId = id
}

/** 获取当前选中城市 */
export function getSelectedCity(): City | null {
  if (state.selectedCityId === null) return null
  return state.cities.find((c) => c.id === state.selectedCityId) ?? null
}

/** 切换暂停 */
export function togglePause(): void {
  state.isPaused = !state.isPaused
}

/** 设置游戏速度 */
export function setSpeed(speed: number): void {
  state.speed = Math.max(0, Math.min(4, speed))
}

// ==================== 派生状态（HUD 用） ====================

/** 选中城市 */
export const selectedCity = computed(() => getSelectedCity())

/** 所有城市总资源汇总（HUD 显示用） */
export const totalResources = computed(() => {
  const totals: Record<ResourceId, number> = {} as Record<ResourceId, number>
  for (const res of RESOURCES) totals[res.id] = 0
  let totalGold = 0
  let totalPopulation = 0

  for (const city of state.cities) {
    for (const res of RESOURCES) {
      totals[res.id] += city.market[res.id].stock
    }
    totalGold += city.treasury
    totalPopulation += Math.floor(city.population)
  }

  return { ...totals, money: totalGold, population: totalPopulation }
})

/** 城市列表（用于城市选择下拉等） */
export const cityList = computed(() => state.cities.map((c) => ({ id: c.id, name: c.name, population: Math.floor(c.population) })))

// ==================== 导出响应式状态 ====================

export const gameState = state