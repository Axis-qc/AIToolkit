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
  memoryContext: string
  toolCalls: ToolCall[]
  isStreaming: boolean
  promptTokens: number
  completionTokens: number
}

const hot = import.meta.hot
const saved = (hot?.data ?? {}) as Record<string, unknown>

export const MAIN_CONV_ID = "main"

export const conversations = (saved.conversations as ReturnType<typeof ref<ConversationItem[]>>) ?? ref<ConversationItem[]>([])
export const archivedConversations = (saved.archivedConversations as ReturnType<typeof ref<ConversationItem[]>>) ?? ref<ConversationItem[]>([])
export const activeConvId = (saved.activeConvId as ReturnType<typeof ref<string | null>>) ?? ref<string | null>(null)
export const isReadonly = (saved.isReadonly as ReturnType<typeof ref<boolean>>) ?? ref(false)
export const messages = (saved.messages as ReturnType<typeof ref<Message[]>>) ?? ref<Message[]>([])
export const isStreaming = (saved.isStreaming as ReturnType<typeof ref<boolean>>) ?? ref(false)
export const isArchiving = (saved.isArchiving as ReturnType<typeof ref<boolean>>) ?? ref(false)
export const archiveSummary = (saved.archiveSummary as ReturnType<typeof ref<string>>) ?? ref('')
export const archiveSteps = (saved.archiveSteps as ReturnType<typeof ref<Array<{ name: string; args: Record<string, unknown>; result: string }>>>) ?? ref<Array<{ name: string; args: Record<string, unknown>; result: string }>>([])
export const contextTokens = (saved.contextTokens as ReturnType<typeof ref<number>>) ?? ref(0)
export const totalCompletionTokens = computed(() =>
  messages.value.reduce((sum, m) => sum + (m.completionTokens || 0), 0)
)
export const memoryContext = ref('')
export const error = (saved.error as ReturnType<typeof ref<string | null>>) ?? ref<string | null>(null)

let nextId: number = (saved.nextId as number | undefined) ?? 0
let abortCtrl: AbortController | null = (saved.abortCtrl as AbortController | null) ?? null

function addMessage(role: 'user' | 'assistant', content = ''): Message {
  const msg: Message = {
    id: ++nextId,
    role,
    content,
    reasoning: '',
    memoryContext: '',
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

/** 从后端原始消息重建前端 Message，合并 tool 消息的结果到 assistant 的 toolCalls */
function loadMessagesFromData(rawMessages: Array<Record<string, unknown>>): Message[] {
  // 先建 tool_call_id → result 映射
  const toolResults: Record<string, string> = {}
  for (const m of rawMessages) {
    if (m.role === 'tool' && m.tool_call_id) {
      toolResults[m.tool_call_id as string] = (m.content as string) || ''
    }
  }

  const result: Message[] = []
  for (const m of rawMessages) {
    if (m.role === 'tool') continue // tool 消息合并到 assistant 中，不单独渲染

    const role = (m.role as string) === 'user' ? 'user' : 'assistant'

    let toolCalls: ToolCall[] = []
    if (Array.isArray(m.tool_calls)) {
      toolCalls = (m.tool_calls as Array<Record<string, unknown>>).map(tc => {
        const func = (tc.function || {}) as Record<string, unknown>
        const name = (func.name as string) || ''
        let args: Record<string, unknown> = {}
        if (typeof func.arguments === 'string') {
          try { args = JSON.parse(func.arguments) } catch { /* ignore */ }
        } else if (func.arguments) {
          args = func.arguments as Record<string, unknown>
        }
        return {
          name,
          arguments: args,
          result: toolResults[tc.id as string] || undefined,
          loading: false,
        }
      })
    }

    result.push({
      id: ++nextId,
      role,
      content: (m.content as string) || '',
      reasoning: (m as Record<string, string>).reasoning_content || '',
      memoryContext: '',
      toolCalls,
      isStreaming: false,
      promptTokens: Number((m as Record<string, unknown>).prompt_tokens) || 0,
      completionTokens: Number((m as Record<string, unknown>).completion_tokens) || 0,
    })
  }
  return result
}

export async function selectConversation(id: string) {
  if (isStreaming.value) return
  try {
    const data = await getConversation(id)
    messages.value = loadMessagesFromData(data.messages as Array<Record<string, unknown>>)
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
    messages.value = loadMessagesFromData(data.messages as Array<Record<string, unknown>>)
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
    archiveSteps.value = result.steps || []
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
  archiveSteps.value = []
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
        case 'memory_context':
          assistantMsg.memoryContext = event.content || ''
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

if (hot) {
  hot.accept()
  hot.dispose(() => {
    hot.data.conversations = conversations
    hot.data.archivedConversations = archivedConversations
    hot.data.activeConvId = activeConvId
    hot.data.isReadonly = isReadonly
    hot.data.messages = messages
    hot.data.isStreaming = isStreaming
    hot.data.isArchiving = isArchiving
    hot.data.archiveSummary = archiveSummary
    hot.data.archiveSteps = archiveSteps
    hot.data.contextTokens = contextTokens
    hot.data.error = error
    hot.data.nextId = nextId
    hot.data.abortCtrl = abortCtrl
  })
}
