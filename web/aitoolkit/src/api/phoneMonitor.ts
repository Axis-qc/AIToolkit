export interface PhoneProc {
  pid: string
  cpu: string
  mem: string
  rss: number
  comm: string
}

export interface PhoneDisk {
  mnt: string
  total: number
  used: number
  avail: number
}

export interface PhoneStatus {
  ok: boolean
  err: string
  ts: number
  cpu?: number | null
  load: string[]
  mem: Record<string, number>
  disk: PhoneDisk[]
  procs: PhoneProc[]
  uptime_s?: number | null
  nproc?: number | null
  cores?: number[] | null
  cpu_src?: 'stat' | 'ps' | null
  cpu_sum_raw?: number | null
  rx_kb_s?: number | null
  tx_kb_s?: number | null
  hist: { cpu: number[]; mem: number[] }
}

export async function fetchPhoneStatus(): Promise<PhoneStatus> {
  const res = await fetch('/api/phone/status', { cache: 'no-store' })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json()
}