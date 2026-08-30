// 太空殖民增量游戏 - 状态管理

import { computed, reactive } from 'vue'
import type { GameSave, MilestoneDef } from './types'
import {
  FACILITIES,
  UPGRADES,
  MILESTONES,
  SAVE_KEY,
  OFFLINE_RATIO,
  OFFLINE_CAP,
  costAt,
  facilityById,
  upgradeById,
} from './data'

const state = reactive<{ save: GameSave | null }>({ save: null })

function freshSave(): GameSave {
  return {
    energy: 0,
    totalProduced: 0,
    clicks: 0,
    facilities: {},
    upgrades: {},
    lastTick: Date.now(),
    offlineGain: 0,
    v: 1,
  }
}

function persist(): void {
  if (!state.save) return
  try {
    localStorage.setItem(SAVE_KEY, JSON.stringify(state.save))
  } catch {
    /* 存档失败不影响游戏 */
  }
}

/** 自动存档最小间隔（5 分钟） */
const AUTO_SAVE_MS = 5 * 60 * 1000
let lastAutoSave = 0

/** 强制立即存档（离开页面等兜底场景） */
export function persistNow(): void {
  lastAutoSave = Date.now()
  persist()
}

// ─── 数值计算 ────────────────────────────────────────────────

/** 各设施当前等级加成倍率 */
function facilityMultiplier(id: string, save: GameSave): number {
  const up = UPGRADES.find((u) => u.kind === 'facility' && u.facilityId === id)
  if (!up) return 1
  const lvl = save.upgrades[up.id] ?? 0
  return Math.pow(up.multiplier, lvl)
}

/** 全局产出倍率 */
function globalMultiplier(save: GameSave): number {
  const up = UPGRADES.find((u) => u.kind === 'global')
  if (!up) return 1
  const lvl = save.upgrades[up.id] ?? 0
  return Math.pow(up.multiplier, lvl)
}

/** 每秒自动产出 */
export function ratePerSec(save: GameSave): number {
  let rate = 0
  for (const f of FACILITIES) {
    const count = save.facilities[f.id] ?? 0
    if (count <= 0) continue
    rate += f.baseRate * count * facilityMultiplier(f.id, save)
  }
  return rate * globalMultiplier(save)
}

/** 单次点击产出 */
export function clickPower(save: GameSave): number {
  const up = UPGRADES.find((u) => u.kind === 'click')
  if (!up) return 1
  const lvl = save.upgrades[up.id] ?? 0
  return Math.pow(up.multiplier, lvl)
}

/** 某设施当前单价 */
export function facilityCost(save: GameSave, id: string): number {
  const f = facilityById(id)
  const owned = save.facilities[id] ?? 0
  return costAt(f.baseCost, f.costGrowth, owned)
}

/** 某升级当前（下一级）成本 */
export function upgradeCost(save: GameSave, id: string): number {
  const u = upgradeById(id)
  const lvl = save.upgrades[id] ?? 0
  return costAt(u.baseCost, u.costGrowth, lvl)
}

// ─── 派生状态 ────────────────────────────────────────────────

export const save = computed(() => state.save)
export const hasSave = computed(() => state.save !== null)

export const rate = computed(() => (state.save ? ratePerSec(state.save) : 0))
export const clickPow = computed(() => (state.save ? clickPower(state.save) : 1))
export const totalProduced = computed(() => state.save?.totalProduced ?? 0)

/** 已解锁设施列表（含数量/单价/是否可买） */
export const facilityRows = computed(() => {
  if (!state.save) return []
  const s = state.save
  return FACILITIES.filter((f) => s.totalProduced >= f.unlockAt).map((f) => {
    const owned = s.facilities[f.id] ?? 0
    const cost = facilityCost(s, f.id)
    const mult = facilityMultiplier(f.id, s)
    return {
      ...f,
      owned,
      cost,
      output: f.baseRate * owned * mult,
      affordable: s.energy >= cost,
      canBuy: owned >= 0,
    }
  })
})

/** 可购买升级列表（已解锁 + 未满级概念：无限等级） */
export const upgradeRows = computed(() => {
  if (!state.save) return []
  const s = state.save
  return UPGRADES.filter((u) => s.totalProduced >= u.unlockAt).map((u) => {
    const lvl = s.upgrades[u.id] ?? 0
    const cost = upgradeCost(s, u.id)
    return {
      ...u,
      lvl,
      cost,
      affordable: s.energy >= cost,
    }
  })
})

/** 已达成里程碑 */
export const reachedMilestones = computed<MilestoneDef[]>(() => {
  const t = totalProduced.value
  return MILESTONES.filter((m) => t >= m.at)
})

/** 下一个未达成里程碑 */
export const nextMilestone = computed<MilestoneDef | null>(() => {
  const t = totalProduced.value
  return MILESTONES.find((m) => t < m.at) ?? null
})

// ─── 操作 ────────────────────────────────────────────────────

export function loadGame(): boolean {
  try {
    const raw = localStorage.getItem(SAVE_KEY)
    if (!raw) return false
    const parsed = JSON.parse(raw) as GameSave
    if (!parsed || typeof parsed.energy !== 'number') return false
    state.save = parsed
    // 离线收益
    const elapsed = Math.min(Math.max(0, (Date.now() - (parsed.lastTick ?? Date.now())) / 1000), OFFLINE_CAP)
    if (elapsed > 1) {
      const gain = ratePerSec(parsed) * elapsed * OFFLINE_RATIO
      if (gain > 0) {
        parsed.energy += gain
        parsed.totalProduced += gain
        parsed.offlineGain = gain
      }
    }
    parsed.lastTick = Date.now()
    persist()
    return true
  } catch {
    return false
  }
}

export function startNewGame(): void {
  state.save = freshSave()
  persist()
}

export function resetGame(): void {
  try {
    localStorage.removeItem(SAVE_KEY)
  } catch {
    /* ignore */
  }
  state.save = freshSave()
}

/** 手动采集 */
export function clickMine(): void {
  if (!state.save) return
  const s = state.save
  const p = clickPower(s)
  s.energy += p
  s.totalProduced += p
  s.clicks += 1
  persist()
}

/** 建造/购买设施 */
export function buyFacility(id: string): string {
  if (!state.save) return '尚未开始'
  const s = state.save
  const f = facilityById(id)
  const cost = facilityCost(s, id)
  if (s.energy < cost) return '能源不足'
  s.energy -= cost
  s.facilities[id] = (s.facilities[id] ?? 0) + 1
  persist()
  return ''
}

/** 购买升级 */
export function buyUpgrade(id: string): string {
  if (!state.save) return '尚未开始'
  const s = state.save
  const u = upgradeById(id)
  const cost = upgradeCost(s, id)
  if (s.energy < cost) return '能源不足'
  s.energy -= cost
  s.upgrades[id] = (s.upgrades[id] ?? 0) + 1
  persist()
  return ''
}

/** 每秒推进（由组件驱动，传入真实流逝秒数） */
export function tick(elapsed: number): void {
  if (!state.save) return
  if (elapsed <= 0) return
  const s = state.save
  const gain = ratePerSec(s) * elapsed
  s.energy += gain
  s.totalProduced += gain
  s.lastTick = Date.now()
  // 节流自动存档（5 分钟一次）
  if (Date.now() - lastAutoSave >= AUTO_SAVE_MS) {
    lastAutoSave = Date.now()
    persist()
  }
}
