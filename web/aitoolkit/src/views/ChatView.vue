<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { RouterLink } from 'vue-router'
import ChatWindow from '@/components/chat/ChatWindow.vue'
import ChatInput from '@/components/chat/ChatInput.vue'
import GraphCanvas from '@/components/graph/GraphCanvas.vue'
import { archivedConversations, activeConvId, isReadonly, MAIN_CONV_ID, messages, error, send, fetchConversations, selectConversation, selectMainConversation, isStreaming, contextTokens, totalCompletionTokens } from '@/stores/chat'

const activeView = ref<'conversations' | 'memory' | 'settings'>('conversations')
const sidebarHovered = ref(false)

const navItems = [
  {
    key: 'conversations' as const,
    label: '对话列表',
    icon: `<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>`,
  },
  {
    key: 'memory' as const,
    label: '记忆图谱',
    icon: `<circle cx="12" cy="5" r="2"/><circle cx="5" cy="19" r="2"/><circle cx="19" cy="19" r="2"/><path d="M12 7v4l2.5 2.5"/><path d="M10.5 7.5l-4.5 9"/><path d="M13.5 7.5l4.5 9"/>`,
  },
  {
    key: 'settings' as const,
    label: '设置',
    icon: `<circle cx="12" cy="12" r="3"/><path d="M12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M1 12h2M21 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42"/>`,
  },
]

function selectView(key: 'conversations' | 'memory' | 'settings') {
  activeView.value = key
}

function formatDate(iso: string): string {
  if (!iso) return ''
  return iso.slice(0, 10).replace(/^\d+-/, '')
}

onMounted(() => {
  selectMainConversation()
  fetchConversations()
})
</script>

<template>
  <div class="chat-page">
    <div
      class="left-zone"
      @mouseenter="sidebarHovered = true"
      @mouseleave="sidebarHovered = false"
    >
      <aside :class="['sidebar', { expanded: sidebarHovered }]">
        <RouterLink to="/" class="back-link" title="返回">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="back-icon">
            <path d="M19 12H5M12 19l-7-7 7-7"/>
          </svg>
          <span class="back-text">返回</span>
        </RouterLink>

        <nav class="nav-list">
          <button
            v-for="item in navItems"
            :key="item.key"
            :class="['nav-item', { active: activeView === item.key }]"
            :title="item.label"
            @click="selectView(item.key)"
          >
            <svg class="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" v-html="item.icon" />
            <span class="nav-label">{{ item.label }}</span>
          </button>
        </nav>
      </aside>

      <div
        v-if="activeView === 'conversations'"
        :class="['conv-panel', { expanded: sidebarHovered }]"
      >
        <div class="conv-panel-inner">
          <div class="section-label">主对话</div>
          <div
            :class="['conv-item', 'main-conv', { active: activeConvId === MAIN_CONV_ID || !activeConvId }]"
            @click="selectMainConversation()"
          >
            <div class="conv-title">知识图谱 AI 对话</div>
            <div class="conv-meta">
              <span class="conv-count">当前</span>
            </div>
          </div>
          <div v-if="archivedConversations.length > 0" class="section-label">归档记录</div>
          <div class="conv-list">
            <div
              v-for="conv in archivedConversations"
              :key="conv.id"
              :class="['conv-item', { active: activeConvId === conv.id }]"
              @click="selectConversation(conv.id)"
            >
              <div class="conv-title">{{ conv.title || '(无标题)' }}</div>
              <div class="conv-meta">
                <span class="conv-date">{{ formatDate(conv.updated_at) }}</span>
                <span class="conv-count">{{ conv.message_count }} 条</span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <div class="main-area">
      <template v-if="activeView === 'conversations'">
        <div v-if="error" class="error-banner">{{ error }}</div>
        <div class="chat-area">
          <header class="chat-header">
            <h2>{{ isReadonly ? (archivedConversations.find(c => c.id === activeConvId)?.title || '归档对话') : '知识图谱 AI 对话' }}</h2>
            <div v-if="contextTokens > 0" class="chat-total-tokens">
              <span class="total-label">上下文</span>
              <span class="total-val">{{ contextTokens.toLocaleString() }}</span>
              <span class="total-sep">+</span>
              <span class="total-label">输出</span>
              <span class="total-val">{{ totalCompletionTokens.toLocaleString() }}</span>
              <span class="total-sep">=</span>
              <span class="total-val total-sum">{{ (contextTokens + totalCompletionTokens).toLocaleString() }}</span>
              <span class="total-label">tokens</span>
            </div>
          </header>
          <ChatWindow />
          <ChatInput />
        </div>
      </template>

      <div v-else-if="activeView === 'memory'" class="graph-view">
        <GraphCanvas />
      </div>

      <div v-else-if="activeView === 'settings'" class="placeholder-view">
        <p>系统设置</p>
        <span>开发中...</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.chat-page {
  width: 100%;
  height: 100%;
  display: flex;
  background: #08080c;
  color: #e0e0e8;
  font-family: inherit;
}

.left-zone {
  display: flex;
  flex-shrink: 0;
  height: 100%;
}

.sidebar {
  display: flex;
  flex-direction: column;
  width: 56px;
  height: 100%;
  flex-shrink: 0;
  border-right: 1px solid rgba(255, 255, 255, 0.06);
  background: rgba(255, 255, 255, 0.015);
  transition: width 0.18s cubic-bezier(0.4, 0, 0.2, 1);
  overflow: hidden;
  padding: 0.75rem 0.5rem;
  gap: 0.5rem;
  will-change: width;
  contain: layout style;
}

.sidebar.expanded {
  width: 200px;
}

.back-link {
  display: flex;
  align-items: center;
  gap: 0.55rem;
  padding: 0.45rem 0.6rem;
  border-radius: 8px;
  color: #7c7c90;
  text-decoration: none;
  font-size: 0.85rem;
  transition: all 0.2s;
  white-space: nowrap;
  overflow: hidden;
}

.back-link:hover {
  background: rgba(255, 255, 255, 0.04);
  color: #c0c0d0;
}

.back-icon {
  width: 18px;
  height: 18px;
  flex-shrink: 0;
}

.back-text {
  opacity: 0;
  transition: opacity 0.15s;
}

.sidebar.expanded .back-text {
  opacity: 1;
}

.nav-list {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.nav-item {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  width: 100%;
  padding: 0.55rem 0.6rem;
  border: none;
  border-radius: 8px;
  background: transparent;
  color: #6b6b80;
  font-size: 0.88rem;
  cursor: pointer;
  transition: all 0.2s;
  font-family: inherit;
  text-align: left;
  white-space: nowrap;
  overflow: hidden;
}

.nav-item:hover {
  background: rgba(255, 255, 255, 0.03);
  color: #a0a0b8;
}

.nav-item.active {
  background: rgba(124, 92, 252, 0.1);
  color: #a78bfa;
}

.nav-icon {
  width: 20px;
  height: 20px;
  flex-shrink: 0;
}

.nav-label {
  opacity: 0;
  transition: opacity 0.15s;
}

.sidebar.expanded .nav-label {
  opacity: 1;
}

.main-area {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  contain: layout style;
  overflow: hidden;
}

.conv-panel {
  width: 0;
  height: 100%;
  flex-shrink: 0;
  border-right: 1px solid transparent;
  background: rgba(255, 255, 255, 0.01);
  transition: width 0.18s cubic-bezier(0.4, 0, 0.2, 1);
  overflow: hidden;
  will-change: width;
  contain: layout style;
}

.conv-panel.expanded {
  width: 260px;
  border-right-color: rgba(255, 255, 255, 0.05);
}

.conv-panel-inner {
  width: 260px;
  height: 100%;
  display: flex;
  flex-direction: column;
  padding: 0.75rem;
  gap: 0.4rem;
  overflow-y: auto;
}

.new-conv-btn {
  width: 100%;
  padding: 0.55rem;
  border: 1px dashed rgba(255, 255, 255, 0.1);
  border-radius: 8px;
  background: transparent;
  color: #7c7c90;
  font-size: 0.85rem;
  cursor: pointer;
  transition: all 0.2s;
  font-family: inherit;
  flex-shrink: 0;
}

.new-conv-btn:hover {
  border-color: rgba(124, 92, 252, 0.4);
  color: #a78bfa;
  background: rgba(124, 92, 252, 0.05);
}

.conv-list {
  display: flex;
  flex-direction: column;
  gap: 0.3rem;
}

.section-label {
  font-size: 0.7rem;
  font-weight: 600;
  color: #4a4a5a;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  padding: 0.4rem 0.75rem 0.2rem;
  flex-shrink: 0;
}

.main-conv {
  border: 1px solid rgba(124, 92, 252, 0.1);
}

.main-conv:hover {
  border-color: rgba(124, 92, 252, 0.25);
}

.main-conv.active {
  border-color: rgba(124, 92, 252, 0.35);
}

.conv-item {
  padding: 0.65rem 0.75rem;
  border-radius: 8px;
  cursor: pointer;
  transition: all 0.2s;
}

.conv-item:hover {
  background: rgba(255, 255, 255, 0.03);
}

.conv-item.active {
  background: rgba(124, 92, 252, 0.08);
}

.conv-title {
  font-size: 0.9rem;
  font-weight: 500;
  color: #c8c8d8;
  margin-bottom: 0.2rem;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.conv-meta {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-size: 0.75rem;
}

.conv-date {
  color: #505060;
  flex-shrink: 0;
}

.conv-count {
  color: #4a4a5a;
}

.conv-archived {
  color: #854d0e;
  background: rgba(245, 158, 11, 0.1);
  padding: 0.05rem 0.35rem;
  border-radius: 4px;
  font-size: 0.68rem;
}

.chat-area {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
  height: 100%;
}

.chat-header {
  padding: 1rem 1.5rem;
  border-bottom: 1px solid rgba(255, 255, 255, 0.05);
  flex-shrink: 0;
}

.chat-header h2 {
  font-size: 1.05rem;
  font-weight: 600;
  color: #d0d0dc;
  margin: 0;
}

.chat-total-tokens {
  display: flex;
  align-items: center;
  gap: 0.25rem;
  margin-top: 0.35rem;
  font-size: 0.68rem;
  font-variant-numeric: tabular-nums;
}

.total-label {
  color: #404050;
}

.total-val {
  color: #7c7c90;
}

.total-sep {
  color: #303040;
}

.total-sum {
  color: #a78bfa;
  font-weight: 600;
}

.placeholder-view {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 0.5rem;
  color: #4a4a5a;
}

.graph-view {
  flex: 1;
  min-height: 0;
}

.placeholder-view p {
  font-size: 1rem;
  font-weight: 500;
  color: #5a5a6e;
  margin: 0;
}

.placeholder-view span {
  font-size: 0.85rem;
}

.error-banner {
  margin: 0.5rem 1rem;
  padding: 0.6rem 1rem;
  background: rgba(239, 68, 68, 0.1);
  border: 1px solid rgba(239, 68, 68, 0.25);
  border-radius: 8px;
  color: #f87171;
  font-size: 0.82rem;
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
