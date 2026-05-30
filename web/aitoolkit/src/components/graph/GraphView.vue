<script setup lang="ts">
import { ref } from 'vue'
import GraphCanvas from './GraphCanvas.vue'
import GraphForce from './GraphForce.vue'

type ViewMode = 'tree' | 'graph'

const mode = ref<ViewMode>('graph')

function toggleMode() {
  mode.value = mode.value === 'tree' ? 'graph' : 'tree'
}
</script>

<template>
  <div class="graph-view">
    <!-- 模式切换 -->
    <div class="view-switcher">
      <button
        :class="['sw-btn', { active: mode === 'graph' }]"
        @click="mode = 'graph'"
        title="力导向图"
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <circle cx="6" cy="6" r="3"/><circle cx="18" cy="6" r="3"/>
          <circle cx="12" cy="18" r="3"/><line x1="8" y1="7" x2="16" y2="7"/>
          <line x1="7" y1="8" x2="11" y2="16"/><line x1="17" y1="8" x2="13" y2="16"/>
        </svg>
        <span>力导向图</span>
      </button>
      <button
        :class="['sw-btn', { active: mode === 'tree' }]"
        @click="mode = 'tree'"
        title="树形浏览"
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <line x1="12" y1="3" x2="12" y2="10"/><line x1="12" y1="10" x2="6" y2="17"/>
          <line x1="12" y1="10" x2="18" y2="17"/><line x1="6" y1="17" x2="6" y2="21"/>
          <line x1="18" y1="17" x2="18" y2="21"/>
        </svg>
        <span>树形浏览</span>
      </button>
    </div>

    <!-- 视图容器 -->
    <div class="view-container">
      <GraphForce v-if="mode === 'graph'" />
      <GraphCanvas v-else />
    </div>
  </div>
</template>

<style scoped>
.graph-view {
  width: 100%;
  height: 100%;
  position: relative;
  background: #080812;
}

.view-switcher {
  position: absolute;
  top: 14px;
  left: 50%;
  transform: translateX(-50%);
  display: flex;
  gap: 4px;
  z-index: 20;
  background: rgba(14, 14, 32, 0.85);
  backdrop-filter: blur(12px);
  border: 1px solid rgba(255, 255, 255, 0.06);
  border-radius: 20px;
  padding: 3px;
}

.sw-btn {
  display: flex;
  align-items: center;
  gap: 5px;
  padding: 5px 13px;
  border: none;
  border-radius: 16px;
  background: transparent;
  color: #5a5a72;
  font-size: 0.75rem;
  cursor: pointer;
  transition: all 0.2s;
  font-family: inherit;
  white-space: nowrap;
}

.sw-btn:hover {
  color: #8a8aa0;
  background: rgba(255, 255, 255, 0.04);
}

.sw-btn.active {
  color: #d0d0e0;
  background: rgba(139, 92, 246, 0.2);
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.3);
}

.view-container {
  width: 100%;
  height: 100%;
}
</style>
