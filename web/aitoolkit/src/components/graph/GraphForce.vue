<script setup lang="ts">
import { ref, onMounted, onUnmounted, watch, nextTick } from 'vue'
import * as d3 from 'd3'
import { fetchFullGraph, fetchFacts, type GraphNode, type GraphEdge, type GraphFact } from '@/api/graph'

// ===== 颜色 =====
const COLOR: Record<string, string> = {
  User: '#f59e0b', preference: '#a78bfa', fact: '#34d399', event: '#60a5fa',
  plan: '#f87171', topic: '#fb923c', todo: '#fbbf24', conflict: '#ef4444',
  pending: '#6b7280', habit: '#06b6d4', interest: '#ec4899', project: '#6366f1',
  skill: '#22c55e', AI: '#8b5cf6', category: '#6366f1', rule: '#f59e0b',
  incident: '#ef4444', component: '#22c55e',
}

function nodeColor(type: string): string { return COLOR[type] || '#7c7c90' }

// ===== 状态 =====
const containerRef = ref<HTMLDivElement>()
const svgRef = ref<SVGSVGElement>()
const loading = ref(true)
const error = ref('')
const selectedNode = ref<{ name: string; type: string; id: string } | null>(null)
const selectedFacts = ref<GraphFact[]>([])
const selectedEntityDesc = ref('')
const loadingFacts = ref(false)

// ===== D3 内部状态 =====
let simulation: d3.Simulation<SimNode, undefined> | null = null
let zoomBehavior: d3.ZoomBehavior<SVGSVGElement, unknown> | null = null

interface SimNode extends d3.SimulationNodeDatum {
  id: string
  name: string
  type: string
  importance: number
  pinned: boolean
}

interface SimLink extends d3.SimulationLinkDatum<SimNode> {
  rel_type: string
}

// ===== 渲染力导向图 =====
async function renderGraph() {
  loading.value = true
  error.value = ''

  try {
    const data = await fetchFullGraph()
    if (!data.nodes.length) {
      loading.value = false
      return
    }

    // 准备节点和边
    const nodeMap = new Map<string, SimNode>()
    const nodes: SimNode[] = data.nodes.map((n: GraphNode) => {
      const node: SimNode = {
        id: n.id,
        name: n.name,
        type: n.type,
        importance: n.importance || 1,
        pinned: n.pinned || false,
      }
      nodeMap.set(n.id, node)
      return node
    })

    const links: SimLink[] = data.edges
      .filter((e: GraphEdge) => nodeMap.has(e.source) && nodeMap.has(e.target))
      .map((e: GraphEdge) => ({
        source: e.source,
        target: e.target,
        rel_type: e.rel_type,
      }))

    await nextTick()
    if (!svgRef.value || !containerRef.value) return

    const container = containerRef.value
    const width = container.clientWidth
    const height = container.clientHeight

    const svg = d3.select(svgRef.value)
    svg.selectAll('*').remove()
    svg.attr('width', width).attr('height', height)

    // 缩放
    const g = svg.append('g')
    zoomBehavior = d3.zoom<SVGSVGElement, unknown>()
      .scaleExtent([0.1, 4])
      .on('zoom', (event) => {
        g.attr('transform', event.transform)
      })
    svg.call(zoomBehavior)

    // 力模拟
    simulation = d3.forceSimulation<SimNode>(nodes)
      .force('link', d3.forceLink<SimNode, SimLink>(links).id(d => d.id).distance(120))
      .force('charge', d3.forceManyBody().strength(-300))
      .force('x', d3.forceX(width / 2).strength(0.06))
      .force('y', d3.forceY(height / 2).strength(0.06))
      .force('collision', d3.forceCollide<SimNode>().radius(d => 20 + Math.sqrt(d.importance || 1) * 6))

    // 边
    const linkGroup = g.append('g').attr('class', 'links')
    const link = linkGroup
      .selectAll<SVGLineElement, SimLink>('line')
      .data(links)
      .join('line')
      .attr('stroke', '#2a2a44')
      .attr('stroke-width', 1)
      .attr('stroke-opacity', 0.4)

    // 边标签
    const linkLabel = linkGroup
      .selectAll<SVGTextElement, SimLink>('text')
      .data(links)
      .join('text')
      .text(d => d.rel_type)
      .attr('fill', '#4a4a62')
      .attr('font-size', 9)
      .attr('text-anchor', 'middle')
      .attr('dy', -4)
      .attr('opacity', 0)

    // 节点组
    const nodeGroup = g.append('g').attr('class', 'nodes')
    const nodeEnter = nodeGroup
      .selectAll<SVGGElement, SimNode>('g')
      .data(nodes)
      .join('g')
      .attr('class', 'node-group')
      .style('cursor', 'pointer')
      .call(
        d3.drag<SVGGElement, SimNode>()
          .on('start', (event, d) => {
            if (!event.active && simulation) simulation.alphaTarget(0.3).restart()
            d.fx = d.x
            d.fy = d.y
          })
          .on('drag', (event, d) => {
            d.fx = event.x
            d.fy = event.y
          })
          .on('end', (event, d) => {
            if (!event.active && simulation) simulation.alphaTarget(0)
            d.fx = null
            d.fy = null
          }) as any
      )

    // 节点圆
    nodeEnter
      .append('circle')
      .attr('r', d => 6 + Math.sqrt(d.importance || 1) * 4)
      .attr('fill', d => nodeColor(d.type))
      .attr('stroke', '#080812')
      .attr('stroke-width', 2)
      .attr('opacity', 0.85)
      .style('transition', 'opacity 0.2s')

    // 节点标签
    nodeEnter
      .append('text')
      .text(d => d.name.length > 15 ? d.name.slice(0, 15) + '…' : d.name)
      .attr('dx', d => 12 + Math.sqrt(d.importance || 1) * 4)
      .attr('dy', 4)
      .attr('fill', '#b0b0c8')
      .attr('font-size', d => Math.min(13, 10 + Math.sqrt(d.importance || 1) * 1.5))
      .attr('font-weight', d => d.importance >= 8 ? '600' : '400')
      .style('pointer-events', 'none')
      .style('text-shadow', '0 1px 4px rgba(0,0,0,0.8)')

    // 重要节点发光效果
    nodeEnter
      .filter(d => d.importance >= 8)
      .append('circle')
      .attr('r', d => 8 + Math.sqrt(d.importance || 1) * 5)
      .attr('fill', 'none')
      .attr('stroke', d => nodeColor(d.type))
      .attr('stroke-width', 1)
      .attr('stroke-opacity', 0.25)
      .attr('opacity', 0.6)

    // 交互：悬停显示边标签
    nodeEnter
      .on('mouseenter', function () {
        d3.select(this).select('circle').attr('opacity', 1)
        // 显示相连的边标签
        const nodeId = d3.select(this).datum() as SimNode
        linkLabel
          .filter(d => {
            const l = d as SimLink
            return l.source && typeof l.source === 'object'
              ? (l.source as SimNode).id === nodeId.id
              : l.source === nodeId.id
          })
          .attr('opacity', 0.6)
      })
      .on('mouseleave', function () {
        d3.select(this).select('circle').attr('opacity', 0.85)
        linkLabel.attr('opacity', 0)
      })

    // 点击节点
    nodeEnter.on('click', async function (event, d) {
      event.stopPropagation()
      selectNode(d)
    })

    // 点击空白取消选中
    svg.on('click', () => {
      selectedNode.value = null
      selectedFacts.value = []
      selectedEntityDesc.value = ''
    })

    // 模拟更新
    simulation!.on('tick', () => {
      link
        .attr('x1', d => (d.source as SimNode).x!)
        .attr('y1', d => (d.source as SimNode).y!)
        .attr('x2', d => (d.target as SimNode).x!)
        .attr('y2', d => (d.target as SimNode).y!)

      linkLabel
        .attr('x', d => ((d.source as SimNode).x! + (d.target as SimNode).x!) / 2)
        .attr('y', d => ((d.source as SimNode).y! + (d.target as SimNode).y!) / 2)

      nodeEnter.attr('transform', d => `translate(${d.x},${d.y})`)
    })

    // 初始缩放适应
    setTimeout(() => {
      if (zoomBehavior && svgRef.value) {
        const bounds = (svgRef.value.querySelector('.nodes') as SVGGElement)?.getBBox()
        if (bounds) {
          const scale = Math.min(
            width / (bounds.width + 100),
            height / (bounds.height + 100),
            1.5
          )
          const tx = width / 2 - (bounds.x + bounds.width / 2) * scale
          const ty = height / 2 - (bounds.y + bounds.height / 2) * scale
          svg.transition().duration(500).call(
            zoomBehavior.transform,
            d3.zoomIdentity.translate(tx, ty).scale(scale)
          )
        }
      }
    }, 100)
  } catch (e) {
    error.value = '加载图谱数据失败'
    console.error(e)
  } finally {
    loading.value = false
  }
}

// ===== 选择节点查看详情 =====
async function selectNode(d: SimNode) {
  selectedNode.value = { name: d.name, type: d.type, id: d.id }
  selectedFacts.value = []
  selectedEntityDesc.value = ''
  loadingFacts.value = true
  try {
    const res = await fetchFacts(d.type, d.name)
    selectedFacts.value = res.facts
    selectedEntityDesc.value = res.entity?.content || ''
  } catch {
    selectedFacts.value = []
  } finally {
    loadingFacts.value = false
  }
}

// ===== 生命周期 =====
onMounted(() => {
  renderGraph()
})

onUnmounted(() => {
  if (simulation) {
    simulation.stop()
    simulation = null
  }
})

// 窗口变化重新渲染
let resizeTimer: ReturnType<typeof setTimeout> | null = null
function onResize() {
  if (resizeTimer) clearTimeout(resizeTimer)
  resizeTimer = setTimeout(() => renderGraph(), 300)
}

onMounted(() => window.addEventListener('resize', onResize))
onUnmounted(() => {
  window.removeEventListener('resize', onResize)
  if (resizeTimer) clearTimeout(resizeTimer)
})
</script>

<template>
  <div ref="containerRef" class="force-container">
    <!-- 加载 -->
    <div v-if="loading" class="force-status">
      <div class="orbit-loader">
        <div class="orbit-ring o-1" /><div class="orbit-ring o-2" /><div class="orbit-ring o-3" />
        <div class="orbit-core" />
      </div>
      <span class="status-text">加载图谱</span>
    </div>

    <!-- 错误 -->
    <div v-else-if="error" class="force-status">
      <span class="status-text">{{ error }}</span>
    </div>

    <!-- SVG -->
    <svg v-show="!loading && !error" ref="svgRef" class="force-svg"></svg>

    <!-- 节点数量 -->
    <div v-if="!loading && !error" class="force-count">
      {{ selectedNode ? '点击空白取消选择' : '拖拽节点 · 滚轮缩放' }}
    </div>

    <!-- 详情面板 -->
    <Transition name="panel-slide">
      <aside v-if="selectedNode" class="detail-panel">
        <div class="panel-accent" :style="{ background: nodeColor(selectedNode.type) }" />
        <button class="panel-close" @click="selectedNode = null; selectedFacts = []; selectedEntityDesc = ''">
          <svg width="16" height="16" viewBox="0 0 16 16"><path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>
        </button>

        <div class="panel-header">
          <div class="panel-dot-wrap">
            <div class="panel-dot" :style="{ background: nodeColor(selectedNode.type) }" />
            <div class="panel-dot-glow" :style="{ background: nodeColor(selectedNode.type) }" />
          </div>
          <div class="panel-titles">
            <span class="panel-name">{{ selectedNode.name }}</span>
            <span class="panel-type" :style="{ color: nodeColor(selectedNode.type) }">{{ selectedNode.type }}</span>
          </div>
        </div>

        <div class="panel-divider" />

        <div v-if="loadingFacts" class="panel-empty">
          <span>加载中...</span>
        </div>

        <!-- 实体描述 -->
        <div v-else-if="selectedEntityDesc" class="panel-section">
          <div class="panel-section-title">描述</div>
          <div class="panel-desc">{{ selectedEntityDesc }}</div>
        </div>

        <!-- 关联事实 -->
        <div v-if="selectedFacts.length > 0" class="panel-section">
          <div class="panel-section-title">关联事实 ({{ selectedFacts.length }})</div>
          <div v-for="(f, i) in selectedFacts" :key="i" class="panel-fact">
            <div class="fact-gutter" :style="{ background: nodeColor(f.type) }" />
            <div class="fact-body">
              <span class="fact-content">{{ f.content }}</span>
            </div>
          </div>
        </div>

        <div v-if="!selectedEntityDesc && selectedFacts.length === 0 && !loadingFacts" class="panel-empty">
          <span>暂无关联信息</span>
        </div>
      </aside>
    </Transition>
  </div>
</template>

<style scoped>
.force-container {
  width: 100%;
  height: 100%;
  position: relative;
  background: #080812;
  overflow: hidden;
}

.force-svg {
  width: 100%;
  height: 100%;
  display: block;
}

/* 加载状态 */
.force-status {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 1rem;
  height: 100%;
}
.status-text { color: #4a4a60; font-size: 0.85rem; }

.orbit-loader {
  position: relative; width: 60px; height: 60px;
  display: flex; align-items: center; justify-content: center;
}
.orbit-core {
  width: 6px; height: 6px; border-radius: 50%; background: #a78bfa;
  box-shadow: 0 0 12px rgba(139, 92, 246, 0.5), 0 0 24px rgba(139, 92, 246, 0.25); z-index: 1;
}
.orbit-ring {
  position: absolute; inset: 0; border-radius: 50%;
  border: 1px solid transparent; opacity: 0.5;
}
.o-1 { border-color: rgba(139, 92, 246, 0.3); animation: orbit-spin 2.4s linear infinite; }
.o-2 { inset: 8px; border-color: rgba(99, 102, 241, 0.25); animation: orbit-spin 1.8s linear infinite reverse; }
.o-3 { inset: 16px; border-color: rgba(168, 85, 247, 0.2); animation: orbit-spin 3s linear infinite; }
@keyframes orbit-spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }

/* 底部提示 */
.force-count {
  position: absolute;
  bottom: 12px;
  left: 50%;
  transform: translateX(-50%);
  font-size: 0.64rem;
  color: #2e2e46;
  pointer-events: none;
  z-index: 2;
  background: rgba(8,8,16,0.65);
  padding: 3px 14px;
  border-radius: 20px;
  border: 1px solid rgba(255,255,255,0.03);
}

/* 详情面板（与 GraphCanvas 一致） */
.detail-panel {
  position: absolute; top: 0; right: 0; width: 320px; height: 100%;
  background: rgba(10, 10, 26, 0.85);
  backdrop-filter: blur(28px) saturate(1.4);
  -webkit-backdrop-filter: blur(28px) saturate(1.4);
  border-left: 1px solid rgba(255, 255, 255, 0.08); padding: 1.5rem;
  overflow-y: auto; z-index: 10;
  box-shadow: -16px 0 48px rgba(0,0,0,0.5), inset 1px 0 0 rgba(255,255,255,0.02);
}
.panel-accent { position: absolute; top: 0; left: 0; right: 0; height: 2px; opacity: 0.6; }
.panel-slide-enter-active { transition: all 0.4s cubic-bezier(0.16, 1, 0.3, 1); }
.panel-slide-leave-active { transition: all 0.3s cubic-bezier(0.5, 0, 0.75, 0); }
.panel-slide-enter-from { transform: translateX(100%); opacity: 0; }
.panel-slide-leave-to { transform: translateX(80%); opacity: 0; }

.panel-close {
  position: absolute; top: 1rem; right: 1rem; width: 28px; height: 28px;
  border: 1px solid rgba(255,255,255,0.06); background: rgba(255,255,255,0.03);
  color: #6b6b80; border-radius: 8px; cursor: pointer;
  display: flex; align-items: center; justify-content: center; transition: all 0.2s;
}
.panel-close:hover { background: rgba(255,255,255,0.08); color: #d0d0d8; border-color: rgba(255,255,255,0.12); }

.panel-header { display: flex; align-items: center; gap: 0.75rem; margin-bottom: 1rem; padding-right: 2rem; }
.panel-dot-wrap { position: relative; flex-shrink: 0; width: 14px; height: 14px; }
.panel-dot { width: 12px; height: 12px; border-radius: 50%; position: absolute; top: 1px; left: 1px; z-index: 1; }
.panel-dot-glow { width: 14px; height: 14px; border-radius: 50%; position: absolute; top: 0; left: 0; filter: blur(6px); opacity: 0.5; }
.panel-titles { display: flex; flex-direction: column; gap: 2px; }
.panel-name { font-size: 1rem; font-weight: 600; color: #e0e0ec; line-height: 1.2; word-break: break-all; }
.panel-type { font-size: 0.7rem; letter-spacing: 0.03em; }
.panel-divider { height: 1px; background: linear-gradient(90deg, rgba(255,255,255,0.08) 0%, rgba(255,255,255,0.01) 100%); margin-bottom: 1.25rem; }
.panel-section { margin-bottom: 1.25rem; }
.panel-section-title { font-size: 0.68rem; font-weight: 600; color: #4a4a5e; text-transform: uppercase; letter-spacing: 0.08em; margin-bottom: 0.5rem; }
.panel-fact {
  font-size: 0.8rem; color: #9a9ab0; line-height: 1.55; margin-bottom: 0.35rem;
  background: rgba(255,255,255,0.02); border-radius: 10px;
  border: 1px solid rgba(255,255,255,0.04); display: flex; overflow: hidden;
  transition: background 0.2s, transform 0.2s, border-color 0.2s;
}
.panel-fact:hover { background: rgba(255,255,255,0.05); border-color: rgba(255,255,255,0.10); transform: translateY(-1px); }
.fact-gutter { width: 3px; flex-shrink: 0; opacity: 0.4; border-radius: 0 2px 2px 0; }
.panel-fact:hover .fact-gutter { opacity: 0.8; }
.fact-body { flex: 1; padding: 0.5rem 0.6rem; display: flex; flex-direction: column; gap: 0.35rem; }
.fact-content { flex: 1; }
.panel-desc {
  font-size: 0.8rem;
  color: #9a9ab0;
  line-height: 1.6;
  padding: 0.5rem 0.75rem;
  background: rgba(255,255,255,0.02);
  border-radius: 10px;
  border: 1px solid rgba(255,255,255,0.04);
  white-space: pre-wrap;
  word-break: break-word;
}
.panel-empty { display: flex; flex-direction: column; align-items: center; gap: 0.75rem; color: #3a3a4e; font-size: 0.78rem; text-align: center; padding: 3rem 0; }
.detail-panel::-webkit-scrollbar { width: 3px; }
.detail-panel::-webkit-scrollbar-track { background: transparent; }
.detail-panel::-webkit-scrollbar-thumb { background: rgba(255,255,255,0.05); border-radius: 3px; }
</style>
