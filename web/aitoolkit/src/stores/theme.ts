import { ref } from 'vue'

/**
 * 主题管理：主色从后端持久化加载，派生高光/暗色后注入 :root CSS 变量。
 * 全站配色由 --accent / --accent-bright / --accent-deep / --line 等变量驱动。
 */

export const DEFAULT_ACCENT = '#d4af37'

/** 快捷预设色板（名字 → 主色 hex） */
export const PRESETS: Array<{ name: string; color: string }> = [
  { name: '经典金', color: '#d4af37' },
  { name: '星际青', color: '#22d3ee' },
  { name: '电子绿', color: '#34d399' },
  { name: '赛博紫', color: '#a78bfa' },
  { name: '科技蓝', color: '#60a5fa' },
  { name: '熔岩橙', color: '#fb923c' },
  { name: '霓虹粉', color: '#ec4899' },
  { name: '警示红', color: '#f87171' },
  { name: '白银', color: '#cbd5e1' },
]

export const currentAccent = ref<string>(DEFAULT_ACCENT)

/** 校验并规范化主色：合法返回小写 #rrggbb，非法返回 null */
export function normalizeAccent(hex: string): string | null {
  let h = (hex || '').trim()
  if (!/^#?[0-9a-fA-F]{3}$|^#?[0-9a-fA-F]{6}$/.test(h)) return null
  h = h.replace('#', '')
  if (h.length === 3) h = h.split('').map(c => c + c).join('')
  return `#${h.toLowerCase()}`
}

/** 两个颜色按权重混合：weight 为 a 的占比（0-1） */
function mix(a: string, b: string, weight: number): string {
  const ca = parseInt(a.slice(1), 16)
  const cb = parseInt(b.slice(1), 16)
  const c = (shift: number) =>
    Math.round(((ca >> shift) & 255) * weight + ((cb >> shift) & 255) * (1 - weight))
      .toString(16).padStart(2, '0')
  return `#${c(16)}${c(8)}${c(0)}`
}

/** 把主色应用到 :root，派生高光/暗色并注入（实时预览用） */
export function applyTheme(accent: string) {
  const a = normalizeAccent(accent) || DEFAULT_ACCENT
  currentAccent.value = a
  const root = document.documentElement
  root.style.setProperty('--accent', a)
  root.style.setProperty('--accent-bright', mix(a, '#ffffff', 0.3))
  root.style.setProperty('--accent-deep', mix(a, '#000000', 0.4))
}

/** 启动时从后端加载并应用主题 */
export async function initTheme(): Promise<void> {
  try {
    const res = await fetch('/api/theme')
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const data = await res.json()
    applyTheme(data?.accent || DEFAULT_ACCENT)
  } catch {
    applyTheme(DEFAULT_ACCENT)
  }
}

/** 保存主色到后端（成功即应用） */
export async function saveTheme(accent: string): Promise<{ ok: boolean; error?: string }> {
  const a = normalizeAccent(accent)
  if (!a) return { ok: false, error: '无效的主色格式' }
  try {
    const res = await fetch('/api/theme', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ accent: a }),
    })
    const data = await res.json()
    if (data?.ok) applyTheme(a)
    return data?.ok ? { ok: true } : { ok: false, error: data?.error || '保存失败' }
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : String(e) }
  }
}

/** 恢复默认主题（同时持久化） */
export async function resetTheme(): Promise<void> {
  applyTheme(DEFAULT_ACCENT)
  await saveTheme(DEFAULT_ACCENT)
}
