<script setup lang="ts">
/**
 * 筛选面板：按实体类型勾选、按重要度设阈值、调标签显示密度。
 * 只产出条件，布局重建由父组件负责。
 */
import { computed } from 'vue'
import type { TypeLegendEntry } from './graphTheme'

const props = defineProps<{
  legend: TypeLegendEntry[]
  hiddenTypes: Set<string>
  minImportance: number
  labelScale: number
  visibleCount: number
  totalCount: number
}>()

const emit = defineEmits<{
  'toggle-type': [type: string]
  'update:min-importance': [value: number]
  'update:label-scale': [value: number]
  reset: []
  close: []
}>()

const allVisible = computed(() => props.hiddenTypes.size === 0)

function isHidden(type: string): boolean {
  return props.hiddenTypes.has(type)
}

/** 只显示实际出现的类型，最多列出前 18 项避免面板过长。 */
const entries = computed(() => props.legend.slice(0, 18))
const hiddenTypeCount = computed(() => props.hiddenTypes.size)
</script>

<template>
  <aside class="filter-panel">
    <div class="filter-head">
      <span class="filter-title">筛选</span>
      <button class="filter-close" title="收起筛选面板" @click="$emit('close')">
        <svg width="14" height="14" viewBox="0 0 16 16">
          <path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" />
        </svg>
      </button>
    </div>

    <div class="filter-summary">
      <span v-text="visibleCount + ' / ' + totalCount + ' 节点'" />
      <span v-if="hiddenTypeCount > 0" class="filter-hidden-count" v-text="'已隐藏 ' + hiddenTypeCount + ' 类'" />
    </div>

    <div class="filter-section">
      <div class="filter-section-head">
        <span class="filter-section-title">实体类型</span>
        <button v-if="!allVisible" class="filter-mini" @click="$emit('reset')">全部显示</button>
      </div>
      <div class="type-list">
        <button
          v-for="entry in entries"
          :key="entry.type"
          class="type-item"
          :class="{ 'type-off': isHidden(entry.type) }"
          :title="entry.type"
          @click="$emit('toggle-type', entry.type)"
        >
          <span class="type-dot" :style="{ background: entry.color, opacity: isHidden(entry.type) ? 0.3 : 1 }" />
          <span class="type-name" v-text="entry.label" />
          <span class="type-count" v-text="entry.count" />
        </button>
      </div>
      <div v-if="legend.length > entries.length" class="type-more" v-text="'另有 ' + (legend.length - entries.length) + ' 类未列出'" />
    </div>

    <div class="filter-section">
      <div class="filter-section-head">
        <span class="filter-section-title">最低重要度</span>
        <span class="filter-value" v-text="minImportance" />
      </div>
      <input
        class="filter-range"
        type="range"
        min="1"
        max="10"
        step="1"
        :value="minImportance"
        @input="$emit('update:min-importance', Number(($event.target as HTMLInputElement).value))"
      />
      <div class="range-hint">
        <span>1</span><span>10</span>
      </div>
    </div>

    <div class="filter-section">
      <div class="filter-section-head">
        <span class="filter-section-title">标签密度</span>
        <span class="filter-value" v-text="labelScale.toFixed(1) + 'x'" />
      </div>
      <input
        class="filter-range"
        type="range"
        min="0.6"
        max="2.6"
        step="0.1"
        :value="labelScale"
        @input="$emit('update:label-scale', Number(($event.target as HTMLInputElement).value))"
      />
      <div class="range-hint">
        <span>少</span><span>多</span>
      </div>
    </div>
  </aside>
</template>

<style scoped>
.filter-panel {
  position: absolute; top: 62px; left: 18px; width: 224px;
  max-height: calc(100% - 120px);
  display: flex; flex-direction: column;
  background: rgba(10, 10, 13, 0.94);
  backdrop-filter: blur(18px);
  border: 1px solid var(--line);
  border-radius: 14px;
  padding: 0.85rem;
  z-index: 14;
  box-shadow: 0 16px 40px rgba(0, 0, 0, 0.4);
  overflow-y: auto;
}

.filter-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 0.4rem; }
.filter-title { font-size: 0.72rem; font-weight: 600; color: var(--text); letter-spacing: 0.05em; }
.filter-close {
  border: none; background: transparent; color: var(--muted); cursor: pointer;
  display: flex; align-items: center; padding: 2px; border-radius: 4px; transition: color 0.18s;
}
.filter-close:hover { color: var(--accent); }

.filter-summary {
  display: flex; align-items: center; justify-content: space-between;
  font-size: 0.66rem; color: var(--muted);
  padding-bottom: 0.6rem;
  border-bottom: 1px solid var(--line);
}
.filter-hidden-count { color: var(--accent); opacity: 0.8; }

.filter-section { margin-top: 0.75rem; }
.filter-section-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 0.45rem; }
.filter-section-title {
  font-size: 0.64rem; font-weight: 600; color: var(--dim);
  text-transform: uppercase; letter-spacing: 0.07em;
}
.filter-value { font-size: 0.66rem; color: var(--accent); }
.filter-mini {
  border: none; background: transparent; color: var(--accent);
  font-size: 0.62rem; font-family: inherit; cursor: pointer; padding: 0; opacity: 0.85;
}
.filter-mini:hover { opacity: 1; text-decoration: underline; }

.type-list { display: flex; flex-direction: column; gap: 1px; }
.type-item {
  display: flex; align-items: center; gap: 6px;
  border: none; background: transparent; cursor: pointer;
  padding: 3px 5px; border-radius: 6px;
  font-family: inherit; font-size: 0.7rem; color: var(--muted);
  transition: background 0.15s, color 0.15s;
  text-align: left;
}
.type-item:hover { background: color-mix(in srgb, var(--accent) 8%, transparent); color: var(--text); }
.type-off { opacity: 0.42; }
.type-dot { width: 7px; height: 7px; border-radius: 50%; flex-shrink: 0; }
.type-name { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.type-count { font-size: 0.62rem; color: var(--dim); flex-shrink: 0; }
.type-more { font-size: 0.6rem; color: var(--dim); margin-top: 4px; }

.filter-range {
  width: 100%; height: 3px; -webkit-appearance: none; appearance: none;
  background: color-mix(in srgb, var(--accent) 16%, transparent);
  border-radius: 2px; outline: none; cursor: pointer;
}
.filter-range::-webkit-slider-thumb {
  -webkit-appearance: none; appearance: none;
  width: 11px; height: 11px; border-radius: 50%;
  background: var(--accent); cursor: pointer;
  box-shadow: 0 0 8px color-mix(in srgb, var(--accent) 50%, transparent);
}
.filter-range::-moz-range-thumb {
  width: 11px; height: 11px; border: none; border-radius: 50%;
  background: var(--accent); cursor: pointer;
}
.range-hint {
  display: flex; justify-content: space-between;
  font-size: 0.58rem; color: var(--dim); margin-top: 3px;
}

.filter-panel::-webkit-scrollbar { width: 3px; }
.filter-panel::-webkit-scrollbar-thumb { background: color-mix(in srgb, var(--accent) 14%, transparent); border-radius: 3px; }
</style>
