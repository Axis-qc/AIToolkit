import { createRouter, createWebHistory } from 'vue-router'
import HomeView from '@/views/HomeView.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/',
      name: 'home',
      component: HomeView,
    },
    {
      path: '/graph',
      name: 'graph',
      component: () => import('@/views/GraphPageView.vue'),
    },
    {
      path: '/tools',
      name: 'tools',
      component: () => import('@/views/ToolsView.vue'),
    },
    {
      path: '/mc-panel',
      name: 'mc-panel',
      component: () => import('@/views/McPanelView.vue'),
    },
    {
      path: '/games',
      name: 'games',
      component: () => import('@/views/GamesView.vue'),
    },
    {
      path: '/games/colony-idle',
      name: 'colony-idle',
      component: () => import('@/games/colonyIdle/ColonyIdleView.vue'),
    },
    {
      path: '/games/voxel4x',
      name: 'voxel4x',
      component: () => import('@/games/voxel4x/Voxel4xView.vue'),
    },
    {
      path: '/settings',
      name: 'settings',
      component: () => import('@/views/SettingsView.vue'),
    },
  ],
})

export default router
