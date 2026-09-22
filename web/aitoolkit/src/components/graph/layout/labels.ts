/**
 * 标签分级显示与矩形避让。
 *
 * 消除「糊成一团」的关键：622 个节点若全部常显名称，文字必然互相叠压。
 * 策略分三级——
 *   焦点级  选中/悬停节点及其一跳邻居，无条件参与
 *   重要级  根节点或重要度达标者，始终参与
 *   普通级  受预算限制，按重要度降序取前 N 个参与竞争
 *
 * 有选中节点时不显示普通标签：此时画面聚焦在该节点的邻域，
 * 减少干扰才能让关系看清楚。
 *
 * 候选按优先级排序后依次做矩形占位检测，位置被占的候选让位。
 * 每帧重建占用网格，避免候选间两两比较。
 */

/** 只需读取坐标与优先级判定字段，不依赖具体节点类型。 */
export interface LabelNode {
  x: number
  y: number
  importance: number
  isRoot: boolean
}

export interface LabelCandidate {
  index: number
  /** 文字锚点（屏幕坐标，节点位置）。 */
  x: number
  y: number
  /** 标签绘制方向：右或左，用于减少同侧拥堵。 */
  side: 1 | -1
}

export interface PlacedLabel {
  index: number
  x: number
  y: number
  side: 1 | -1
}

/** 屏幕空间矩形，避让全部在屏幕坐标下判定。 */
export interface ScreenRect {
  x0: number
  y0: number
  x1: number
  y1: number
}

/** 网格单元边长（屏幕像素），用于加速重叠查询。 */
const CELL = 64

class RectGrid {
  private cells = new Map<number, ScreenRect[]>()

  private key(cx: number, cy: number): number {
    // 坐标已限制在合理范围，用位移打包成单一整数键。
    return ((cx + 4096) << 13) ^ (cy + 4096)
  }

  insert(rect: ScreenRect): void {
    const cx0 = Math.floor(rect.x0 / CELL)
    const cy0 = Math.floor(rect.y0 / CELL)
    const cx1 = Math.floor(rect.x1 / CELL)
    const cy1 = Math.floor(rect.y1 / CELL)
    for (let cx = cx0; cx <= cx1; cx++) {
      for (let cy = cy0; cy <= cy1; cy++) {
        const key = this.key(cx, cy)
        let bucket = this.cells.get(key)
        if (bucket === undefined) {
          bucket = []
          this.cells.set(key, bucket)
        }
        bucket.push(rect)
      }
    }
  }

  intersects(rect: ScreenRect): boolean {
    const cx0 = Math.floor(rect.x0 / CELL)
    const cy0 = Math.floor(rect.y0 / CELL)
    const cx1 = Math.floor(rect.x1 / CELL)
    const cy1 = Math.floor(rect.y1 / CELL)
    for (let cx = cx0; cx <= cx1; cx++) {
      for (let cy = cy0; cy <= cy1; cy++) {
        const bucket = this.cells.get(this.key(cx, cy))
        if (bucket === undefined) continue
        for (const other of bucket) {
          if (
            rect.x0 < other.x1 && rect.x1 > other.x0 &&
            rect.y0 < other.y1 && rect.y1 > other.y0
          ) {
            return true
          }
        }
      }
    }
    return false
  }
}

export interface LabelContext {
  /** 世界坐标 → 屏幕坐标的缩放比。 */
  scale: number
  /** 世界坐标 → 屏幕坐标的平移量。 */
  offsetX: number
  offsetY: number
  /** 画布可视区域（屏幕像素）。 */
  viewWidth: number
  viewHeight: number
  /** 节点屏幕半径查询。 */
  radiusOf: (index: number) => number
  /** 标签文字宽度查询（屏幕像素，已含内边距）。 */
  widthOf: (index: number) => number
  /** 标签文字高度（屏幕像素）。 */
  height: number
  /** 是否忽略预算、全部参与竞争（对应界面的「全部标签」开关）。 */
  showAll: boolean
  /**
   * 普通标签的参与预算系数。越大越多普通节点参与竞争，
   * 对应界面上的「标签密度」滑块。
   */
  labelScale: number
  /** 焦点节点集合（选中、悬停及其邻居），无条件参与。 */
  focus: Set<number>
  /** 是否有选中节点。此时收敛为只显示焦点与重要节点。 */
  hasSelection: boolean
}

/** 焦点级之外，普通标签的基准预算。 */
const BASE_BUDGET = 40
/** 重要度达到该值的节点始终参与，不受预算限制。 */
const ALWAYS_IMPORTANCE = 8
/** 视口外多留的余量，避免边缘标签突现突隐。 */
const VIEW_MARGIN = 200

/**
 * 计算本帧应绘制的标签。
 * 返回按优先级排定的结果，调用方直接绘制即可。
 */
export function placeLabels(nodes: LabelNode[], context: LabelContext): PlacedLabel[] {
  const { scale, offsetX, offsetY, viewWidth, viewHeight } = context

  // 先分类收集：焦点级与重要级不受预算限制，普通级按预算裁量。
  const focusCandidates: LabelCandidate[] = []
  const alwaysCandidates: LabelCandidate[] = []
  const normalCandidates: LabelCandidate[] = []

  for (let i = 0; i < nodes.length; i++) {
    const node = nodes[i]!
    const sx = node.x * scale + offsetX
    const sy = node.y * scale + offsetY

    // 视口外直接跳过。
    if (
      sx < -VIEW_MARGIN || sx > viewWidth + VIEW_MARGIN ||
      sy < -VIEW_MARGIN || sy > viewHeight + VIEW_MARGIN
    ) {
      continue
    }

    const candidate: LabelCandidate = {
      index: i,
      x: sx,
      y: sy,
      // 右侧优先；位于画布右半区的标签翻到左侧，避免文字顶出边界。
      side: sx > viewWidth * 0.62 ? -1 : 1,
    }

    if (context.focus.has(i)) {
      focusCandidates.push(candidate)
    } else if (node.isRoot || node.importance >= ALWAYS_IMPORTANCE) {
      alwaysCandidates.push(candidate)
    } else {
      normalCandidates.push(candidate)
    }
  }

  // 普通标签的预算：showAll 时不设限，否则按密度系数放大基准值。
  // 有选中节点时完全收起普通标签，把画面让给该节点的邻域关系。
  let budget = 0
  if (context.showAll) budget = normalCandidates.length
  else if (!context.hasSelection) budget = Math.round(BASE_BUDGET * context.labelScale)

  // 普通级按重要度降序取前 budget 个，同级按屏幕位置排序保证稳定不闪。
  normalCandidates.sort((a, b) => {
    const ia = nodes[a.index]!
    const ib = nodes[b.index]!
    if (ia.importance !== ib.importance) return ib.importance - ia.importance
    if (a.y !== b.y) return a.y - b.y
    return a.x - b.x
  })
  const selected = normalCandidates.slice(0, Math.max(0, budget))

  // 重要级同样按重要度排序，位置更靠前者优先占位。
  alwaysCandidates.sort((a, b) => {
    const ia = nodes[a.index]!
    const ib = nodes[b.index]!
    if (ia.importance !== ib.importance) return ib.importance - ia.importance
    if (a.y !== b.y) return a.y - b.y
    return a.x - b.x
  })

  const grid = new RectGrid()
  const placed: PlacedLabel[] = []
  const height = context.height

  /** 构造某个候选在指定方向上的标签矩形。 */
  function rectFor(candidate: LabelCandidate, side: 1 | -1): ScreenRect {
    const radius = context.radiusOf(candidate.index)
    const width = context.widthOf(candidate.index)
    const left = side === 1
      ? candidate.x + radius + 4
      : candidate.x - radius - 4 - width
    return {
      x0: left,
      y0: candidate.y - height / 2,
      x1: left + width,
      y1: candidate.y + height / 2,
    }
  }

  function insideView(rect: ScreenRect): boolean {
    if (rect.x1 < 0 || rect.x0 > viewWidth) return false
    if (rect.y1 < 0 || rect.y0 > viewHeight) return false
    return true
  }

  /**
   * 尝试放置。优先默认方向，被占则翻到另一侧；仍被占则放弃。
   * force 为 true 时（焦点标签）两个方向都冲突也强制放置，
   * 保证当前操作对象的名称一定可读。
   */
  function tryPlace(candidate: LabelCandidate, force: boolean): void {
    const primary = rectFor(candidate, candidate.side)
    if (insideView(primary) && !grid.intersects(primary)) {
      grid.insert(primary)
      placed.push({ index: candidate.index, x: primary.x0, y: candidate.y, side: candidate.side })
      return
    }

    const flip: 1 | -1 = candidate.side === 1 ? -1 : 1
    const secondary = rectFor(candidate, flip)
    if (insideView(secondary) && !grid.intersects(secondary)) {
      grid.insert(secondary)
      placed.push({ index: candidate.index, x: secondary.x0, y: candidate.y, side: flip })
      return
    }

    if (force) {
      const chosen = insideView(primary) ? primary : secondary
      grid.insert(chosen)
      placed.push({ index: candidate.index, x: chosen.x0, y: candidate.y, side: chosen === primary ? candidate.side : flip })
    }
  }

  // 焦点标签优先且强制显示，其次是重要节点，最后是预算内的普通标签。
  for (const candidate of focusCandidates) tryPlace(candidate, true)
  for (const candidate of alwaysCandidates) tryPlace(candidate, false)
  for (const candidate of selected) tryPlace(candidate, false)

  return placed
}
