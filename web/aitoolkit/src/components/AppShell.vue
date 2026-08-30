<script setup lang="ts">
import { computed, ref } from 'vue'
import { RouterLink, useRoute } from 'vue-router'

const route = useRoute()
const collapsed = ref(true)

function toggleSidebar() {
  collapsed.value = !collapsed.value
}

const navItems = [
  { to: '/', label: '总览', key: 'overview' },
  { to: '/graph', label: '知识图谱', key: 'graph' },
  { to: '/tools', label: '网页工具', key: 'tools' },
  { to: '/mc-panel', label: 'MC 面板', key: 'mc' },
  { to: '/games', label: '小游戏', key: 'games' },
]

const pageTitle = computed(() => {
  if (route.name === 'graph') return '知识图谱'
  if (route.name === 'tools') return '工具中心'
  if (route.name === 'mc-panel') return 'MC 面板'
  if (route.name === 'colony-idle') return '殖民地放置'
  if (route.name === 'voxel4x') return '体素四叉'
  if (route.name === 'games') return '休闲模块'
  if (route.name === 'settings') return '系统设置'
  return '运行总览'
})
</script>

<template>
  <div :class="['app-shell', { 'is-collapsed': collapsed }]">
    <aside class="app-sidebar">
      <RouterLink to="/" class="brand" aria-label="返回总览">
        <span class="brand-mark"><i></i><i></i><i></i></span>
        <span class="brand-copy">
          <strong>AITK</strong>
          <small>LOCAL OPS</small>
        </span>
      </RouterLink>
      <button
        class="sidebar-toggle"
        type="button"
        :aria-label="collapsed ? '展开侧边导航' : '收纳侧边导航'"
        :title="collapsed ? '展开导航' : '收纳导航'"
        @click="toggleSidebar"
      >
        <span>{{ collapsed ? '›' : '‹' }}</span>
      </button>

      <div class="sidebar-label">控制台</div>
      <nav class="app-nav" aria-label="主导航">
        <RouterLink
          v-for="item in navItems"
          :key="item.to"
          :to="item.to"
          class="app-nav-item"
          active-class="is-active"
          :title="item.label"
        >
          <svg class="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <template v-if="item.key === 'overview'">
              <rect x="3" y="3" width="7" height="7" rx="1" /><rect x="14" y="3" width="7" height="7" rx="1" /><rect x="3" y="14" width="7" height="7" rx="1" /><rect x="14" y="14" width="7" height="7" rx="1" />
            </template>
            <template v-else-if="item.key === 'graph'">
              <circle cx="6" cy="6" r="2.5" /><circle cx="18" cy="6" r="2.5" /><circle cx="12" cy="18" r="2.5" /><path d="m8.2 7.3 2.2 8M15.8 7.3l-2.2 8M8.5 6h7" />
            </template>
            <template v-else-if="item.key === 'tools'">
              <path d="m14.7 6.3 3-3 3 3-3 3M4 20l9.7-9.7M13.5 4.5a4.4 4.4 0 0 0-5.7 5.7L3 15v5h5l4.8-4.8a4.4 4.4 0 0 0 5.7-5.7" />
            </template>
            <template v-else-if="item.key === 'mc'">
              <rect x="3" y="4" width="18" height="16" rx="2" /><path d="M7 9h2M7 13h2M15 9h2M15 13h2" />
            </template>
            <template v-else>
              <path d="M4 8h16M6 5h.01M10 5h.01M14 5h.01M5 8v10a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8M9 12h6M9 16h4" />
            </template>
          </svg>
          <span class="nav-label">{{ item.label }}</span>
          <span class="nav-arrow">↗</span>
        </RouterLink>
      </nav>

      <div class="sidebar-spacer"></div>

      <RouterLink to="/settings" class="system-card" active-class="is-active">
        <span class="status-ring"></span>
        <span>
          <strong>本地节点在线</strong>
          <small>所有数据仅在本机处理</small>
        </span>
      </RouterLink>
      <div class="sidebar-foot">v0.1.0 <span>/</span> MONITORING SUITE</div>
    </aside>

    <section class="app-main">
      <header class="app-topbar">
        <div>
          <div class="topbar-kicker">AITK / OBSERVABILITY</div>
          <h1>{{ pageTitle }}</h1>
        </div>
        <div class="topbar-meta">
          <span class="live-dot"></span>
          <span>LOCAL ENVIRONMENT</span>
          <span class="meta-separator">·</span>
          <span>{{ new Date().toLocaleTimeString('zh-CN', { hour12: false }) }}</span>
        </div>
      </header>
      <main class="app-content">
        <slot />
      </main>
    </section>
  </div>
</template>
