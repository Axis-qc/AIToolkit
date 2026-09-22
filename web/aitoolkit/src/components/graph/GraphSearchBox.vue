<script setup lang="ts">
/**
 * 搜索定位框。
 * 输入关键词后在候选列表中定位，选中即通知父组件把镜头飞到目标节点。
 * 支持上下键移动、回车确认、Esc 关闭。
 */
import { ref, computed, watch, onMounted, onUnmounted, nextTick } from 'vue'
import type { GraphNode } from '@/api/graph'
import { nodeColor, typeLabel } from './graphTheme'

const props = defineProps<{
  /** 当前可见（已过滤）的节点。 */
  nodes: GraphNode[]
}>()

const emit = defineEmits<{
  select: [id: string]
}>()

const query = ref('')
const open = ref(false)
const activeIndex = ref(0)
const inputRef = ref<HTMLInputElement>()

const MAX_RESULTS = 12

const results = computed<GraphNode[]>(() => {
  const q = query.value.trim().toLowerCase()
  if (q === '') return []
  const matched: GraphNode[] = []
  for (const node of props.nodes) {
    if (node.name.toLowerCase().includes(q) || node.type.toLowerCase().includes(q)) {
      matched.push(node)
      if (matched.length >= MAX_RESULTS) break
    }
  }
  return matched
})

// 结果变化时把选中项复位到第一条。
watch(results, () => {
  activeIndex.value = 0
})

function openBox(): void {
  open.value = true
  nextTick(() => inputRef.value?.focus())
}

function closeBox(): void {
  open.value = false
  query.value = ''
}

function choose(node: GraphNode | undefined): void {
  if (node === undefined) return
  emit('select', node.id)
  closeBox()
}

function onKeyDown(event: KeyboardEvent): void {
  if (event.key === 'Escape') {
    event.stopPropagation()
    closeBox()
    return
  }
  if (event.key === 'Enter') {
    event.preventDefault()
    choose(results.value[activeIndex.value])
    return
  }
  if (event.key === 'ArrowDown') {
    event.preventDefault()
    if (results.value.length > 0) {
      activeIndex.value = (activeIndex.value + 1) % results.value.length
    }
    return
  }
  if (event.key === 'ArrowUp') {
    event.preventDefault()
    if (results.value.length > 0) {
      activeIndex.value = (activeIndex.value - 1 + results.value.length) % results.value.length
    }
  }
}

/** 全局快捷键 Ctrl+F / Cmd+F 打开搜索。 */
function onGlobalKey(event: KeyboardEvent): void {
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'f') {
    event.preventDefault()
    openBox()
  }
}

onMounted(() => document.addEventListener('keydown', onGlobalKey))
onUnmounted(() => document.removeEventListener('keydown', onGlobalKey))
</script>

<template>
  <div class="search-box">
    <button v-if="!open" class="search-trigger" title="搜索节点 (Ctrl+F)" @click="openBox">
      <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <circle cx="11" cy="11" r="8" /><path d="m21 21-4.35-4.35" />
      </svg>
      <span>搜索</span>
    </button>

    <template v-else>
      <div class="search-field">
        <svg class="search-icon" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <circle cx="11" cy="11" r="8" /><path d="m21 21-4.35-4.35" />
        </svg>
        <input
          ref="inputRef"
          v-model="query"
          class="search-input"
          placeholder="搜索节点名称或类型"
          @keydown="onKeyDown"
          @blur="closeBox"
        />
        <span v-if="query.trim() !== ''" class="search-count" v-text="results.length" />
      </div>

      <div v-if="query.trim() !== ''" class="search-results">
        <div v-if="results.length === 0" class="result-empty">无匹配节点</div>
        <button
          v-for="(node, i) in results"
          :key="node.id"
          class="result-item"
          :class="{ 'result-active': i === activeIndex }"
          @mousedown.prevent="choose(node)"
          @mouseenter="activeIndex = i"
        >
          <span class="result-dot" :style="{ background: nodeColor(node.type) }" />
          <span class="result-name" v-text="node.name" />
          <span class="result-type" v-text="typeLabel(node.type)" />
        </button>
      </div>
    </template>
  </div>
</template>

<style scoped>
.search-box { position: relative; }

.search-trigger {
  display: flex; align-items: center; gap: 5px;
  border: none; background: transparent; color: var(--muted);
  font-size: 0.72rem; font-family: inherit; cursor: pointer;
  padding: 5px 12px; border-radius: 14px; white-space: nowrap;
  transition: all 0.18s;
}
.search-trigger:hover { color: var(--text); background: color-mix(in srgb, var(--accent) 8%, transparent); }

.search-field {
  display: flex; align-items: center; gap: 6px;
  padding: 4px 10px; min-width: 250px;
  background: rgba(6, 6, 10, 0.9);
  border: 1px solid var(--line-strong);
  border-radius: 14px;
}
.search-icon { color: var(--muted); flex-shrink: 0; }
.search-input {
  flex: 1; background: transparent; border: none; outline: none;
  color: var(--text); font-size: 0.72rem; font-family: inherit; min-width: 0;
}
.search-input::placeholder { color: var(--dim); }
.search-count { font-size: 0.62rem; color: var(--muted); flex-shrink: 0; }

.search-results {
  position: absolute; top: 34px; left: 0; right: 0;
  max-height: 300px; overflow-y: auto;
  background: rgba(10, 10, 13, 0.98);
  border: 1px solid var(--line-strong);
  border-radius: 10px;
  padding: 4px;
  box-shadow: 0 16px 40px rgba(0, 0, 0, 0.55);
}

.result-empty {
  font-size: 0.7rem; color: var(--dim); text-align: center; padding: 12px 0;
}

.result-item {
  display: flex; align-items: center; gap: 6px; width: 100%;
  border: none; background: transparent; cursor: pointer;
  padding: 5px 7px; border-radius: 6px;
  font-family: inherit; font-size: 0.72rem; color: var(--muted);
  text-align: left; transition: background 0.12s, color 0.12s;
}
.result-item:hover, .result-active {
  background: color-mix(in srgb, var(--accent) 12%, transparent);
  color: var(--text);
}
.result-dot { width: 7px; height: 7px; border-radius: 50%; flex-shrink: 0; }
.result-name { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.result-type { font-size: 0.6rem; color: var(--dim); flex-shrink: 0; }

.search-results::-webkit-scrollbar { width: 3px; }
.search-results::-webkit-scrollbar-thumb { background: color-mix(in srgb, var(--accent) 16%, transparent); border-radius: 3px; }
</style>
