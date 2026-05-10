<script setup lang="ts">
import { watch, nextTick, ref, computed, onMounted } from 'vue'
import { messages, isStreaming, isArchiving, archiveSummary } from '@/stores/chat'

const containerRef = ref<HTMLElement | null>(null)
const expandedReasoning = ref<Set<number>>(new Set())

const displayedMessages = computed(() => messages.value.slice(-100))

function scrollToBottom() {
  nextTick(() => {
    if (containerRef.value) {
      containerRef.value.scrollTop = containerRef.value.scrollHeight
    }
  })
}

function toggleReasoning(msgId: number) {
  const s = expandedReasoning.value
  s.has(msgId) ? s.delete(msgId) : s.add(msgId)
}

// 页面初始化 / 刷新时，消息加载完成后滚动到底部
onMounted(() => {
  // 延迟一小段时间等待异步消息加载完成
  const timer = setInterval(() => {
    if (messages.value.length > 0 && containerRef.value) {
      scrollToBottom()
      clearInterval(timer)
    }
  }, 50)
  // 安全上限：最多等 3 秒
  setTimeout(() => clearInterval(timer), 3000)
})

// 新消息到达时滚动
watch(
  () => messages.value.length,
  () => scrollToBottom(),
)

// 流式输出（消息内容增长）时滚动
watch(
  () => {
    const last = messages.value[messages.value.length - 1]
    return last ? last.content.length + last.reasoning.length : 0
  },
  () => scrollToBottom(),
)

function toolArgsStr(args: Record<string, unknown>): string {
  return JSON.stringify(args, null, 0).slice(0, 80)
}
</script>

<template>
  <div ref="containerRef" class="chat-window">
    <div v-if="messages.length === 0" class="empty-state">
      <div class="empty-icon">&#x25C8;</div>
      <h3>知识图谱 AI 对话</h3>
      <p>发送消息开始对话，AI 会自动检索和更新记忆</p>
    </div>

    <div v-else class="message-list">
      <div
        v-for="msg in displayedMessages"
        :key="msg.id"
        :class="['message', msg.role]"
      >
        <div class="message-bubble">
          <!-- Reasoning (thinking process) -->
          <div v-if="msg.reasoning" class="reasoning-block">
            <button
              class="reasoning-toggle"
              @click="toggleReasoning(msg.id)"
            >
              <span class="reasoning-dot" />
              <span>思考过程</span>
              <span class="reasoning-chevron">{{ expandedReasoning.has(msg.id) ? '▼' : '▶' }}</span>
            </button>
            <div
              v-if="expandedReasoning.has(msg.id) || msg.isStreaming"
              class="reasoning-content"
            >
              {{ msg.reasoning }}
            </div>
          </div>

          <!-- Tool calls -->
          <div
            v-if="msg.toolCalls.length > 0"
            class="tool-calls"
          >
            <div
              v-for="(tc, i) in msg.toolCalls"
              :key="i"
              :class="['tool-call', { loading: tc.loading, done: tc.result }]"
            >
              <div class="tool-call-header">
                <span class="tool-dot" />
                <span class="tool-name">{{ tc.name }}</span>
                <span v-if="tc.loading" class="tool-spinner" />
                <span v-else-if="tc.result" class="tool-check">&#x2713;</span>
              </div>
              <div class="tool-args">{{ toolArgsStr(tc.arguments) }}</div>
              <div v-if="tc.result" class="tool-result">{{ tc.result }}</div>
            </div>
          </div>

          <div v-if="msg.content" class="msg-content" v-text="msg.content" />
          <div v-else-if="!msg.isStreaming" class="msg-empty">(empty response)</div>
          <div v-if="msg.role === 'assistant' && (msg.promptTokens || msg.completionTokens)" class="msg-tokens">
            {{ (msg.promptTokens + msg.completionTokens).toLocaleString() }} tokens
          </div>
          <span v-if="msg.isStreaming && isStreaming" class="cursor-blink">|</span>
        </div>
      </div>
    </div>

    <!-- 归档提示放在消息列表下方 -->
    <div v-if="isArchiving" class="archiving-banner">
      <span class="archiving-spinner" />
      <span>正在整理对话记忆，生成知识图谱...</span>
    </div>
    <div v-else-if="archiveSummary" class="archive-result">
      <span class="archive-icon">&#x2713;</span>
      <span>{{ archiveSummary }}</span>
    </div>
  </div>
</template>

<style scoped>
.chat-window {
  flex: 1;
  overflow-y: auto;
  padding: 1.5rem;
}

.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100%;
  gap: 0.75rem;
  color: #4a4a5a;
}

.empty-icon {
  font-size: 3rem;
  color: #7c5cfc44;
}

.empty-state h3 {
  font-size: 1.2rem;
  font-weight: 600;
  color: #6b6b80;
}

.empty-state p {
  font-size: 0.9rem;
}

.message-list {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.message {
  display: flex;
  max-width: 85%;
}

.message.user {
  align-self: flex-end;
}

.message.assistant {
  align-self: flex-start;
}

.message-bubble {
  padding: 0.75rem 1rem;
  border-radius: 12px;
  line-height: 1.6;
  font-size: 0.92rem;
  word-break: break-word;
  white-space: pre-wrap;
}

.message.user .message-bubble {
  background: rgba(124, 92, 252, 0.15);
  color: #d8d0f0;
  border: 1px solid rgba(124, 92, 252, 0.2);
  border-bottom-right-radius: 4px;
}

.message.assistant .message-bubble {
  background: rgba(255, 255, 255, 0.04);
  color: #c8c8d8;
  border: 1px solid rgba(255, 255, 255, 0.06);
  border-bottom-left-radius: 4px;
  min-width: 0;
}

.msg-content {
  white-space: pre-wrap;
}

.msg-content :deep(p) {
  margin: 0 0 0.5rem;
}

.msg-content :deep(p:last-child) {
  margin-bottom: 0;
}

.msg-content :deep(code) {
  background: rgba(255, 255, 255, 0.06);
  padding: 0.15em 0.4em;
  border-radius: 4px;
  font-size: 0.85em;
}

.msg-content :deep(pre) {
  background: rgba(255, 255, 255, 0.05);
  padding: 0.75rem;
  border-radius: 8px;
  overflow-x: auto;
}

.msg-content :deep(pre code) {
  background: none;
  padding: 0;
}

.msg-tokens {
  margin-top: 0.4rem;
  padding-top: 0.3rem;
  border-top: 1px solid rgba(255, 255, 255, 0.04);
  font-size: 0.68rem;
  color: #3a3a4a;
  text-align: right;
}

/* ── Reasoning ── */
.reasoning-block {
  margin-bottom: 0.6rem;
  border: 1px solid rgba(255, 255, 255, 0.04);
  border-radius: 8px;
  overflow: hidden;
}

.reasoning-toggle {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  width: 100%;
  padding: 0.4rem 0.6rem;
  background: rgba(0, 0, 0, 0.15);
  border: none;
  color: #6b6b80;
  font-size: 0.76rem;
  font-family: inherit;
  cursor: pointer;
  transition: color 0.2s;
}

.reasoning-toggle:hover {
  color: #9090a8;
}

.reasoning-dot {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: #f59e0b;
  flex-shrink: 0;
}

.reasoning-chevron {
  margin-left: auto;
  font-size: 0.65rem;
  color: #505060;
}

.reasoning-content {
  padding: 0.5rem 0.65rem;
  color: #5a5a6e;
  font-size: 0.76rem;
  line-height: 1.5;
  white-space: pre-wrap;
  border-top: 1px solid rgba(255, 255, 255, 0.03);
  max-height: 160px;
  overflow-y: auto;
}

/* ── Tool Calls ── */
.tool-calls {
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
  margin-bottom: 0.6rem;
  padding-bottom: 0.6rem;
  border-bottom: 1px solid rgba(255, 255, 255, 0.06);
}

.tool-call {
  background: rgba(0, 0, 0, 0.2);
  border-radius: 8px;
  padding: 0.5rem 0.65rem;
  border: 1px solid rgba(255, 255, 255, 0.05);
  font-size: 0.78rem;
  transition: border-color 0.3s;
}

.tool-call.done {
  border-color: rgba(52, 211, 153, 0.15);
}

.tool-call.loading {
  border-color: rgba(124, 92, 252, 0.2);
}

.tool-call-header {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  margin-bottom: 0.2rem;
}

.tool-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #7c5cfc;
  flex-shrink: 0;
}

.tool-call.done .tool-dot {
  background: #34d399;
}

.tool-name {
  font-weight: 600;
  color: #a78bfa;
  font-size: 0.76rem;
}

.tool-call.done .tool-name {
  color: #6ee7b7;
}

.tool-spinner {
  width: 12px;
  height: 12px;
  border: 2px solid rgba(124, 92, 252, 0.3);
  border-top-color: #a78bfa;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
  margin-left: auto;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

.tool-check {
  margin-left: auto;
  color: #34d399;
  font-size: 0.75rem;
}

.tool-args {
  color: #5a5a6e;
  font-family: 'Consolas', 'Courier New', monospace;
  font-size: 0.72rem;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.tool-result {
  margin-top: 0.3rem;
  padding-top: 0.3rem;
  border-top: 1px solid rgba(255, 255, 255, 0.04);
  color: #7c7c90;
  font-size: 0.75rem;
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 120px;
  overflow-y: auto;
}

.cursor-blink {
  color: #a78bfa;
  animation: blink 1s step-end infinite;
}

@keyframes blink {
  0%, 100% { opacity: 1; }
  50% { opacity: 0; }
}

.archiving-banner {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  padding: 0.6rem 1rem;
  margin: 0.5rem 0;
  background: rgba(245, 158, 11, 0.08);
  border: 1px solid rgba(245, 158, 11, 0.2);
  border-radius: 8px;
  color: #f59e0b;
  font-size: 0.82rem;
  flex-shrink: 0;
}

.archiving-spinner {
  width: 14px;
  height: 14px;
  border: 2px solid rgba(245, 158, 11, 0.25);
  border-top-color: #f59e0b;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
  flex-shrink: 0;
}

.archive-result {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.6rem 1rem;
  margin: 0.5rem 0;
  background: rgba(52, 211, 153, 0.06);
  border: 1px solid rgba(52, 211, 153, 0.15);
  border-radius: 8px;
  color: #34d399;
  font-size: 0.82rem;
  flex-shrink: 0;
}

.archive-icon {
  font-size: 0.9rem;
  flex-shrink: 0;
}
</style>
