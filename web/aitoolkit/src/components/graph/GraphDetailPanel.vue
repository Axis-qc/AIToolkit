<script setup lang="ts">
/**
 * 实体详情面板。
 * 选中节点后按需拉取该实体的描述与关联事实，视觉与既有暗色主题保持一致。
 */
import { ref, watch, computed } from 'vue'
import { fetchFacts, type GraphNode, type GraphFact } from '@/api/graph'
import { nodeColor, typeLabel } from './graphTheme'

const props = defineProps<{
  node: GraphNode
}>()

defineEmits<{
  close: []
}>()

const facts = ref<GraphFact[]>([])
const description = ref('')
const importance = ref<number>(1)
const pinned = ref(false)
const loading = ref(false)
const failed = ref(false)

/** 请求序号：实体快速切换时丢弃过期响应。 */
let requestId = 0

async function loadFacts(node: GraphNode): Promise<void> {
  const current = ++requestId
  loading.value = true
  failed.value = false
  facts.value = []
  description.value = ''
  importance.value = node.importance || 1
  pinned.value = !!node.pinned

  try {
    const res = await fetchFacts(node.type, node.name)
    if (current !== requestId) return
    facts.value = res.facts
    description.value = res.entity?.content || ''
  } catch {
    if (current !== requestId) return
    failed.value = true
  } finally {
    if (current === requestId) loading.value = false
  }
}

watch(() => props.node, (node) => {
  if (node) loadFacts(node)
}, { immediate: true })

const color = computed(() => nodeColor(props.node.type))
</script>

<template>
  <aside class="detail-panel">
    <div class="panel-accent" :style="{ background: color }" />
    <button class="panel-close" title="关闭" @click="$emit('close')">
      <svg width="16" height="16" viewBox="0 0 16 16">
        <path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" />
      </svg>
    </button>

    <div class="panel-header">
      <div class="panel-dot-wrap">
        <div class="panel-dot" :style="{ background: color }" />
        <div class="panel-dot-glow" :style="{ background: color }" />
      </div>
      <div class="panel-titles">
        <span class="panel-name" v-text="node.name" />
        <div class="panel-meta">
          <span class="panel-type" :style="{ color }" v-text="typeLabel(node.type)" />
          <span class="panel-badge" v-text="'重要度 ' + importance" />
          <span v-if="pinned" class="panel-badge badge-pinned">固定</span>
        </div>
      </div>
    </div>

    <div class="panel-divider" />

    <div v-if="loading" class="panel-empty">
      <span>加载中</span>
    </div>

    <template v-else>
      <div v-if="failed" class="panel-empty">
        <span>加载失败</span>
      </div>

      <div v-if="description" class="panel-section">
        <div class="panel-section-title">描述</div>
        <div class="panel-desc" v-text="description" />
      </div>

      <div v-if="facts.length > 0" class="panel-section">
        <div class="panel-section-title" v-text="'关联事实 ' + facts.length" />
        <div v-for="(fact, i) in facts" :key="fact.id ?? i" class="panel-fact">
          <div class="fact-gutter" :style="{ background: nodeColor(fact.type) }" />
          <div class="fact-body">
            <span class="fact-type-tag" :style="{ color: nodeColor(fact.type) }" v-text="typeLabel(fact.type)" />
            <span class="fact-content" v-text="fact.content" />
          </div>
        </div>
      </div>

      <div v-if="!description && facts.length === 0 && !failed" class="panel-empty">
        <span>暂无关联信息</span>
      </div>
    </template>
  </aside>
</template>

<style scoped>
.detail-panel {
  position: absolute; top: 0; right: 0; width: 340px; height: 100%;
  background: rgba(10, 10, 13, 0.95);
  backdrop-filter: blur(28px) saturate(1.4);
  -webkit-backdrop-filter: blur(28px) saturate(1.4);
  border-left: 1px solid var(--line);
  padding: 1.5rem;
  overflow-y: auto;
  z-index: 20;
  box-shadow: -16px 0 48px rgba(0, 0, 0, 0.5);
  animation: panel-in 0.32s cubic-bezier(0.16, 1, 0.3, 1);
}

@keyframes panel-in {
  from { transform: translateX(100%); opacity: 0; }
  to { transform: translateX(0); opacity: 1; }
}

.panel-accent { position: absolute; top: 0; left: 0; right: 0; height: 2px; opacity: 0.7; }

.panel-close {
  position: absolute; top: 1rem; right: 1rem; width: 28px; height: 28px;
  border: 1px solid var(--line); background: color-mix(in srgb, var(--accent) 5%, transparent);
  color: var(--muted); border-radius: 8px; cursor: pointer;
  display: flex; align-items: center; justify-content: center; transition: all 0.2s;
}
.panel-close:hover { color: var(--accent); border-color: var(--line-strong); }

.panel-header { display: flex; align-items: flex-start; gap: 0.75rem; margin-bottom: 1rem; padding-right: 2rem; }
.panel-dot-wrap { position: relative; flex-shrink: 0; width: 14px; height: 14px; margin-top: 4px; }
.panel-dot { width: 12px; height: 12px; border-radius: 50%; position: absolute; top: 1px; left: 1px; z-index: 1; }
.panel-dot-glow { width: 14px; height: 14px; border-radius: 50%; position: absolute; top: 0; left: 0; filter: blur(6px); opacity: 0.5; }
.panel-titles { display: flex; flex-direction: column; gap: 5px; min-width: 0; }
.panel-name { font-size: 1rem; font-weight: 600; color: var(--text); line-height: 1.3; word-break: break-word; }
.panel-meta { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.panel-type { font-size: 0.7rem; letter-spacing: 0.03em; }
.panel-badge {
  font-size: 0.62rem; color: var(--muted);
  background: color-mix(in srgb, var(--accent) 6%, transparent);
  border: 1px solid var(--line);
  border-radius: 4px; padding: 1px 5px;
}
.badge-pinned { color: var(--accent); }

.panel-divider {
  height: 1px;
  background: linear-gradient(90deg, color-mix(in srgb, var(--accent) 18%, transparent) 0%, transparent 100%);
  margin-bottom: 1.25rem;
}

.panel-section { margin-bottom: 1.25rem; }
.panel-section-title {
  font-size: 0.68rem; font-weight: 600; color: var(--dim);
  text-transform: uppercase; letter-spacing: 0.08em; margin-bottom: 0.5rem;
}

.panel-fact {
  font-size: 0.8rem; color: var(--muted); line-height: 1.55; margin-bottom: 0.4rem;
  background: color-mix(in srgb, var(--accent) 4%, transparent);
  border: 1px solid var(--line);
  border-radius: 10px; display: flex; overflow: hidden;
  transition: background 0.2s, border-color 0.2s;
}
.panel-fact:hover {
  background: color-mix(in srgb, var(--accent) 8%, transparent);
  border-color: var(--line-strong);
}
.fact-gutter { width: 3px; flex-shrink: 0; opacity: 0.5; }
.fact-body { flex: 1; padding: 0.5rem 0.6rem; display: flex; flex-direction: column; gap: 0.3rem; min-width: 0; }
.fact-type-tag { font-size: 0.62rem; font-weight: 600; flex-shrink: 0; opacity: 0.85; }
.fact-content { flex: 1; word-break: break-word; }

.panel-desc {
  font-size: 0.8rem; color: var(--muted); line-height: 1.6;
  padding: 0.5rem 0.75rem;
  background: color-mix(in srgb, var(--accent) 4%, transparent);
  border: 1px solid var(--line);
  border-radius: 10px;
  white-space: pre-wrap; word-break: break-word;
  max-height: 320px; overflow-y: auto;
}

.panel-empty {
  display: flex; align-items: center; justify-content: center;
  color: var(--dim); font-size: 0.78rem; padding: 3rem 0;
}

.detail-panel::-webkit-scrollbar { width: 3px; }
.detail-panel::-webkit-scrollbar-track { background: transparent; }
.detail-panel::-webkit-scrollbar-thumb { background: color-mix(in srgb, var(--accent) 14%, transparent); border-radius: 3px; }
.panel-desc::-webkit-scrollbar { width: 3px; }
.panel-desc::-webkit-scrollbar-thumb { background: color-mix(in srgb, var(--accent) 14%, transparent); border-radius: 3px; }
</style>
