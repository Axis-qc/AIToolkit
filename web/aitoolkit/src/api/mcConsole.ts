// MC 服务器面板：RCON 控制台 API（/api/mc）
export interface McStatus {
  ok: boolean
  online: boolean
  players?: number | null
  players_max?: number | null
  names?: string[]
  raw?: string
  error?: string
}

export interface McCmdResult {
  ok: boolean
  output?: string
  error?: string
}

function authHeaders(): Record<string, string> {
  return {
    'Content-Type': 'application/json',
    'X-MC-Auth': localStorage.getItem('mcAuth') || '',
  }
}

/** 校验网页访问密码；成功则把 token 存入 localStorage */
export async function mcAuth(token: string): Promise<boolean> {
  const res = await fetch('/api/mc/auth', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ token }),
  })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  const j = await res.json()
  if (j.ok) localStorage.setItem('mcAuth', token)
  return !!j.ok
}

export function mcLogout(): void {
  localStorage.removeItem('mcAuth')
}

export async function fetchMcStatus(): Promise<McStatus> {
  const res = await fetch('/api/mc/status', { cache: 'no-store', headers: authHeaders() })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json()
}

export async function sendMcCommand(command: string): Promise<McCmdResult> {
  const res = await fetch('/api/mc/cmd', {
    method: 'POST',
    headers: authHeaders(),
    body: JSON.stringify({ command }),
  })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json()
}
