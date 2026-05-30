<script setup lang="ts">
import { ref, onMounted, onUnmounted, computed, nextTick } from 'vue'
import { fetchFacts, fetchRoots, fetchChildren, type GraphNode, type GraphFact } from '@/api/graph'

// ===== 类型 =====
interface TreeNode {
  key: string          // 树内唯一键
  entityId: string     // 实体 ID: type|name
  name: string
  type: string
  parentKey: string | null
  children: TreeNode[]
  expanded: boolean
  loading: boolean
  loaded: boolean       // 是否已拉取过子节点
  hasChildren: boolean  // 是否有子节点（后端告知）
}

interface FlatRow {
  key: string
  name: string
  type: string
  depth: number
  hasChildren: boolean
  expanded: boolean
  loading: boolean
  node: TreeNode
}

// ===== 状态 =====
const roots = ref<TreeNode[]>([])
const loading = ref(true)
const empty = ref(false)
const selectedId = ref<string | null>(null)
const searchQuery = ref('')
const showSearch = ref(false)
const showUI = ref(false)

// 详情面板
const nodeFactsCache = ref<Map<string, GraphFact[]>>(new Map())

// ===== 颜色/图标 =====
const COLOR: Record<string, string> = {
  User: '#f59e0b', preference: '#a78bfa', fact: '#34d399', event: '#60a5fa',
  plan: '#f87171', topic: '#fb923c', todo: '#fbbf24', conflict: '#ef4444',
  pending: '#6b7280', habit: '#06b6d4', interest: '#ec4899', project: '#6366f1', skill: '#22c55e',
  AI: '#8b5cf6', category: '#6366f1', rule: '#f59e0b', incident: '#ef4444', component: '#22c55e',
}

function nodeColor(type: string): string { return COLOR[type] || '#7c7c90' }

function getTypeIcon(type: string): string {
  const m: Record<string, string> = {
    User: '\u2299', AI: '\u25C9', preference: '\u2661', fact: '\u25CF', event: '\u25C6',
    plan: '\u25B6', topic: '\u2726', todo: '\u2610', conflict: '\u26A1',
    pending: '?', habit: '\u21BB', interest: '\u2605', project: '\u2B21', skill: '\u25C6',
    category: '\u25C7', rule: '\u2699', incident: '\u26A0', component: '\u25A3',
  }
  return m[type] || '\u25CF'
}

function typeLabel(type: string): string {
  const m: Record<string, string> = {
    User: '用户', AI: 'AI', preference: '偏好', fact: '事实', event: '事件',
    plan: '计划', topic: '主题', todo: '待办', conflict: '冲突', pending: '待跟进',
    habit: '习惯', interest: '兴趣', project: '项目', skill: '技能',
    category: '分类', rule: '规则', incident: '事件', component: '组件',
  }
  return m[type] || type
}

// ===== 树操作 =====
function makeNodeKey(entityId: string, parentKey: string | null): string {
  return parentKey ? `${entityId}|${parentKey}` : entityId
}

function createTreeNode(gn: GraphNode, parentKey: string | null, hasChildren: boolean): TreeNode {
  const entityId = gn.id || `${gn.type}|${gn.name}`
  return {
    key: makeNodeKey(entityId, parentKey),
    entityId,
    name: gn.name,
    type: gn.type,
    parentKey,
    children: [],
    expanded: false,
    loading: false,
    loaded: false,
    hasChildren,
  }
}

// ===== 扁平化 =====
const flatRows = computed<FlatRow[]>(() => {
  const result: FlatRow[] = []
  function walk(nodes: TreeNode[], depth: number) {
    for (const node of nodes) {
      result.push({
        key: node.key,
        name: node.name,
        type: node.type,
        depth,
        hasChildren: node.hasChildren,
        expanded: node.expanded,
        loading: node.loading,
        node,
      })
      if (node.expanded) {
        walk(node.children, depth + 1)
      }
    }
  }
  walk(roots.value, 0)
  return result
})

// 搜索过滤
const filteredRows = computed(() => {
  const q = searchQuery.value.trim().toLowerCase()
  if (!q) return flatRows.value
  return flatRows.value.filter(r =>
    r.name.toLowerCase().includes(q) || r.type.toLowerCase().includes(q)
  )
})

// ===== 选中节点 =====
const selectedRow = computed(() => flatRows.value.find(r => r.key === selectedId.value) || null)

const selectedFacts = computed(() => {
  if (!selectedId.value) return []
  return nodeFactsCache.value.get(selectedId.value)?.facts || []
})

const selectedEntity = computed(() => {
  if (!selectedId.value) return null
  return nodeFactsCache.value.get(selectedId.value)?.entity || null
})

function selectRow(row: FlatRow) {
  if (selectedId.value === row.key) {
    selectedId.value = null
    return
  }
  selectedId.value = row.key
  loadFacts(row.node)
}

async function loadFacts(node: TreeNode) {
  if (nodeFactsCache.value.has(node.key)) return
  try {
    const res = await fetchFacts(node.type, node.name)
    nodeFactsCache.value.set(node.key, { facts: res.facts, entity: res.entity || null })
  } catch {
    nodeFactsCache.value.set(node.key, { facts: [], entity: null })
  }
}

function closePanel() { selectedId.value = null }

// ===== 循环检测：收集祖先 entityId 集合 =====
function getAncestorEntityIds(node: TreeNode): Set<string> {
  const ancestors = new Set<string>()
  let current: TreeNode | undefined = node
  while (current) {
    ancestors.add(current.entityId)
    // 通过 parentKey 在 flatRows 中找父节点
    if (current.parentKey) {
      const parentRow = flatRows.value.find(r => r.key === current!.parentKey)
      current = parentRow?.node
    } else {
      current = undefined
    }
  }
  return ancestors
}

// ===== 展开/折叠 =====
async function toggleExpand(row: FlatRow) {
  const node = row.node
  if (node.expanded) {
    node.expanded = false
    return
  }
  node.expanded = true
  if (node.loaded) return

  node.loading = true
  try {
    const data = await fetchChildren(node.type, node.name)
    node.loaded = true
    const ancestors = getAncestorEntityIds(node)
    for (const gn of data.nodes) {
      const childEntityId = gn.id || `${gn.type}|${gn.name}`
      // 跳过已在祖先路径中的节点，防止循环引用
      if (ancestors.has(childEntityId)) continue
      const hasChild = data.has_children?.[childEntityId] ?? false
      const child = createTreeNode(gn, node.key, hasChild)
      node.children.push(child)
    }
  } catch {
    // 加载失败，保持 loaded=false 以便重试
    node.expanded = false
  } finally {
    node.loading = false
  }
}

// ===== 生命周期 =====
onMounted(async () => {
  try {
    const data = await fetchRoots()
    roots.value = data.nodes.map(gn => {
      const entityId = gn.id || `${gn.type}|${gn.name}`
      return createTreeNode(gn, null, true) // 根节点默认有子节点
    })
    if (roots.value.length === 0) {
      empty.value = true
    }
    // 根节点默认展开第一层
    for (const root of roots.value) {
      await toggleExpandDirect(root)
    }
  } catch {
    empty.value = true
  } finally {
    loading.value = false
  }
  setTimeout(() => { showUI.value = true }, 200)
})

// 直接展开（不通过 row，用于初始加载）
async function toggleExpandDirect(node: TreeNode) {
  node.expanded = true
  if (node.loaded) return
  node.loading = true
  try {
    const data = await fetchChildren(node.type, node.name)
    node.loaded = true
    const ancestors = getAncestorEntityIds(node)
    for (const gn of data.nodes) {
      const childEntityId = gn.id || `${gn.type}|${gn.name}`
      if (ancestors.has(childEntityId)) continue
      const hasChild = data.has_children?.[childEntityId] ?? false
      node.children.push(createTreeNode(gn, node.key, hasChild))
    }
  } catch {
    node.expanded = false
  } finally {
    node.loading = false
  }
}

// ===== 搜索 =====
function toggleSearch() {
  showSearch.value = !showSearch.value
  if (!showSearch.value) searchQuery.value = ''
  else nextTick(() => { (document.querySelector('.search-input') as HTMLInputElement)?.focus() })
}

// ===== 键盘 =====
function onKeyDown(e: KeyboardEvent) {
  if (e.key === 'Escape') {
    if (showSearch.value && searchQuery.value) { searchQuery.value = ''; return }
    closePanel()
  }
  if ((e.ctrlKey || e.metaKey) && e.key === 'f') { e.preventDefault(); toggleSearch() }
}

onMounted(() => document.addEventListener('keydown', onKeyDown))
onUnmounted(() => {
  document.removeEventListener('keydown', onKeyDown)
})

// ===== 全部展开/折叠 =====
function expandAll() {
  for (const row of flatRows.value) {
    if (row.hasChildren && !row.expanded && !row.loading) {
      toggleExpand(row)
    }
  }
}

function collapseAll() {
  for (const root of roots.value) {
    collapseRecursive(root)
  }
}

function collapseRecursive(node: TreeNode) {
  node.expanded = false
  for (const child of node.children) {
    collapseRecursive(child)
  }
}
</script>

<template>
  <div class="tree-container">
    <!-- 搜索栏 -->
    <Transition name="fade">
      <div v-if="showSearch" class="search-bar">
        <svg class="search-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/></svg>
        <input v-model="searchQuery" class="search-input" placeholder="搜索节点..." @keydown.esc.stop="searchQuery = ''" />
        <span v-if="searchQuery" class="search-count">{{ filteredRows.length }} 个匹配</span>
        <button class="search-close" @click="toggleSearch">✕</button>
      </div>
    </Transition>

    <!-- 工具栏 -->
    <Transition name="fade">
      <div v-if="showUI && !loading && !empty" class="toolbar">
        <span class="toolbar-count">{{ flatRows.length }} 节点</span>
        <button class="toolbar-btn" @click="expandAll" title="全部展开">展开全部</button>
        <button class="toolbar-btn" @click="collapseAll" title="全部折叠">折叠全部</button>
      </div>
    </Transition>

    <!-- 加载状态 -->
    <div v-if="loading" class="status-view">
      <div class="orbit-loader">
        <div class="orbit-ring o-1" /><div class="orbit-ring o-2" /><div class="orbit-ring o-3" />
        <div class="orbit-core" />
      </div>
      <span class="status-text">加载图谱</span>
    </div>

    <!-- 空状态 -->
    <div v-else-if="empty" class="status-view">
      <div class="empty-illustration">
        <svg width="80" height="80" viewBox="0 0 80 80" fill="none">
          <circle cx="40" cy="40" r="35" stroke="#1e1e3a" stroke-width="0.8" stroke-dasharray="6 5"/>
          <circle cx="40" cy="40" r="25" stroke="#1a1a34" stroke-width="0.6"/>
          <circle cx="30" cy="30" r="6" fill="none" stroke="#2d2d52" stroke-width="0.8"/>
          <circle cx="52" cy="34" r="5" fill="none" stroke="#2d2d52" stroke-width="0.8"/>
          <circle cx="42" cy="54" r="5.5" fill="none" stroke="#2d2d52" stroke-width="0.8"/>
        </svg>
      </div>
      <span class="status-text">暂无图谱数据</span>
      <span class="status-hint">开始对话后，AI 会将重要信息存入图谱</span>
    </div>

    <!-- 树视图 -->
    <div v-show="!loading && !empty" class="tree-scroll" @click.self="closePanel">
      <div
        v-for="row in filteredRows"
        :key="row.key"
        class="tree-row"
        :class="{
          'row-selected': selectedId === row.key,
          'row-root': row.depth === 0,
        }"
        :style="{ paddingLeft: (12 + row.depth * 20) + 'px' }"
        @click.stop="selectRow(row)"
      >
        <!-- 展开/折叠箭头 -->
        <span
          v-if="row.hasChildren"
          class="row-arrow"
          :class="{ 'arrow-expanded': row.expanded, 'arrow-loading': row.loading }"
          @click.stop="toggleExpand(row)"
        >
          <span v-if="row.loading" class="arrow-spin">⟳</span>
          <span v-else>▶</span>
        </span>
        <span v-else class="row-arrow arrow-none"></span>

        <!-- 类型图标 -->
        <span class="row-icon" :style="{ color: nodeColor(row.type) }">{{ getTypeIcon(row.type) }}</span>

        <!-- 名称 -->
        <span class="row-name" :style="{ color: row.depth <= 1 ? '#e0e0ec' : '#b0b0c4' }">{{ row.name }}</span>

        <!-- 类型标签 -->
        <span class="row-type-tag" :style="{ color: nodeColor(row.type), background: nodeColor(row.type) + '18' }">{{ typeLabel(row.type) }}</span>
      </div>

      <div v-if="searchQuery && filteredRows.length === 0" class="search-empty">无匹配节点</div>
    </div>

    <!-- 底部提示 -->
    <Transition name="fade">
      <div v-if="showUI && !loading && !empty" class="tree-hint">
        点击展开 · 点击名称查看详情 · Ctrl+F 搜索 · Esc 关闭
      </div>
    </Transition>

    <!-- 详情面板 -->
    <Transition name="panel-slide">
      <aside v-if="selectedRow" class="detail-panel">
        <div class="panel-accent" :style="{ background: nodeColor(selectedRow.type) }" />
        <button class="panel-close" @click="closePanel">
          <svg width="16" height="16" viewBox="0 0 16 16"><path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>
        </button>

        <div class="panel-header">
          <div class="panel-dot-wrap">
            <div class="panel-dot" :style="{ background: nodeColor(selectedRow.type) }" />
            <div class="panel-dot-glow" :style="{ background: nodeColor(selectedRow.type) }" />
          </div>
          <div class="panel-titles">
            <span class="panel-name">{{ selectedRow.name }}</span>
            <span class="panel-type" :style="{ color: nodeColor(selectedRow.type) }">{{ typeLabel(selectedRow.type) }}</span>
          </div>
        </div>

        <div class="panel-divider" />

        <!-- 实体描述 -->
        <div v-if="selectedEntity?.content" class="panel-section">
          <div class="panel-section-title">描述</div>
          <div class="panel-desc">{{ selectedEntity.content }}</div>
        </div>

        <!-- 关联事实 -->
        <div v-if="selectedFacts.length > 0" class="panel-section">
          <div class="panel-section-title">关联事实</div>
          <div v-for="(f, i) in selectedFacts" :key="i" class="panel-fact">
            <div class="fact-gutter" :style="{ background: nodeColor(f.type) }" />
            <div class="fact-body">
              <span class="fact-type-tag" :style="{ color: nodeColor(f.type), background: nodeColor(f.type) + '18' }">{{ typeLabel(f.type) }}</span>
              <span class="fact-content">{{ f.content }}</span>
            </div>
          </div>
        </div>

        <div v-if="!selectedEntity?.content && selectedFacts.length === 0" class="panel-empty">
          <span>暂无关联信息</span>
        </div>
      </aside>
    </Transition>
  </div>
</template>

<style scoped>
.tree-container {
  width: 100%; height: 100%; position: relative;
  background: #080812; overflow: hidden; display: flex; flex-direction: column;
}
.tree-container::after {
  content: ''; position: absolute; inset: 0; pointer-events: none; z-index: 0;
  background:
    radial-gradient(ellipse at center, transparent 35%, rgba(4,4,10,0.55) 100%),
    radial-gradient(ellipse at 15% 15%, rgba(99,102,241,0.04) 0%, transparent 50%),
    radial-gradient(ellipse at 85% 85%, rgba(139,92,246,0.03) 0%, transparent 50%);
}

/* 搜索栏 */
.search-bar {
  position: absolute; top: 14px; left: 50%; transform: translateX(-50%);
  display: flex; align-items: center; gap: 8px; z-index: 15;
  background: rgba(14,14,32,0.94); backdrop-filter: blur(20px);
  border: 1px solid rgba(255,255,255,0.08); border-radius: 14px;
  padding: 9px 14px; min-width: 300px;
  box-shadow: 0 8px 32px rgba(0,0,0,0.5);
}
.search-bar:focus-within {
  border-color: rgba(139, 92, 246, 0.45);
  box-shadow: 0 8px 32px rgba(0,0,0,0.5), 0 0 24px rgba(99,102,241,0.15);
}
.search-icon { color: #5a5a72; flex-shrink: 0; }
.search-input {
  flex: 1; background: transparent; border: none; color: #d0d0e0;
  font-size: 0.82rem; outline: none; font-family: inherit;
}
.search-input::placeholder { color: #3a3a52; }
.search-count { font-size: 0.68rem; color: #5a5a72; white-space: nowrap; }
.search-close {
  background: none; border: none; color: #5a5a72; cursor: pointer;
  font-size: 0.8rem; padding: 2px 4px; border-radius: 4px;
}
.search-close:hover { color: #c0c0d0; }

.fade-enter-active, .fade-leave-active { transition: opacity 0.25s ease, transform 0.25s ease; }
.fade-enter-from, .fade-leave-to { opacity: 0; transform: translateY(-6px); }

/* 工具栏 */
.toolbar {
  position: absolute; top: 14px; right: 18px; z-index: 5;
  display: flex; align-items: center; gap: 10px;
  padding: 6px 12px; background: rgba(14,14,32,0.85); backdrop-filter: blur(12px);
  border: 1px solid rgba(255,255,255,0.06); border-radius: 20px;
}
.toolbar-count { font-size: 0.7rem; color: #5a5a72; }
.toolbar-btn {
  background: none; border: 1px solid rgba(255,255,255,0.08); color: #7a7a94;
  font-size: 0.66rem; padding: 3px 10px; border-radius: 10px; cursor: pointer;
  transition: all 0.2s; font-family: inherit;
}
.toolbar-btn:hover { background: rgba(255,255,255,0.05); color: #c0c0d0; border-color: rgba(255,255,255,0.15); }

/* 状态 */
.status-view {
  display: flex; flex-direction: column; align-items: center;
  justify-content: center; gap: 1rem; height: 100%; position: relative; z-index: 1;
}
.status-text { color: #4a4a60; font-size: 0.85rem; letter-spacing: 0.08em; }
.status-hint { color: #35354a; font-size: 0.78rem; }
.empty-illustration { opacity: 0.3; }

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

/* 树滚动区 */
.tree-scroll {
  flex: 1; overflow-y: auto; overflow-x: hidden; position: relative; z-index: 1;
  padding: 60px 0 40px 0;
}
.tree-scroll::-webkit-scrollbar { width: 4px; }
.tree-scroll::-webkit-scrollbar-track { background: transparent; }
.tree-scroll::-webkit-scrollbar-thumb { background: rgba(255,255,255,0.04); border-radius: 2px; }

/* 树行 */
.tree-row {
  display: flex; align-items: center; gap: 6px; height: 32px;
  padding-right: 20px; cursor: pointer; position: relative;
  transition: background 0.15s; border-radius: 0 6px 6px 0; margin-right: 8px;
}
.tree-row:hover { background: rgba(255,255,255,0.03); }
.row-selected { background: rgba(139, 92, 246, 0.12) !important; }
.row-selected::before {
  content: ''; position: absolute; left: 0; top: 50%; transform: translateY(-50%);
  width: 3px; height: 18px; background: #a78bfa; border-radius: 0 2px 2px 0;
}
.row-root { margin-top: 12px; }
.row-root + .row-root { margin-top: 12px; }

/* 箭头 */
.row-arrow {
  width: 16px; height: 16px; display: flex; align-items: center; justify-content: center;
  font-size: 7px; color: #5a5a72; flex-shrink: 0; transition: transform 0.2s, color 0.2s;
}
.row-arrow:hover { color: #a0a0b8; }
.arrow-expanded { transform: rotate(90deg); }
.arrow-none { visibility: hidden; }
.arrow-spin {
  display: inline-block; animation: spin 0.8s linear infinite; font-size: 10px;
}
@keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }
.arrow-loading { color: #a78bfa; }

/* 图标 */
.row-icon { font-size: 12px; flex-shrink: 0; width: 16px; text-align: center; }

/* 名称 */
.row-name {
  font-size: 0.82rem; flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}

/* 类型标签 */
.row-type-tag {
  font-size: 0.6rem; font-weight: 500; padding: 1px 6px; border-radius: 4px;
  flex-shrink: 0; opacity: 0.7;
}
.tree-row:hover .row-type-tag { opacity: 1; }

.search-empty {
  text-align: center; color: #4a4a60; padding: 40px 0; font-size: 0.85rem;
}

.tree-hint {
  position: absolute; bottom: 12px; left: 50%; transform: translateX(-50%);
  font-size: 0.64rem; color: #2e2e46; pointer-events: none; z-index: 2;
  letter-spacing: 0.04em; background: rgba(8,8,16,0.65); padding: 3px 14px;
  border-radius: 20px; border: 1px solid rgba(255,255,255,0.03);
}

/* 详情面板 */
.detail-panel {
  position: absolute; top: 0; right: 0; width: 320px; height: 100%;
  background: rgba(10, 10, 26, 0.85); backdrop-filter: blur(28px) saturate(1.4);
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
.fact-type-tag { font-size: 0.62rem; font-weight: 600; flex-shrink: 0; padding: 0.08rem 0.35rem; border-radius: 4px; }
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
