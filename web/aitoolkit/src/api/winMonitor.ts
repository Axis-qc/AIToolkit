import type { PhoneStatus } from './phoneMonitor'

export async function fetchWinStatus(): Promise<PhoneStatus> {
  const res = await fetch('/api/win/status', { cache: 'no-store' })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json()
}
