/**
 * Canvas 渲染层。
 *
 * 单层 Canvas，不创建任何 DOM 节点。每帧流程：
 *   视口裁剪 → 批量画边 → 画节点 → 画标签
 *
 * 边按「普通 / 层级 / 高亮」三桶写入 Path2D 后各描边一次，
 * 避免逐条设置描边状态；节点按颜色分桶批量填充。
 * 静止后由外层停掉动画帧，本模块不持有任何定时器。
 */

import type { SimLink } from './forceSim'
import type { PlacedLabel } from './labels'
import { nodeColor } from '../graphTheme'

/** 渲染所需的节点视图，由视图层从布局与原始数据合成。 */
export interface RenderNode {
  x: number
  y: number
  type: string
  name: string
  importance: number
  isRoot: boolean
}

export interface RenderStyle {
  background: string
  edgeColor: string
  edgeHierarchyColor: string
  edgeHighlightColor: string
  /** 非焦点元素的降透明度系数。 */
  dimOpacity: number
  labelColor: string
  labelFocusColor: string
  font: string
  labelFontSize: number
}

export const DEFAULT_STYLE: RenderStyle = {
  background: '#08080b',
  edgeColor: '#2b2820',
  edgeHierarchyColor: '#57503d',
  edgeHighlightColor: '#d8b96a',
  dimOpacity: 0.18,
  labelColor: '#b8b0a0',
  labelFocusColor: '#f5efdf',
  font: 'system-ui, -apple-system, "Segoe UI", "Microsoft YaHei", sans-serif',
  labelFontSize: 11,
}

export interface Viewport {
  scale: number
  offsetX: number
  offsetY: number
  /** 视口宽高，单位为 CSS 像素。 */
  width: number
  height: number
  /** 设备像素比。canvas 的位图尺寸是 CSS 尺寸的 dpr 倍。 */
  dpr: number
}

/** 节点基础半径，按重要度放大并限制在可读范围内。 */
export function nodeRadius(importance: number): number {
  const base = 3.2 + Math.sqrt(Math.max(1, importance)) * 1.5
  return Math.min(base, 11)
}

/** 节点命中判定半径，比视觉半径宽容一点便于点击。 */
export function nodeHitRadius(importance: number): number {
  return nodeRadius(importance) + 4
}

/** 缩放对节点大小的修正，限制幅度以免极端缩放下节点过大或消失。 */
function nodeScale(scale: number): number {
  return Math.max(0.75, Math.min(1.4, scale))
}

export interface SceneInput {
  nodes: RenderNode[]
  links: SimLink[]
  /** 每条边的类型名，用于高亮时显示。 */
  linkTypes: string[]
  /** 层级边下标集合。 */
  hierarchyLinks: Set<number>
  /** 选中的节点下标（由双击设置），-1 表示无。 */
  selected: number
  /** 焦点邻域；非空时非焦点元素降透明度。 */
  focus: Set<number>
  /** 需要显示类型标签的边（选中节点的关联边）。 */
  highlightLinks: Set<number>
  labels: PlacedLabel[]
  /** 标签文字截断上限（按字符数近似）。 */
  maxLabelChars: number
}

function formatName(name: string, maxChars: number): string {
  if (name.length <= maxChars) return name
  return name.slice(0, maxChars) + '…'
}

/**
 * 绘制一帧，返回本帧绘制的标签数，便于上层做性能观测。
 */
export function renderScene(
  ctx: CanvasRenderingContext2D,
  input: SceneInput,
  viewport: Viewport,
  style: RenderStyle,
): number {
  const { scale, offsetX, offsetY, width, height, dpr } = viewport

  ctx.save()
  // 恢复 dpr 变换：canvas 位图尺寸是 CSS 尺寸的 dpr 倍，
  // 所有绘制都按 CSS 像素坐标进行，由该变换负责映射到物理像素。
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
  ctx.fillStyle = style.background
  ctx.fillRect(0, 0, width, height)

  const hasFocus = input.focus.size > 0
  const sizeFactor = nodeScale(scale)

  // 视口在世界坐标下的范围，用于裁剪；留余量避免边界闪烁。
  const pad = 40 / scale
  const worldLeft = -offsetX / scale - pad
  const worldTop = -offsetY / scale - pad
  const worldRight = (width - offsetX) / scale + pad
  const worldBottom = (height - offsetY) / scale + pad

  // ── 边 ──────────────────────────────────────────────
  const plainPath = new Path2D()
  const hierarchyPath = new Path2D()
  const highlightPath = new Path2D()
  let plainCount = 0
  let hierarchyCount = 0
  let highlightCount = 0

  const edgeLabelData: { x: number; y: number; text: string }[] = []

  for (let i = 0; i < input.links.length; i++) {
    const link = input.links[i]!
    const source = input.nodes[link.source]
    const target = input.nodes[link.target]
    if (source === undefined || target === undefined) continue

    const sx = source.x
    const sy = source.y
    const tx = target.x
    const ty = target.y

    // 两端同侧出界则跳过。
    if (
      (sx < worldLeft && tx < worldLeft) ||
      (sx > worldRight && tx > worldRight) ||
      (sy < worldTop && ty < worldTop) ||
      (sy > worldBottom && ty > worldBottom)
    ) {
      continue
    }

    const x1 = sx * scale + offsetX
    const y1 = sy * scale + offsetY
    const x2 = tx * scale + offsetX
    const y2 = ty * scale + offsetY

    if (input.highlightLinks.has(i)) {
      highlightPath.moveTo(x1, y1)
      highlightPath.lineTo(x2, y2)
      highlightCount++
      if (scale > 0.5) {
        edgeLabelData.push({
          x: (x1 + x2) / 2,
          y: (y1 + y2) / 2,
          text: input.linkTypes[i] ?? '',
        })
      }
    } else if (input.hierarchyLinks.has(i)) {
      hierarchyPath.moveTo(x1, y1)
      hierarchyPath.lineTo(x2, y2)
      hierarchyCount++
    } else {
      plainPath.moveTo(x1, y1)
      plainPath.lineTo(x2, y2)
      plainCount++
    }
  }

  ctx.lineWidth = 1
  ctx.globalAlpha = hasFocus ? style.dimOpacity : 0.5
  ctx.strokeStyle = style.edgeColor
  if (plainCount > 0) ctx.stroke(plainPath)

  ctx.globalAlpha = hasFocus ? style.dimOpacity * 1.6 : 0.75
  ctx.strokeStyle = style.edgeHierarchyColor
  if (hierarchyCount > 0) ctx.stroke(hierarchyPath)

  ctx.globalAlpha = 1
  ctx.lineWidth = 1.6
  ctx.strokeStyle = style.edgeHighlightColor
  if (highlightCount > 0) ctx.stroke(highlightPath)

  // ── 节点 ────────────────────────────────────────────
  // 按颜色分桶，同色节点一次 beginPath 后批量填充。
  const buckets = new Map<string, number[]>()
  for (let i = 0; i < input.nodes.length; i++) {
    const node = input.nodes[i]!
    if (
      node.x < worldLeft || node.x > worldRight ||
      node.y < worldTop || node.y > worldBottom
    ) {
      continue
    }
    const color = nodeColor(node.type)
    let bucket = buckets.get(color)
    if (bucket === undefined) {
      bucket = []
      buckets.set(color, bucket)
    }
    bucket.push(i)
  }

  for (const [color, indices] of buckets) {
    ctx.fillStyle = color
    ctx.beginPath()
    for (const i of indices) {
      const node = input.nodes[i]!
      const dimmed = hasFocus && !input.focus.has(i)
      if (dimmed) continue
      ctx.moveTo(node.x * scale + offsetX + nodeRadius(node.importance) * sizeFactor, node.y * scale + offsetY)
      ctx.arc(
        node.x * scale + offsetX,
        node.y * scale + offsetY,
        nodeRadius(node.importance) * sizeFactor,
        0,
        Math.PI * 2,
      )
    }
    ctx.globalAlpha = 0.92
    ctx.fill()

    // 被降透明度的节点单独走一遍，避免与实心节点共用同一次填充。
    if (hasFocus) {
      ctx.globalAlpha = style.dimOpacity
      ctx.beginPath()
      for (const i of indices) {
        if (input.focus.has(i)) continue
        const node = input.nodes[i]!
        ctx.moveTo(node.x * scale + offsetX + nodeRadius(node.importance) * sizeFactor, node.y * scale + offsetY)
        ctx.arc(
          node.x * scale + offsetX,
          node.y * scale + offsetY,
          nodeRadius(node.importance) * sizeFactor,
          0,
          Math.PI * 2,
        )
      }
      ctx.fill()
    }
  }

  // 根节点虚线环
  ctx.setLineDash([3, 3])
  ctx.lineWidth = 1.2
  ctx.globalAlpha = hasFocus ? style.dimOpacity : 0.7
  for (let i = 0; i < input.nodes.length; i++) {
    const node = input.nodes[i]!
    if (!node.isRoot) continue
    if (node.x < worldLeft || node.x > worldRight || node.y < worldTop || node.y > worldBottom) continue
    ctx.strokeStyle = nodeColor(node.type)
    ctx.beginPath()
    ctx.arc(
      node.x * scale + offsetX,
      node.y * scale + offsetY,
      (nodeRadius(node.importance) + 4.5) * sizeFactor,
      0,
      Math.PI * 2,
    )
    ctx.stroke()
  }
  ctx.setLineDash([])

  // 选中描边（由双击设置）。悬停不产生任何视觉变化。
  ctx.globalAlpha = 1
  ctx.lineWidth = 2
  if (input.selected >= 0) {
    const node = input.nodes[input.selected]
    if (node !== undefined) {
      ctx.strokeStyle = style.labelFocusColor
      ctx.beginPath()
      ctx.arc(
        node.x * scale + offsetX,
        node.y * scale + offsetY,
        (nodeRadius(node.importance) + 3) * sizeFactor,
        0,
        Math.PI * 2,
      )
      ctx.stroke()
    }
  }

  // ── 标签 ────────────────────────────────────────────
  ctx.font = `${style.labelFontSize}px ${style.font}`
  ctx.textBaseline = 'middle'
  ctx.globalAlpha = 1

  for (const label of input.labels) {
    const node = input.nodes[label.index]
    if (node === undefined) continue
    const isFocus = input.focus.has(label.index) || label.index === input.selected
    const dimmed = hasFocus && !isFocus
    ctx.globalAlpha = dimmed ? style.dimOpacity : 1
    ctx.fillStyle = isFocus ? style.labelFocusColor : style.labelColor
    ctx.textAlign = label.side === 1 ? 'left' : 'right'
    ctx.fillText(formatName(node.name, input.maxLabelChars), label.x, label.y)
  }

  // 高亮边的关系类型
  if (edgeLabelData.length > 0) {
    ctx.globalAlpha = 0.9
    ctx.fillStyle = style.labelFocusColor
    ctx.textAlign = 'center'
    ctx.font = `${style.labelFontSize - 1}px ${style.font}`
    for (const item of edgeLabelData) {
      if (item.text === '') continue
      ctx.fillText(item.text, item.x, item.y - 7)
    }
  }

  ctx.restore()
  return input.labels.length
}
