export interface ChatMessage {
  role: string
  content: string
}

export interface SSEEvent {
  type: 'token' | 'reasoning' | 'tool_call' | 'tool_result' | 'done' | 'error' | 'archive_required' | 'archived'
  content?: string
  name?: string
  arguments?: Record<string, unknown>
  tool_call_id?: string
  conversation_id?: string
  last_prompt_tokens?: number
  prompt_tokens?: number
  completion_tokens?: number
}

export interface ConversationItem {
  id: string
  title: string
  created_at: string
  updated_at: string
  archived: boolean
  message_count: number
}

export interface ConversationDetail {
  id: string
  title: string
  created_at: string
  updated_at: string
  archived: boolean
  messages: { role: string; content: string; tool_calls?: unknown; reasoning_content?: string }[]
}

export async function* sendChat(
  messages: ChatMessage[],
  conversationId: string | null,
  signal?: AbortSignal,
): AsyncGenerator<SSEEvent> {
  const response = await fetch('/api/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ messages, conversation_id: conversationId }),
    signal,
  })

  if (!response.ok) throw new Error(`HTTP ${response.status}: ${response.statusText}`)

  const reader = response.body?.getReader()
  if (!reader) throw new Error('No response body')

  const decoder = new TextDecoder()
  let buffer = ''

  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break

      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() || ''

      for (const line of lines) {
        const trimmed = line.trim()
        if (!trimmed.startsWith('data: ')) continue
        const data = trimmed.slice(6).trim()
        if (!data || data === '[DONE]') continue
        try {
          yield JSON.parse(data) as SSEEvent
        } catch {
          // skip malformed lines
        }
      }
    }
  } finally {
    reader.cancel().catch(() => {})
  }
}

async function api(path: string, init?: RequestInit) {
  const res = await fetch(path, init)
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res
}

export async function listConversations(): Promise<ConversationItem[]> {
  const res = await api('/api/conversations')
  const data = await res.json()
  return data.conversations
}

export async function getConversation(id: string): Promise<ConversationDetail> {
  const res = await api(`/api/conversations/${id}`)
  return res.json()
}

export async function deleteConversation(id: string): Promise<void> {
  await api(`/api/conversations/${id}`, { method: 'DELETE' })
}

export async function updateGraph(conversationId: string): Promise<{ success: boolean; summary: string }> {
  const res = await api('/api/chat/update-graph', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ conversation_id: conversationId }),
  })
  return res.json()
}
