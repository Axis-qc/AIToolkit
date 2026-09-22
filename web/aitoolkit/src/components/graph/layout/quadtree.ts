/**
 * Barnes-Hut 四叉树 —— 力导向斥力的近似加速结构。
 *
 * 朴素斥力需要每对节点互算，复杂度 O(n^2)。四叉树把远处的一整簇节点
 * 折叠成一个质心，只要簇的尺寸与距离之比小于 theta 就用质心近似，
 * 复杂度降到 O(n log n)。621 个节点规模下每帧只需数千次计算。
 *
 * 实现要点：所有树节点从对象池复用，构建阶段不产生新分配，
 * 避免每帧 GC 抖动导致的掉帧。
 */

/** 参与排斥的物体，坐标为世界坐标。 */
export interface QuadBody {
  x: number
  y: number
  /** 斥力电荷，越大对周围推得越远。 */
  charge: number
}

interface QuadNode {
  /** 该子树所有 body 的质心。 */
  cx: number
  cy: number
  /** 该子树所有 body 的电荷总和。 */
  mass: number
  /** 覆盖范围。 */
  x0: number
  y0: number
  x1: number
  y1: number
  /** 叶子持有的 body 下标；-1 表示这是内部节点。 */
  body: number
  c0: QuadNode | null
  c1: QuadNode | null
  c2: QuadNode | null
  c3: QuadNode | null
}

/** 低于此尺寸不再细分，避免重合点导致无限递归。 */
const MIN_SIZE = 0.5

/**
 * 距离平方软化下限。d2 被夹到不低于该值后，力 = dx * rep * q * m / d2
 * 在近距离退化为与 dx 成正比的有界量（Plummer 软化），
 * 既不会因除零爆成 Infinity，重合点也不会被弹飞。
 * 节点实际间距在数十世界单位量级，该下限对正常节点对无影响。
 */
const SOFTENING2 = 1.0

const pool: QuadNode[] = []
let poolUsed = 0

function createNode(): QuadNode {
  return {
    cx: 0, cy: 0, mass: 0,
    x0: 0, y0: 0, x1: 0, y1: 0,
    body: -1,
    c0: null, c1: null, c2: null, c3: null,
  }
}

function acquire(x0: number, y0: number, x1: number, y1: number): QuadNode {
  let node = pool[poolUsed]
  if (node === undefined) {
    node = createNode()
    pool[poolUsed] = node
  }
  poolUsed++
  node.cx = 0
  node.cy = 0
  node.mass = 0
  node.x0 = x0
  node.y0 = y0
  node.x1 = x1
  node.y1 = y1
  node.body = -1
  node.c0 = null
  node.c1 = null
  node.c2 = null
  node.c3 = null
  return node
}

function childAt(node: QuadNode, index: number, mx: number, my: number): QuadNode {
  let existing: QuadNode | null
  let x0: number
  let y0: number
  let x1: number
  let y1: number

  if (index === 0) {
    existing = node.c0
    x0 = node.x0; y0 = node.y0; x1 = mx; y1 = my
  } else if (index === 1) {
    existing = node.c1
    x0 = mx; y0 = node.y0; x1 = node.x1; y1 = my
  } else if (index === 2) {
    existing = node.c2
    x0 = node.x0; y0 = my; x1 = mx; y1 = node.y1
  } else {
    existing = node.c3
    x0 = mx; y0 = my; x1 = node.x1; y1 = node.y1
  }

  if (existing !== null) return existing

  const child = acquire(x0, y0, x1, y1)
  if (index === 0) node.c0 = child
  else if (index === 1) node.c1 = child
  else if (index === 2) node.c2 = child
  else node.c3 = child
  return child
}

function quadrantOf(node: QuadNode, x: number, y: number): number {
  const mx = (node.x0 + node.x1) / 2
  const my = (node.y0 + node.y1) / 2
  return (x >= mx ? 1 : 0) + (y >= my ? 2 : 0)
}

/**
 * 把一个 body 下沉到合适的孩子节点，并把它的电荷并入当前节点的质心。
 * 调用前当前节点已确认可容纳新 body（非满叶子）。
 */
function pushDown(node: QuadNode, index: number, bodies: QuadBody[]): void {
  const body = bodies[index]!
  const mx = (node.x0 + node.x1) / 2
  const my = (node.y0 + node.y1) / 2
  const child = childAt(node, quadrantOf(node, body.x, body.y), mx, my)
  insert(child, index, bodies)

  const mass = node.mass + body.charge
  if (mass > 0) {
    node.cx = (node.cx * node.mass + body.x * body.charge) / mass
    node.cy = (node.cy * node.mass + body.y * body.charge) / mass
  }
  node.mass = mass
}

function insert(node: QuadNode, index: number, bodies: QuadBody[]): void {
  const body = bodies[index]!

  // 空叶子：直接安放。
  if (node.body === -1 && node.c0 === null && node.mass === 0) {
    node.body = index
    node.mass = body.charge
    node.cx = body.x
    node.cy = body.y
    return
  }

  // 尺寸已达下限，无法再细分：并入质心做近似，视觉上这些点已重合。
  if (node.x1 - node.x0 < MIN_SIZE) {
    const mass = node.mass + body.charge
    if (mass > 0) {
      node.cx = (node.cx * node.mass + body.x * body.charge) / mass
      node.cy = (node.cy * node.mass + body.y * body.charge) / mass
    }
    node.mass = mass
    return
  }

  // 叶子已有一个 body：把它和新 body 一起下沉，自己转为内部节点。
  if (node.body !== -1) {
    const existing = node.body
    node.body = -1
    node.mass = 0
    node.cx = 0
    node.cy = 0
    pushDown(node, existing, bodies)
  }

  pushDown(node, index, bodies)
}

function accumulate(
  node: QuadNode,
  index: number,
  bodies: QuadBody[],
  theta2: number,
  repulsion: number,
  outX: Float64Array,
  outY: Float64Array,
): void {
  if (node.mass === 0) return

  // 这个叶子就是自己，不产生自斥力。
  if (node.body === index) return

  const body = bodies[index]!
  let dx = body.x - node.cx
  let dy = body.y - node.cy
  let d2 = dx * dx + dy * dy

  // 完全重合时方向为零，靠斥力永远分不开，用下标做确定性抖动打破对称。
  if (d2 < 1e-12) {
    const angle = index * 2.399963
    dx = Math.cos(angle)
    dy = Math.sin(angle)
    d2 = 1
  } else if (d2 < SOFTENING2) {
    // 极近时抬高距离，保证力有界且连续。
    d2 = SOFTENING2
  }

  const size = node.x1 - node.x0
  const isLeaf = node.body !== -1

  // 叶子直接算；内部节点若相对距离足够远，用质心近似。
  if (isLeaf || (size * size) / d2 < theta2) {
    const force = (repulsion * body.charge * node.mass) / d2
    const inv = 1 / Math.sqrt(d2)
    outX[index] = outX[index]! + dx * inv * force
    outY[index] = outY[index]! + dy * inv * force
    return
  }

  if (node.c0 !== null) accumulate(node.c0, index, bodies, theta2, repulsion, outX, outY)
  if (node.c1 !== null) accumulate(node.c1, index, bodies, theta2, repulsion, outX, outY)
  if (node.c2 !== null) accumulate(node.c2, index, bodies, theta2, repulsion, outX, outY)
  if (node.c3 !== null) accumulate(node.c3, index, bodies, theta2, repulsion, outX, outY)
}

/**
 * 计算所有 body 受到的斥力，结果累加进 outX / outY。
 * theta 越大近似越激进，0.9 是精度与速度的常用折中。
 */
export function applyRepulsion(
  bodies: QuadBody[],
  theta: number,
  repulsion: number,
  outX: Float64Array,
  outY: Float64Array,
): void {
  const count = bodies.length
  if (count === 0) return
  if (count === 1) return

  let minX = Infinity
  let minY = Infinity
  let maxX = -Infinity
  let maxY = -Infinity
  for (const body of bodies) {
    if (body.x < minX) minX = body.x
    if (body.y < minY) minY = body.y
    if (body.x > maxX) maxX = body.x
    if (body.y > maxY) maxY = body.y
  }

  // 撑成正方形，避免细长分布时象限划分退化。
  const width = maxX - minX
  const height = maxY - minY
  const size = Math.max(width, height, MIN_SIZE)
  const cx = (minX + maxX) / 2
  const cy = (minY + maxY) / 2
  const half = size / 2 + 1

  poolUsed = 0
  const root = acquire(cx - half, cy - half, cx + half, cy + half)

  for (let i = 0; i < count; i++) insert(root, i, bodies)

  const theta2 = theta * theta
  for (let i = 0; i < count; i++) {
    accumulate(root, i, bodies, theta2, repulsion, outX, outY)
  }
}
