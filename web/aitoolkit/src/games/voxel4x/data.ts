/**
 * Voxel 4X 阶段 2 静态数据
 * 资源表、建筑表、地形要求映射、初始配置
 * 所有数据可序列化，供 store 与 UI 使用
 */

/** 生物群系 ID（terrain.ts 中 biomeId 为 number，这里本地定义避免循环依赖） */
type BiomeId = number

// ==================== 资源系统 ====================

/** 可交易资源 ID（不含 money、science、labor） */
export type ResourceId =
  | 'food'
  | 'wood'
  | 'stone'
  | 'iron'
  | 'coal'
  | 'tools'
  | 'cloth'

/** 资源定义 */
export interface ResourceDef {
  id: ResourceId
  name: string
  basePrice: number
}

/** 资源表：id、中文名、基价 */
export const RESOURCES: ResourceDef[] = [
  { id: 'food', name: '食物', basePrice: 1.0 },
  { id: 'wood', name: '木材', basePrice: 1.2 },
  { id: 'stone', name: '石料', basePrice: 0.8 },
  { id: 'iron', name: '铁', basePrice: 2.0 },
  { id: 'coal', name: '煤', basePrice: 1.5 },
  { id: 'tools', name: '工具', basePrice: 6.0 },
  { id: 'cloth', name: '布', basePrice: 5.0 },
]

/** 资源 ID -> 定义的快速查找 */
export const RESOURCE_MAP: Record<ResourceId, ResourceDef> = Object.fromEntries(
  RESOURCES.map((r) => [r.id, r])
) as Record<ResourceId, ResourceDef>

/** 按 ID 获取资源定义 */
export function getResource(id: ResourceId): ResourceDef {
  return RESOURCE_MAP[id]
}

/** 所有可交易资源 ID 列表 */
export const ALL_RESOURCE_IDS: ResourceId[] = RESOURCES.map((r) => r.id)

// ==================== 生产方式 ====================

/** 生产方式：输入/输出/雇佣人数/名称/描述 */
export interface ProductionMethod {
  id: string
  name: string
  desc: string
  /** 每秒输入：资源 -> 数量/秒 */
  inputs: Partial<Record<ResourceId, number>>
  /** 每秒输出：资源 -> 数量/秒 */
  outputs: Partial<Record<ResourceId, number>>
  /** 雇佣劳动力数量 */
  labor: number
  /** 每秒科技点产出（学校等知识建筑） */
  scienceOutput?: number
}

// ==================== 建筑系统 ====================

/** 建筑 ID */
export type BuildingId =
  | 'farm'
  | 'lumber'
  | 'quarry'
  | 'iron_mine'
  | 'coal_mine'
  | 'workshop'
  | 'weaver'
  | 'granary'
  | 'market'
  | 'school'

/** 成本定义：可交易资源 + 金币 */
export interface Cost {
  resources: Partial<Record<ResourceId, number>>
  money?: number
}

/** 建筑定义 */
export interface BuildingDef {
  id: BuildingId
  name: string
  desc: string
  /** 允许建造的生物群系 ID 集合（空 = 无地形限制） */
  allowedBiomes: BiomeId[]
  /** 生产方式列表（默认第一个为初始方式） */
  methods: ProductionMethod[]
  /** 建造消耗（一次性） */
  buildCost: Cost
  /** 开发消耗（城市自动开发该格时额外消耗） */
  developCost: Cost
  /** 开发耗时（秒） */
  developTime: number
  /** 是否为基础设施（不产出资源，提供容量/效率加成） */
  isInfrastructure?: boolean
}

/**
 * 生物群系 ID 到地形类型的映射说明（参考 terrain.ts 的 BIOME_NAMES）：
 * 0-4: 海洋类（深海平原/深海/浅海/大陆架/海冰）-> 不可建造生产建筑
 * 5: 河流 -> 可建农场（灌溉）
 * 6: 海滩 -> 可建伐木场（沿海林）
 * 7: 沙漠 / 8: 荒漠 / 9: 干旱草原 / 10: 稀树草原 -> 仅限特定建筑
 * 11: 草原 -> 肥沃（农场）
 * 12: 温带森林 / 13: 温带雨林 / 14: 热带雨林 / 15: 泰加林 -> 森林（伐木场）
 * 16: 灌木苔原 / 17: 苔原 -> 边缘地带
 * 18: 高山草甸 -> 肥沃（农场）
 * 19: 高山裸岩 / 20: 高山积雪 / 21: 极地冰原 -> 山地（矿场）
 *
 * 映射规则：
 * - 肥沃地（农场）：6(海滩), 11(草原), 18(高山草甸)
 * - 森林（伐木场）：12(温带森林), 13(温带雨林), 14(热带雨林), 15(泰加林)
 * - 山地/岩石（采石场/铁矿/煤矿）：19(高山裸岩), 20(高山积雪), 21(极地冰原)
 * - 平坦（工坊/织布坊/粮仓/市场/学校）：11(草原), 12(温带森林), 13(温带雨林), 18(高山草甸)
 * - 河流（农场灌溉加成）：5(河流)
 */

// 肥沃地生物群系：适合农场
const FERTILE_BIOMES: BiomeId[] = [6, 11, 18]
// 森林生物群系：适合伐木场
const FOREST_BIOMES: BiomeId[] = [12, 13, 14, 15]
// 山地生物群系：适合矿场
const MOUNTAIN_BIOMES: BiomeId[] = [19, 20, 21]
// 平坦生物群系：适合基础设施与加工建筑
const FLAT_BIOMES: BiomeId[] = [11, 12, 13, 18]
// 河流：农场灌溉
const RIVER_BIOME: BiomeId[] = [5]

/** 建筑表 */
export const BUILDINGS: BuildingDef[] = [
  {
    id: 'farm',
    name: '农场',
    desc: '生产食物，养活人口。肥沃地产出高，河流格额外加成。',
    allowedBiomes: [...FERTILE_BIOMES, ...RIVER_BIOME],
    methods: [
      {
        id: 'extensive',
        name: '粗放耕作',
        desc: '低投入低产出，每工 1 食物/秒',
        inputs: {},
        outputs: { food: 1 },
        labor: 1,
      },
      {
        id: 'intensive',
        name: '集约耕作',
        desc: '高投入高产出，每 3 工 3 食物/秒（需工具）',
        inputs: { tools: 0.5 },
        outputs: { food: 3 },
        labor: 3,
      },
    ],
    buildCost: { resources: { wood: 20, stone: 10 } },
    developCost: { resources: { wood: 10, stone: 5 }, money: 20 },
    developTime: 3,
  },
  {
    id: 'lumber',
    name: '伐木场',
    desc: '砍伐木材。仅限森林地形。',
    allowedBiomes: FOREST_BIOMES,
    methods: [
      {
        id: 'normal',
        name: '普通采伐',
        desc: '标准采伐，每工 1 木材/秒',
        inputs: {},
        outputs: { wood: 1 },
        labor: 1,
      },
      {
        id: 'rotation',
        name: '轮伐制',
        desc: '可持续采伐，产出提升但需维护（消耗工具）',
        inputs: { tools: 0.3 },
        outputs: { wood: 2 },
        labor: 2,
      },
    ],
    buildCost: { resources: { wood: 15, stone: 5 } },
    developCost: { resources: { wood: 10 }, money: 15 },
    developTime: 3,
  },
  {
    id: 'quarry',
    name: '采石场',
    desc: '开采石料。山地/岩石地形。',
    allowedBiomes: MOUNTAIN_BIOMES,
    methods: [
      {
        id: 'normal',
        name: '普通开采',
        desc: '基础开采，每工 1 石料/秒',
        inputs: {},
        outputs: { stone: 1 },
        labor: 1,
      },
      {
        id: 'blasting',
        name: '爆破开采',
        desc: '使用炸药提高产出，消耗煤',
        inputs: { coal: 0.5 },
        outputs: { stone: 3 },
        labor: 2,
      },
    ],
    buildCost: { resources: { wood: 10, stone: 20, iron: 5 } },
    developCost: { resources: { wood: 10, stone: 10 }, money: 25 },
    developTime: 4,
  },
  {
    id: 'iron_mine',
    name: '铁矿',
    desc: '开采铁矿。山地含矿带。',
    allowedBiomes: MOUNTAIN_BIOMES,
    methods: [
      {
        id: 'normal',
        name: '普通开采',
        desc: '基础开采，每工 0.5 铁/秒',
        inputs: {},
        outputs: { iron: 0.5 },
        labor: 1,
      },
      {
        id: 'deep',
        name: '深井开采',
        desc: '深部开采，产出翻倍但消耗煤',
        inputs: { coal: 0.5 },
        outputs: { iron: 1.5 },
        labor: 2,
      },
    ],
    buildCost: { resources: { wood: 20, stone: 30, iron: 10 } },
    developCost: { resources: { wood: 15, stone: 15, iron: 5 }, money: 40 },
    developTime: 5,
  },
  {
    id: 'coal_mine',
    name: '煤矿',
    desc: '开采煤炭。山地。工业革命核心资源。',
    allowedBiomes: MOUNTAIN_BIOMES,
    methods: [
      {
        id: 'normal',
        name: '普通开采',
        desc: '基础开采，每工 0.8 煤/秒',
        inputs: {},
        outputs: { coal: 0.8 },
        labor: 1,
      },
      {
        id: 'deep',
        name: '深井开采',
        desc: '深部开采，产出翻倍',
        inputs: { tools: 0.2 },
        outputs: { coal: 2 },
        labor: 2,
      },
    ],
    buildCost: { resources: { wood: 20, stone: 20, iron: 5 } },
    developCost: { resources: { wood: 15, stone: 15 }, money: 35 },
    developTime: 5,
  },
  {
    id: 'workshop',
    name: '工坊',
    desc: '加工木材+铁=工具。平坦地。',
    allowedBiomes: FLAT_BIOMES,
    methods: [
      {
        id: 'hand',
        name: '手工制作',
        desc: '消耗木材+铁生产工具，每 3 工 1 工具/秒',
        inputs: { wood: 2, iron: 1 },
        outputs: { tools: 1 },
        labor: 3,
      },
      {
        id: 'mechanical',
        name: '机械加工',
        desc: '蒸汽动力，效率翻倍但消耗煤',
        inputs: { wood: 3, iron: 2, coal: 1 },
        outputs: { tools: 3 },
        labor: 4,
      },
    ],
    buildCost: { resources: { wood: 30, stone: 20, iron: 15 } },
    developCost: { resources: { wood: 20, stone: 15, iron: 10 }, money: 50 },
    developTime: 4,
  },
  {
    id: 'weaver',
    name: '织布坊',
    desc: '生产布。平坦地。（简化：仅消耗劳动力）',
    allowedBiomes: FLAT_BIOMES,
    methods: [
      {
        id: 'hand',
        name: '手工织布',
        desc: '传统手工，每 2 工 1 布/秒',
        inputs: {},
        outputs: { cloth: 1 },
        labor: 2,
      },
      {
        id: 'mechanical',
        name: '机械织布',
        desc: '动力织布机，产出翻倍消耗煤',
        inputs: { coal: 0.5 },
        outputs: { cloth: 3 },
        labor: 3,
      },
    ],
    buildCost: { resources: { wood: 20, stone: 10 } },
    developCost: { resources: { wood: 15, stone: 10 }, money: 30 },
    developTime: 4,
  },
  {
    id: 'granary',
    name: '粮仓',
    desc: '增加城市食物储存上限，防止浪费。平坦地。',
    allowedBiomes: FLAT_BIOMES,
    methods: [
      {
        id: 'basic',
        name: '基础粮仓',
        desc: '食物容量 +500',
        inputs: {},
        outputs: {},
        labor: 0,
      },
    ],
    buildCost: { resources: { wood: 30, stone: 20 } },
    developCost: { resources: { wood: 20, stone: 15 }, money: 30 },
    developTime: 3,
    isInfrastructure: true,
  },
  {
    id: 'market',
    name: '市场',
    desc: '提升城市贸易吞吐量与库存容量。平坦地。',
    allowedBiomes: FLAT_BIOMES,
    methods: [
      {
        id: 'basic',
        name: '基础市场',
        desc: '库存容量 +1000，贸易效率提升',
        inputs: {},
        outputs: {},
        labor: 1,
      },
    ],
    buildCost: { resources: { wood: 40, stone: 30, iron: 10 } },
    developCost: { resources: { wood: 25, stone: 20, iron: 5 }, money: 60 },
    developTime: 4,
    isInfrastructure: true,
  },
  {
    id: 'school',
    name: '学校',
    desc: '产出科技点，推进科技树。平坦地。',
    allowedBiomes: FLAT_BIOMES,
    methods: [
      {
        id: 'basic',
        name: '基础教育',
        desc: '每 2 工产出 1 科技点/秒',
        inputs: {},
        outputs: {},
        labor: 2,
        scienceOutput: 1,
      },
    ],
    buildCost: { resources: { wood: 50, stone: 40, iron: 20 } },
    developCost: { resources: { wood: 30, stone: 25, iron: 10 }, money: 80 },
    developTime: 6,
    isInfrastructure: true,
  },
]

/** 建筑 ID -> 定义快速查找 */
export const BUILDING_MAP: Record<BuildingId, BuildingDef> = Object.fromEntries(
  BUILDINGS.map((b) => [b.id, b])
) as Record<BuildingId, BuildingDef>

/** 按 ID 获取建筑定义 */
export function getBuilding(id: BuildingId): BuildingDef {
  return BUILDING_MAP[id]
}

/** 检查生物群系是否允许建造该建筑 */
export function canBuildOnBiome(buildingId: BuildingId, biomeId: BiomeId): boolean {
  const building = BUILDING_MAP[buildingId]
  return building.allowedBiomes.length === 0 || building.allowedBiomes.includes(biomeId)
}

/** 获取生物群系适合建造的建筑列表（按优先级排序） */
export function getSuitableBuildings(biomeId: BiomeId): BuildingDef[] {
  return BUILDINGS.filter((b) => canBuildOnBiome(b.id, biomeId))
}

// ==================== 初始配置 ====================

/** 城市初始状态 */
export const INITIAL_CITY_CONFIG = {
  population: 10,
  foodStock: 50,
  resources: {
    food: 50,
    wood: 50,
    stone: 50,
    iron: 0,
    coal: 0,
    tools: 0,
    cloth: 0,
  } as Record<ResourceId, number>,
  treasury: 100,
}

/** 人口每秒基础食物消耗 */
export const POPULATION_FOOD_CONSUMPTION_PER_SEC = 0.1

/** 人口增长：食物充足时每秒增长率（每人每秒） */
export const POPULATION_GROWTH_RATE = 0.001

/** 人口下降：食物不足时每秒下降率 */
export const POPULATION_DECLINE_RATE = 0.0005

/** 自动开发基础冷却（秒） */
export const AUTO_DEVELOP_BASE_COOLDOWN = 3

/** 自动开发距离惩罚系数（每格额外秒数） */
export const AUTO_DEVELOP_DISTANCE_FACTOR = 0.5

/** 市场价格计算参数 */
export const PRICE_FORMULA = {
  elasticity: 0.75,
  minMultiplier: 0.25,
  maxMultiplier: 1.75,
}

/** 格子键格式：`wx,wy` */
export type CellKey = string

/** 格坐标转键 */
export function cellKey(wx: number, wy: number): CellKey {
  return `${wx},${wy}`
}

/** 键转格坐标 */
export function parseCellKey(key: CellKey): { wx: number; wy: number } {
  const [wx, wy] = key.split(',').map(Number)
  return { wx, wy }
}

/** 获取相邻 8 格键 */
export function getNeighborKeys(key: CellKey): CellKey[] {
  const { wx, wy } = parseCellKey(key)
  const neighbors: CellKey[] = []
  for (let dx = -1; dx <= 1; dx++) {
    for (let dy = -1; dy <= 1; dy++) {
      if (dx === 0 && dy === 0) continue
      neighbors.push(cellKey(wx + dx, wy + dy))
    }
  }
  return neighbors
}

// ==================== 单位定义（阶段 3） ====================

/** 单位 ID */
export type UnitId = 'scout' | 'settler'

/** 单位定义 */
export interface UnitDef {
  id: UnitId
  name: string
  desc: string
  /** 移动速度（格/秒） */
  speed: number
  /** 视野半径（格，沿途解锁迷雾） */
  vision: number
  /** 训练消耗（含金币） */
  trainCost: Cost
  /** 训练耗时（秒） */
  trainTime: number
  /** 是否需要科技解锁 */
  requireTech?: TechId
  /** 是否可建城（开拓者） */
  canSettle?: boolean
}

export const UNITS: Record<UnitId, UnitDef> = {
  scout: {
    id: 'scout',
    name: '侦察兵',
    desc: '快速移动并沿途探索迷雾，为城市拓展视野。',
    speed: 4,
    vision: 4,
    trainCost: { resources: { food: 20, wood: 10 }, money: 30 },
    trainTime: 3,
  },
  settler: {
    id: 'settler',
    name: '开拓者',
    desc: '抵达目标格后可建立新城市，开启新前哨。',
    speed: 2,
    vision: 2,
    trainCost: { resources: { food: 50, wood: 30, stone: 20 }, money: 60 },
    trainTime: 8,
    canSettle: true,
    requireTech: 'colonization',
  },
}

/** 单位 ID 列表 */
export const UNIT_IDS: UnitId[] = ['scout', 'settler']

// ==================== 科技树（阶段 3） ====================

/** 时代 */
export type Era = 'ancient' | 'classical' | 'medieval' | 'industrial' | 'modern' | 'space'

export const ERA_NAMES: Record<Era, string> = {
  ancient: '远古',
  classical: '古典',
  medieval: '中古',
  industrial: '工业',
  modern: '现代',
  space: '星际',
}

/** 科技 ID */
export type TechId =
  | 'agriculture'
  | 'mining'
  | 'writing'
  | 'iron_working'
  | 'machinery'
  | 'colonization'
  | 'civil_service'
  | 'steam_power'
  | 'chemistry'
  | 'electricity'
  | 'aeronautics'
  | 'spaceflight'

/** 科技定义 */
export interface TechDef {
  id: TechId
  name: string
  desc: string
  era: Era
  /** 研究消耗（科技点） */
  cost: number
  /** 前置科技 */
  requires?: TechId[]
  /** 解锁建筑 ID（若解锁） */
  unlockBuildings?: BuildingId[]
  /** 解锁生产方式 ID（若解锁，key 是建筑 id） */
  unlockMethod?: Partial<Record<BuildingId, string>>
  /** 解锁单位 */
  unlockUnits?: UnitId[]
  /** 效率加成（0.1 = +10% 全部产出） */
  efficiency?: number
}

/** 科技树（按时代分组） */
export const TECHS: TechDef[] = [
  { id: 'agriculture', name: '农耕', desc: '农场产出 +20%，解锁集约耕作。', era: 'ancient', cost: 30, efficiency: 0.2, unlockMethod: { farm: 'intensive' } },
  { id: 'mining', name: '采矿', desc: '解锁铁矿与煤矿。', era: 'ancient', cost: 40, unlockBuildings: ['iron_mine', 'coal_mine'] },
  { id: 'writing', name: '文字', desc: '解锁学校，产出科技点。', era: 'ancient', cost: 50, unlockBuildings: ['school'] },
  { id: 'iron_working', name: '铁器', desc: '解锁工坊，可生产工具。', era: 'classical', cost: 80, requires: ['mining'], unlockBuildings: ['workshop'] },
  { id: 'machinery', name: '机械', desc: '解锁机械生产方式。', era: 'classical', cost: 120, requires: ['iron_working'], unlockMethod: { workshop: 'mechanical', weaver: 'mechanical' } },
  { id: 'colonization', name: '殖民', desc: '解锁开拓者，可建立新城市。', era: 'classical', cost: 150, requires: ['writing'], unlockUnits: ['settler'] },
  { id: 'civil_service', name: '文官制度', desc: '行政效率 +20%，市场容量提升。', era: 'medieval', cost: 200, requires: ['writing'], efficiency: 0.2 },
  { id: 'steam_power', name: '蒸汽动力', desc: '采矿与工业产出 +30%。', era: 'industrial', cost: 350, requires: ['machinery'], efficiency: 0.3 },
  { id: 'chemistry', name: '化学', desc: '解锁爆破开采。', era: 'industrial', cost: 400, requires: ['steam_power'], unlockMethod: { quarry: 'blasting' } },
  { id: 'electricity', name: '电气', desc: '全部产出 +40%。', era: 'modern', cost: 600, requires: ['chemistry'], efficiency: 0.4 },
  { id: 'aeronautics', name: '航空', desc: '探索视野扩大。', era: 'modern', cost: 800, requires: ['electricity'] },
  { id: 'spaceflight', name: '星际航行', desc: '迈入星际时代。', era: 'space', cost: 1200, requires: ['aeronautics'] },
]

/** 科技 ID -> 定义 */
export const TECH_MAP: Record<TechId, TechDef> = Object.fromEntries(
  TECHS.map((t) => [t.id, t])
) as Record<TechId, TechDef>
