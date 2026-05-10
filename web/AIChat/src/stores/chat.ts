import { ref, computed } from 'vue'
import { sendChat, type ChatMessage, listConversations, getConversation, deleteConversation, updateGraph, type ConversationItem } from '@/api/chat'

export interface ToolCall {
  name: string
  arguments: Record<string, unknown>
  result?: string
  loading: boolean
}

export interface Message {
  id: number
  role: 'user' | 'assistant' | 'tool'
  content: string
  reasoning: string
  toolCalls: ToolCall[]
  isStreaming: boolean
  promptTokens: number
  completionTokens: number
}

let nextId = 0

export const MAIN_CONV_ID = "main"

export const conversations = ref<ConversationItem[]>([])
export const archivedConversations = ref<ConversationItem[]>([])
export const activeConvId = ref<string | null>(null)
export const isReadonly = ref(false)
export const messages = ref<Message[]>([])
export const isStreaming = ref(false)
export const isArchiving = ref(false)
export const archiveSummary = ref('')
export const contextTokens = ref(0)
export const error = ref<string | null>(null)

let abortCtrl: AbortController | null = null

function addMessage(role: 'user' | 'assistant', content = ''): Message {
  const msg: Message = {
    id: ++nextId,
    role,
    content,
    reasoning: '',
    toolCalls: [],
    isStreaming: role === 'assistant',
    promptTokens: 0,
    completionTokens: 0,
  }
  messages.value.push(msg)
  return messages.value[messages.value.length - 1]!!
}

export async function fetchConversations() {
  try {
    const all = await listConversations()
    archivedConversations.value = all.filter(c => c.id !== MAIN_CONV_ID)
  } catch {
    // silently fail
  }
}

export async function selectConversation(id: string) {
  if (isStreaming.value) return
  try {
    const data = await getConversation(id)
    messages.value = data.messages.map((m, i) => ({
      id: ++nextId,
      role: m.role as 'user' | 'assistant',
      content: m.content || '',
      reasoning: (m as Record<string, string>).reasoning_content || '',
      toolCalls: [],
      isStreaming: false,
      promptTokens: Number((m as Record<string, unknown>).prompt_tokens) || 0,
      completionTokens: Number((m as Record<string, unknown>).completion_tokens) || 0,
    }))
    activeConvId.value = id
    isReadonly.value = id !== MAIN_CONV_ID
    error.value = null
  } catch {
    error.value = '无法加载对话'
  }
}

export function selectMainConversation() {
  if (isStreaming.value) return
  newConversationInternal()
  activeConvId.value = MAIN_CONV_ID
  isReadonly.value = false
  loadMainMessages()
}

async function loadMainMessages() {
  try {
    const data = await getConversation(MAIN_CONV_ID)
    messages.value = data.messages.map((m, i) => ({
      id: ++nextId,
      role: m.role as 'user' | 'assistant',
      content: m.content || '',
      reasoning: (m as Record<string, string>).reasoning_content || '',
      toolCalls: [],
      isStreaming: false,
      promptTokens: Number((m as Record<string, unknown>).prompt_tokens) || 0,
      completionTokens: Number((m as Record<string, unknown>).completion_tokens) || 0,
    }))
  } catch {
    // main conversation might not exist yet
  }
}

export function newConversation() {
  selectMainConversation()
}

function newConversationInternal() {
  messages.value = []
  activeConvId.value = null
  contextTokens.value = 0
  error.value = null
  isReadonly.value = false
  nextId = 0
}

export async function removeConversation(id: string) {
  if (id === MAIN_CONV_ID) return
  try {
    await deleteConversation(id)
  } catch {
    // ignore
  }
  if (activeConvId.value === id) {
    selectMainConversation()
  }
  await fetchConversations()
}

export async function triggerArchive(convId: string) {
  isArchiving.value = true
  error.value = null
  try {
    const result = await updateGraph(convId)
    archiveSummary.value = result.summary
  } catch (e: unknown) {
    error.value = e instanceof Error ? e.message : '归档失败'
  } finally {
    isArchiving.value = false
  }
  newConversation()
  await fetchConversations()
}

export async function send(content: string) {
  if (!content.trim() || isStreaming.value || isArchiving.value) return

  error.value = null
  archiveSummary.value = ''
  const userMsg = addMessage('user', content)
  const assistantMsg = addMessage('assistant')
  isStreaming.value = true

  const history: ChatMessage[] = messages.value
    .slice(0, -1)
    .filter((m) => m.role !== 'tool')
    .filter((m) => !m.isStreaming || m === assistantMsg)
    .map((m) => ({ role: m.role, content: m.content }))

  abortCtrl = new AbortController()

  try {
    for await (const event of sendChat(history, MAIN_CONV_ID, abortCtrl.signal)) {
      switch (event.type) {
        case 'archived': {
          const idx = messages.value.indexOf(userMsg)
          if (idx > 0) messages.value.splice(0, idx)
          fetchConversations()
          break
        }

        case 'archive_required':
          messages.value.pop()
          messages.value.pop()
          error.value = '对话上下文已达上限，正在自动归档...'
          isStreaming.value = false
          if (activeConvId.value) {
            await triggerArchive(activeConvId.value)
          }
          send(content)
          return

        case 'token':
          assistantMsg.content += event.content || ''
          break
        case 'reasoning':
          assistantMsg.reasoning += event.content || ''
          break
        case 'tool_call':
          assistantMsg.toolCalls.push({
            name: event.name!,
            arguments: event.arguments || {},
            loading: true,
          })
          break
        case 'tool_result': {
          const pending = assistantMsg.toolCalls.find((tc) => tc.loading && !tc.result)
          if (pending) {
            pending.result = event.content
            pending.loading = false
          }
          break
        }
        case 'error':
          error.value = event.content || 'Unknown error'
          break
        case 'done':
          assistantMsg.promptTokens = event.prompt_tokens || 0
          assistantMsg.completionTokens = event.completion_tokens || 0
          contextTokens.value = event.prompt_tokens || contextTokens.value
          break
      }
    }
  } catch (e: unknown) {
    if (e instanceof DOMException && e.name === 'AbortError') {
      assistantMsg.content += '\n\n[已取消]'
    } else {
      const msg = e instanceof Error ? e.message : String(e)
      error.value = msg
      assistantMsg.content ||= `[连接后端失败]\n${msg}`
    }
  } finally {
    isStreaming.value = false
    assistantMsg.isStreaming = false
    abortCtrl = null
    fetchConversations()
  }
}

export function stop() {
  abortCtrl?.abort()
}
