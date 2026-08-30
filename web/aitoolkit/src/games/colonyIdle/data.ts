// 太空殖民增量游戏 - 静态数据

import type { FacilityDef, MilestoneDef, UpgradeDef } from './types'

/** 六大殖民设施（产出/秒逐级跃升） */
export const FACILITIES: FacilityDef[] = [
  {
    id: 'solar',
    name: '太阳能阵列',
    desc: '基础能源板，稳定的初始来源',
    baseCost: 15,
    costGrowth: 1.15,
    baseRate: 0.1,
    unlockAt: 0,
  },
  {
    id: 'geo',
    name: '地热站',
    desc: '利用行星核心热量发电',
    baseCost: 120,
    costGrowth: 1.15,
    baseRate: 1,
    unlockAt: 100,
  },
  {
    id: 'orbit',
    name: '轨道太阳能',
    desc: '轨道上的巨型反射镜阵列',
    baseCost: 1300,
    costGrowth: 1.15,
    baseRate: 8,
    unlockAt: 1500,
  },
  {
    id: 'fusion',
    name: '聚变堆',
    desc: '可控核聚变，殖民地能源支柱',
    baseCost: 14000,
    costGrowth: 1.15,
    baseRate: 47,
    unlockAt: 20000,
  },
  {
    id: 'antimatter',
    name: '反物质引擎',
    desc: '正反物质湮灭，能量密度极高',
    baseCost: 170000,
    costGrowth: 1.15,
    baseRate: 260,
    unlockAt: 250000,
  },
  {
    id: 'dyson',
    name: '戴森云',
    desc: '包裹恒星的收集卫星群，终极能源',
    baseCost: 1800000,
    costGrowth: 1.15,
    baseRate: 1400,
    unlockAt: 2600000,
  },
]

/** 升级树 */
export const UPGRADES: UpgradeDef[] = [
  {
    id: 'click_power',
    kind: 'click',
    name: '操控效率',
    desc: '手动采集每次产出 ×2',
    baseCost: 100,
    costGrowth: 5,
    multiplier: 2,
    unlockAt: 0,
  },
  {
    id: 'global_net',
    kind: 'global',
    name: '能量输配网',
    desc: '所有设施产出 ×1.5',
    baseCost: 500,
    costGrowth: 8,
    multiplier: 1.5,
    unlockAt: 1000,
  },
  {
    id: 'up_solar',
    kind: 'facility',
    name: '太阳能增效',
    desc: '太阳能阵列产出 ×2',
    baseCost: 150,
    costGrowth: 4,
    multiplier: 2,
    facilityId: 'solar',
    unlockAt: 200,
  },
  {
    id: 'up_geo',
    kind: 'facility',
    name: '地热增效',
    desc: '地热站产出 ×2',
    baseCost: 1200,
    costGrowth: 4,
    multiplier: 2,
    facilityId: 'geo',
    unlockAt: 3000,
  },
  {
    id: 'up_orbit',
    kind: 'facility',
    name: '轨道增效',
    desc: '轨道太阳能产出 ×2',
    baseCost: 13000,
    costGrowth: 4,
    multiplier: 2,
    facilityId: 'orbit',
    unlockAt: 40000,
  },
  {
    id: 'up_fusion',
    kind: 'facility',
    name: '聚变增效',
    desc: '聚变堆产出 ×2',
    baseCost: 140000,
    costGrowth: 4,
    multiplier: 2,
    facilityId: 'fusion',
    unlockAt: 500000,
  },
  {
    id: 'up_antimatter',
    kind: 'facility',
    name: '反物质增效',
    desc: '反物质引擎产出 ×2',
    baseCost: 1700000,
    costGrowth: 4,
    multiplier: 2,
    facilityId: 'antimatter',
    unlockAt: 6000000,
  },
  {
    id: 'up_dyson',
    kind: 'facility',
    name: '戴森增效',
    desc: '戴森云产出 ×2',
    baseCost: 18000000,
    costGrowth: 4,
    multiplier: 2,
    facilityId: 'dyson',
    unlockAt: 60000000,
  },
]

/** 里程碑（总产量推进提示） */
export const MILESTONES: MilestoneDef[] = [
  { at: 10, title: '殖民地奠基', desc: '殖民地建立，开始采集能源' },
  { at: 100, title: '地热时代', desc: '解锁地热站，产能不再全靠双手' },
  { at: 1500, title: '轨道时代', desc: '解锁轨道太阳能，能源如潮水涌来' },
  { at: 20000, title: '聚变时代', desc: '解锁聚变堆，殖民地步入工业巅峰' },
  { at: 250000, title: '反物质时代', desc: '解锁反物质引擎，能量近乎无限' },
  { at: 2600000, title: '戴森时代', desc: '解锁戴森云，恒星的伟力尽归囊中' },
  { at: 10000000, title: '银河公民', desc: '你的殖民地产能已超亿级' },
]

/** 存档键名 */
export const SAVE_KEY = 'colony-idle-save-v1'

/** 离线收益比例（防空挂） */
export const OFFLINE_RATIO = 0.3
/** 离线收益计入上限（秒） */
export const OFFLINE_CAP = 8 * 3600

export function facilityById(id: string): FacilityDef {
  const f = FACILITIES.find((x) => x.id === id)
  if (!f) throw new Error('unknown facility: ' + id)
  return f
}

export function upgradeById(id: string): UpgradeDef {
  const u = UPGRADES.find((x) => x.id === id)
  if (!u) throw new Error('unknown upgrade: ' + id)
  return u
}

/** 第 level 级（从 0 开始）的成本 */
export function costAt(base: number, growth: number, level: number): number {
  return Math.ceil(base * Math.pow(growth, level))
}

/** 数字格式化：K/M/B/T... */
export function fmt(n: number): string {
  if (!isFinite(n)) return '∞'
  if (n < 0) return '-' + fmt(-n)
  if (n < 1000) return n < 10 ? n.toFixed(1) : Math.floor(n).toString()
  const units = ['K', 'M', 'B', 'T', 'Qa', 'Qi', 'Sx', 'Sp', 'Oc', 'No']
  let u = -1
  let v = n
  while (v >= 1000 && u < units.length - 1) {
    v /= 1000
    u++
  }
  return (v >= 100 ? v.toFixed(0) : v >= 10 ? v.toFixed(1) : v.toFixed(2)) + units[u]
}
