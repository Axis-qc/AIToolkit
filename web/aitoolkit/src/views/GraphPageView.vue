<script setup lang="ts">
import { RouterLink } from 'vue-router'
import GraphView from '@/components/graph/GraphView.vue'
</script>

<template>
  <div class="graph-page">
    <RouterLink to="/" class="back-btn" title="返回首页">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M19 12H5M12 19l-7-7 7-7"/>
      </svg>
      <span>返回</span>
    </RouterLink>
    <GraphView />
  </div>
</template>

<style scoped>
.graph-page {
  width: 100%;
  height: 100%;
  position: relative;
  background: #080812;
}

.back-btn {
  position: absolute;
  top: 14px;
  left: 16px;
  z-index: 30;
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 6px 12px;
  border-radius: 8px;
  background: rgba(14, 14, 32, 0.8);
  backdrop-filter: blur(12px);
  border: 1px solid rgba(255, 255, 255, 0.06);
  color: #8a8aa0;
  font-size: 0.8rem;
  text-decoration: none;
  cursor: pointer;
  transition: all 0.2s;
  font-family: inherit;
}

.back-btn:hover {
  color: #d0d0e0;
  background: rgba(14, 14, 32, 0.95);
  border-color: rgba(255, 255, 255, 0.12);
}

.back-btn svg {
  width: 16px;
  height: 16px;
  flex-shrink: 0;
}
</style>
