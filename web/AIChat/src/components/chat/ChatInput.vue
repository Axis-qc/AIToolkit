<script setup lang="ts">
import { ref, computed } from 'vue'
import { isStreaming, isArchiving, isReadonly, activeConvId, messages, contextTokens, triggerArchive, send, stop } from '@/stores/chat'

const input = ref('')

const showArchive = computed(() => activeConvId.value && !isArchiving.value && !isStreaming.value && messages.value.length > 0 && !isReadonly.value)

function onSubmit() {
  const text = input.value.trim()
  if (!text) return
  send(text)
  input.value = ''
}

function onArchive() {
  if (activeConvId.value) triggerArchive(activeConvId.value)
}
</script>

<template>
  <div class="chat-input-bar">
    <div v-if="isReadonly" class="readonly-banner">归档对话，只可查看</div>
    <template v-else>
    <div v-if="contextTokens > 0" class="token-bar">
      <span class="token-count">{{ contextTokens.toLocaleString() }}</span>
      <span class="token-sep">/</span>
      <span class="token-max">200,000</span>
      <span class="token-label">tokens</span>
    </div>
    <div class="input-wrapper">
      <button
        v-if="showArchive"
        class="archive-btn"
        @click="onArchive"
        title="整理记忆并归档"
      >
        整理记忆
      </button>
      <input
        v-model="input"
        :disabled="isStreaming || isArchiving"
        type="text"
        :placeholder="isArchiving ? '正在整理记忆...' : isStreaming ? 'AI 回复中...' : '输入消息...'"
        class="chat-input"
        @keydown.enter.exact.prevent="onSubmit"
      />
      <button
        v-if="isStreaming"
        class="stop-btn"
        @click="stop"
      >
        停止
      </button>
      <button
        v-else
        :disabled="!input.trim() || isArchiving"
        class="send-btn"
        @click="onSubmit"
      >
        发送
      </button>
    </div>
    </template>
  </div>
</template>

<style scoped>
.chat-input-bar {
  padding: 1rem 1.5rem;
  border-top: 1px solid rgba(255, 255, 255, 0.05);
  flex-shrink: 0;
  background: rgba(0, 0, 0, 0.15);
}

.token-bar {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 0.2rem;
  margin-bottom: 0.5rem;
  font-size: 0.68rem;
}

.token-count {
  color: #7c7c90;
  font-variant-numeric: tabular-nums;
}

.token-sep,
.token-max {
  color: #404050;
}

.token-label {
  color: #404050;
  margin-left: 0.15rem;
}

.input-wrapper {
  display: flex;
  gap: 0.6rem;
  align-items: center;
}

.chat-input {
  flex: 1;
  padding: 0.7rem 1rem;
  background: rgba(255, 255, 255, 0.04);
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 10px;
  color: #e0e0e8;
  font-size: 0.9rem;
  font-family: inherit;
  outline: none;
  transition: border-color 0.2s;
}

.chat-input::placeholder {
  color: #4a4a5a;
}

.chat-input:focus {
  border-color: rgba(124, 92, 252, 0.4);
}

.chat-input:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.send-btn {
  padding: 0.7rem 1.4rem;
  background: rgba(124, 92, 252, 0.15);
  border: 1px solid rgba(124, 92, 252, 0.3);
  border-radius: 10px;
  color: #a78bfa;
  font-size: 0.9rem;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.2s;
  font-family: inherit;
  flex-shrink: 0;
}

.send-btn:hover:not(:disabled) {
  background: rgba(124, 92, 252, 0.25);
  border-color: rgba(124, 92, 252, 0.5);
}

.send-btn:disabled {
  opacity: 0.3;
  cursor: not-allowed;
}

.stop-btn {
  padding: 0.7rem 1.4rem;
  background: rgba(239, 68, 68, 0.12);
  border: 1px solid rgba(239, 68, 68, 0.25);
  border-radius: 10px;
  color: #f87171;
  font-size: 0.9rem;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.2s;
  font-family: inherit;
  flex-shrink: 0;
}

.stop-btn:hover {
  background: rgba(239, 68, 68, 0.2);
  border-color: rgba(239, 68, 68, 0.4);
}

.archive-btn {
  padding: 0.7rem 1rem;
  background: rgba(245, 158, 11, 0.1);
  border: 1px solid rgba(245, 158, 11, 0.25);
  border-radius: 10px;
  color: #f59e0b;
  font-size: 0.82rem;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.2s;
  font-family: inherit;
  flex-shrink: 0;
}

.archive-btn:hover {
  background: rgba(245, 158, 11, 0.18);
  border-color: rgba(245, 158, 11, 0.4);
}

.readonly-banner {
  text-align: center;
  padding: 0.7rem;
  color: #505060;
  font-size: 0.82rem;
  border: 1px dashed rgba(255, 255, 255, 0.06);
  border-radius: 8px;
}
</style>
