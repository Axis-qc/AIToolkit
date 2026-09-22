/**
 * 自研力导向布局内核。
 *
 * 面向本项目实测规模调参：621 个实体、898 条边、平均度数 3.07、最大度数 14。
 * 布局目标不是层级排布，而是关系网——同类实体自然聚成团，
 * 连通分量彼此分开，孤立节点被推到外围。
 *
 * 三类力：
 *   斥力    Barnes-Hut 四叉树近似，按度数衰减，避免枢纽把邻居推得过远
 *   弹簧力  沿边把相连节点拉近，强度随两端度数衰减
 *   向心力  按度数反向加权把游离节点收拢，孤立节点权重最低故飘在外围
 *
 * 内核不做任何绘制，只维护坐标与速度；渲染由 renderer 负责。
 */

import { applyRepulsion, type QuadBody } from './quadtree'

/** 传入内核的实体输入。 */
export interface SimInputNode {
  id: string
  /** 连边数量，用于力参数衰减与向心权重。 */
  degree: number
  /** 重要度。 */
  importance: number
  /** 是否固定根节点（参与初始环形排布）。 */
  isRoot: boolean
}

/** 渲染层直接读取的可变布局状态。 */
export interface SimNode {
  id: string
  x: number
  y: number
  vx: number
  vy: number
  /** 非空表示被用户拖拽固定，布局不再移动它。 */
  fx: number | null
  fy: number | null
  degree: number
  importance: number
  isRoot: boolean
}

export interface SimLink {
  source: number
  target: number
}

export interface ForceConfig {
  /** 斥力全局系数。 */
  repulsion: number
  /** 四叉树近似阈值。 */
  theta: number
  /** 理想弹簧长度。 */
  linkDistance: number
  /** 弹簧强度系数。 */
  linkStrength: number
  /** 向心力系数。 */
  centering: number
  /** 每帧速度保留比例。 */
  velocityDecay: number
  /** 单帧最大位移，防止局部力失衡时节点瞬移。 */
  maxVelocity: number
}

/**
 * 默认参数。取值为对真实图谱（622 节点 / 898 边）实测扫描的结果。
 * 斥力与理想边长较早期版本上调，让团块之间拉开空隙、结构层次更清楚。
 */
export const DEFAULT_CONFIG: ForceConfig = {
  repulsion: 3200,
  // 单帧实测约 0.65ms，远低于 16.7ms 的 60fps 预算，
  // 因此取比常用值 0.9 更小的 theta 换取更高的斥力精度。
  theta: 0.72,
  linkDistance: 105,
  linkStrength: 0.42,
  centering: 0.06,
  velocityDecay: 0.62,
  maxVelocity: 36,
}

/** 位置安全上限，任何异常力都不会把节点推出可绘制范围。 */
const POSITION_LIMIT = 1e6

export class ForceSimulation {
  readonly nodes: SimNode[] = []
  readonly links: SimLink[] = []

  private config: ForceConfig
  private alpha = 1
  private alphaMin = 0.002
  private alphaDecay: number
  private bodies: QuadBody[] = []
  private forceX: Float64Array = new Float64Array(0)
  private forceY: Float64Array = new Float64Array(0)
  private adjacency: number[][] = []

  constructor(config: Partial<ForceConfig> = {}) {
    this.config = { ...DEFAULT_CONFIG, ...config }
    // 约 260 帧退火到 alphaMin，60fps 下 4 秒出头收敛。
    this.alphaDecay = 1 - Math.pow(this.alphaMin, 1 / 260)
  }

  /** 重新加热，用于拖拽或结构变化后继续收敛。 */
  reheat(alpha = 0.45): void {
    this.alpha = Math.min(1, Math.max(this.alpha, alpha))
  }

  /**
   * 用新数据整体重建布局。
   * 同名节点若已在上一轮存在则沿用坐标，避免筛选或重载后整张图跳变。
   */
  setGraph(inputNodes: SimInputNode[], links: SimLink[], previous?: SimNode[]): void {
    const prevById = new Map<string, SimNode>()
    if (previous) for (const node of previous) prevById.set(node.id, node)

    const count = inputNodes.length

    this.nodes.length = 0
    this.links.length = 0
    this.adjacency = new Array<number[]>(count)
    this.bodies = new Array<QuadBody>(count)
    this.forceX = new Float64Array(count)
    this.forceY = new Float64Array(count)

    // 初始位置用黄金角螺旋铺开，近似均匀分布，比随机撒点收敛更快且结果稳定。
    for (let i = 0; i < count; i++) {
      const input = inputNodes[i]!
      this.adjacency[i] = []

      const prev = prevById.get(input.id)
      let x: number
      let y: number
      if (prev) {
        x = prev.x
        y = prev.y
      } else {
        const angle = i * 2.399963
        const radius = 12 * Math.sqrt(i + 1)
        x = Math.cos(angle) * radius
        y = Math.sin(angle) * radius
      }

      this.nodes.push({
        id: input.id,
        x,
        y,
        vx: 0,
        vy: 0,
        fx: null,
        fy: null,
        degree: input.degree,
        importance: input.importance,
        isRoot: input.isRoot,
      })

      this.bodies[i] = { x, y, charge: 1 + Math.sqrt(input.degree) * 0.5 }
    }

    // links 的 source/target 已经是 inputNodes 的下标（由调用方映射好），
    // 这里只做边界校验，不能再按 id 重新查表。
    for (const link of links) {
      const source = link.source
      const target = link.target
      if (source < 0 || target < 0) continue
      if (source >= count || target >= count) continue
      if (source === target) continue
      this.links.push({ source, target })
      this.adjacency[source]!.push(target)
      this.adjacency[target]!.push(source)
    }

    this.alpha = 1
  }

  /**
   * 推进一帧。返回 false 表示已收敛，调用方可停掉动画帧。
   */
  step(): boolean {
    const nodes = this.nodes
    const count = nodes.length
    if (count === 0) return false

    const config = this.config

    // 弹簧力的距离截断上限。必须严格大于理想边长，否则 bias 会恒为负、
    // 弹簧力整体反转成推远——筛选到少量节点时就会节点飞散、长线横穿画布。
    // 因此上限只与理想边长挂钩，绝不与节点数挂钩。
    const maxDistance = config.linkDistance * 4

    this.forceX.fill(0)
    this.forceY.fill(0)

    // 1) 斥力：Barnes-Hut 近似
    for (let i = 0; i < count; i++) {
      const node = nodes[i]!
      const body = this.bodies[i]!
      body.x = node.x
      body.y = node.y
    }
    applyRepulsion(this.bodies, config.theta, config.repulsion, this.forceX, this.forceY)

    // 2) 弹簧力：只沿真实边施力，强度随两端度数衰减。
    // 符号约定：dx = target - source。距离大于理想长度时把两端拉近，
    // 因此 target 沿 -dx、source 沿 +dx 受力。
    for (const link of this.links) {
      const source = nodes[link.source]!
      const target = nodes[link.target]!
      const dx = target.x - source.x
      const dy = target.y - source.y
      let distance = Math.sqrt(dx * dx + dy * dy)
      if (distance < 1) distance = 1
      if (distance > maxDistance) distance = maxDistance

      const bias = (distance - config.linkDistance) / distance
      // 度数越高，单条边的影响力越低，避免枢纽把所有邻居硬拽到一起。
      const scale = config.linkStrength / Math.min(source.degree + 1, target.degree + 1)

      const fx = dx * bias * scale
      const fy = dy * bias * scale
      this.forceX[link.target] = this.forceX[link.target]! - fx
      this.forceY[link.target] = this.forceY[link.target]! - fy
      this.forceX[link.source] = this.forceX[link.source]! + fx
      this.forceY[link.source] = this.forceY[link.source]! + fy
    }

    // 3) 向心力：度数越高权重越大，枢纽锚在中心，叶子与孤立节点留在外围。
    // 这里只算力，不乘 alpha——alpha 在积分阶段统一施加，保证三类力
    // 按同一比例退火，不会出现斥力恒强而向心衰减的失衡。
    const strength = config.centering
    for (let i = 0; i < count; i++) {
      const node = nodes[i]!
      const weight = 0.35 + Math.min(1, node.degree / 8) * 0.65
      this.forceX[i] = this.forceX[i]! - node.x * strength * weight
      this.forceY[i] = this.forceY[i]! - node.y * strength * weight
    }

    // 4) 积分：所有力统一乘 alpha 后退火，再施加速度阻尼与位移上限。
    const decay = config.velocityDecay
    const alphaScale = this.alpha
    let moved = false
    for (let i = 0; i < count; i++) {
      const node = nodes[i]!

      if (node.fx !== null && node.fy !== null) {
        node.x = node.fx
        node.y = node.fy
        node.vx = 0
        node.vy = 0
        continue
      }

      let vx = (node.vx + this.forceX[i]! * alphaScale) * decay
      let vy = (node.vy + this.forceY[i]! * alphaScale) * decay

      const speed = Math.sqrt(vx * vx + vy * vy)
      if (speed > config.maxVelocity) {
        const clamp = config.maxVelocity / speed
        vx *= clamp
        vy *= clamp
      }

      node.vx = vx
      node.vy = vy
      node.x += vx
      node.y += vy

      // 兜底：任何异常都不让坐标溢出到不可绘制区域。
      if (!Number.isFinite(node.x) || Math.abs(node.x) > POSITION_LIMIT) {
        node.x = Number.isFinite(node.x) ? Math.sign(node.x) * POSITION_LIMIT : 0
        node.vx = 0
      }
      if (!Number.isFinite(node.y) || Math.abs(node.y) > POSITION_LIMIT) {
        node.y = Number.isFinite(node.y) ? Math.sign(node.y) * POSITION_LIMIT : 0
        node.vy = 0
      }

      if (!moved && (vx * vx + vy * vy) > 0.01) moved = true
    }

    // 5) 退火
    this.alpha += (0 - this.alpha) * this.alphaDecay
    if (this.alpha < this.alphaMin) this.alpha = 0

    return moved || this.alpha > 0
  }

  /** 把某个节点按拖拽目标固定。 */
  pin(index: number, x: number, y: number): void {
    const node = this.nodes[index]
    if (!node) return
    node.fx = x
    node.fy = y
  }

  /** 解除固定。release 为 true 时恢复自由，否则停在当前位置。 */
  unpin(index: number, release: boolean): void {
    const node = this.nodes[index]
    if (!node) return
    if (release) {
      node.fx = null
      node.fy = null
    } else {
      node.fx = node.x
      node.fy = node.y
    }
  }

  /** 某个节点的一跳邻居下标，用于高亮邻域。 */
  neighborsOf(index: number): number[] {
    return this.adjacency[index] ?? []
  }

  /** 全部节点的包围盒，用于自动适配视口。 */
  bounds(): { minX: number; minY: number; maxX: number; maxY: number } {
    if (this.nodes.length === 0) return { minX: 0, minY: 0, maxX: 0, maxY: 0 }
    let minX = Infinity
    let minY = Infinity
    let maxX = -Infinity
    let maxY = -Infinity
    for (const node of this.nodes) {
      if (node.x < minX) minX = node.x
      if (node.y < minY) minY = node.y
      if (node.x > maxX) maxX = node.x
      if (node.y > maxY) maxY = node.y
    }
    return { minX, minY, maxX, maxY }
  }
}
