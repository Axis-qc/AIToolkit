// 太空殖民增量游戏 - 类型定义

/** 设施定义 */
export interface FacilityDef {
  id: string
  name: string
  desc: string
  /** 基础造价 */
  baseCost: number
  /** 每次购买后成本增长倍率 */
  costGrowth: number
  /** 每座基础产出（能源/秒） */
  baseRate: number
  /** 累计总产量达到该值才解锁 */
  unlockAt: number
}

/** 升级类型 */
export type UpgradeKind = 'click' | 'facility' | 'global'

/** 升级定义 */
export interface UpgradeDef {
  id: string
  kind: UpgradeKind
  name: string
  desc: string
  /** 首次购买成本 */
  baseCost: number
  /** 每级成本增长倍率 */
  costGrowth: number
  /** 每级增益倍率（乘算） */
  multiplier: number
  /** 关联设施（kind=facility 时必填） */
  facilityId?: string
  /** 解锁所需总产量 */
  unlockAt: number
}

/** 里程碑定义 */
export interface MilestoneDef {
  at: number
  title: string
  desc: string
}

/** 游戏存档 */
export interface GameSave {
  /** 当前能源 */
  energy: number
  /** 历史累计产量 */
  totalProduced: number
  /** 手动点击次数 */
  clicks: number
  /** 各设施拥有数量 */
  facilities: Partial<Record<string, number>>
  /** 各升级当前等级 */
  upgrades: Partial<Record<string, number>>
  /** 上次结算时间戳（离线收益用） */
  lastTick: number
  /** 离线收益累计展示 */
  offlineGain: number
  /** 版本号 */
  v: number
}
