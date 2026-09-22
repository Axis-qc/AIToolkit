<script setup lang="ts">
import { onMounted, onBeforeUnmount, ref, computed, watch } from 'vue'
import LineChart from '@/components/LineChart.vue'
import { fetchPhoneStatus, type PhoneStatus, type PhoneProc, type PhoneDisk } from '@/api/phoneMonitor'
import { fetchWinStatus } from '@/api/winMonitor'
import { mcAuth, mcLogout, fetchMcStatus, sendMcCommand } from '@/api/mcConsole'

/* ================= 认证门 ================= */
const authed = ref(localStorage.getItem('mcAuth') ? true : false)
const authPwd = ref('')
const authErr = ref('')
const authing = ref(false)

async function doAuth() {
  if (!authPwd.value) return
  authing.value = true
  authErr.value = ''
  try {
    const ok = await mcAuth(authPwd.value)
    if (ok) {
      authed.value = true
      authPwd.value = ''
      startPolling()
    } else {
      authErr.value = '密码错误'
    }
  } catch (e) {
    authErr.value = String(e)
  } finally {
    authing.value = false
  }
}

function logout() {
  mcLogout()
  authed.value = false
  stopPolling()
}

/* ================= 服务器状态（RCON） ================= */
const mc = ref<{ online: boolean; players?: number | null; players_max?: number | null; names?: string[]; error?: string } | null>(null)
const mcNames = computed(() => (mc.value?.names || []).join('、'))
const mcOnline = computed(() => !!mc.value?.online)
let mcTimer: number | undefined

async function tickMc() {
  try {
    const s = await fetchMcStatus()
    mc.value = s
  } catch (e) {
    const msg = String(e)
    mc.value = null
    if (msg.includes('401')) logout()
  }
}

/* ================= 命令控制台 ================= */
const cmdInput = ref('')
const cmdBusy = ref(false)
const presets = ['list', 'spark tps', 'save-all', 'say 服务器面板在线']
const log = ref<{ cmd: string; out: string; ok: boolean; ts: string }[]>([])

async function runCmd(cmd?: string) {
  const c = (cmd ?? cmdInput.value).trim()
  if (!c || cmdBusy.value) return
  cmdBusy.value = true
  const ts = new Date().toLocaleTimeString()
  try {
    const r = await sendMcCommand(c)
    log.value.unshift({ cmd: c, out: r.output ?? r.error ?? '', ok: r.ok, ts })
  } catch (e) {
    log.value.unshift({ cmd: c, out: String(e), ok: false, ts })
  } finally {
    cmdBusy.value = false
    cmdInput.value = ''
    if (log.value.length > 50) log.value.pop()
  }
}

async function runStop() {
  if (!window.confirm('确认向服务器发送 stop 命令？会保存存档并关闭服务端。')) return
  await runCmd('stop')
}

/* ================= 服务器数据源切换（手机 / Windows 新服务器） ================= */
const monitorSource = ref<'phone' | 'win'>('win')

function switchSource(src: 'phone' | 'win') {
  if (monitorSource.value === src) return
  monitorSource.value = src
  resetCharts()
  tick()
  fetchSeries()
}

/* ================= 手机负载监控（沿用原页面逻辑） ================= */
const data = ref<PhoneStatus | null>(null)
const loading = ref(true)
const error = ref('')
const fresh = ref<number | null>(null) // 距上次成功采样秒数
let timer: number | undefined
let freshTimer: number | undefined

/* ================= 折线图（24h 历史 / 悬停时间段 / 滚轮缩放，四图共享窗口） ================= */
interface ChartPoint {
  ts: number
  [k: string]: number | null
}
type ChartSeries = Array<{ key: string; label: string; color: string; unit: string; digits?: number }>

interface RawPoint {
  ts: number
  cpu: number | null
  mem_avail: number | null
  swap_total: number
  swap_free: number
  disk_used: number
  disk_total: number
  rx: number | null
  tx: number | null
}

const rawByTs = new Map<number, RawPoint>()
const history = ref<RawPoint[]>([])
let hasOverview = false
let lastFetch = { from: 0, to: 0 }

// 时间窗口（四张图共享，同步缩放/平移）
const spanMs = ref(5 * 60_000)
const followLive = ref(true)
const fixedT0 = ref(0)
const fixedT1 = ref(0)
const nowTick = ref(Date.now())
const t0 = computed(() => (followLive.value ? nowTick.value - spanMs.value : fixedT0.value))
const t1 = computed(() => (followLive.value ? nowTick.value : fixedT1.value))

const cpuSeries: ChartSeries = [{ key: 'cpu', label: 'CPU', color: '#f59e0b', unit: '%', digits: 0 }]
const memSeries: ChartSeries = [{ key: 'memPct', label: '内存', color: '#34d399', unit: '%', digits: 0 }]
const swapSeries: ChartSeries = [{ key: 'swapPct', label: 'Swap', color: '#fbbf24', unit: '%', digits: 0 }]
const netSeries: ChartSeries = [
  { key: 'rx', label: '入站', color: '#38bdf8', unit: 'KB/s', digits: 0 },
  { key: 'tx', label: '出站', color: '#a78bfa', unit: 'KB/s', digits: 0 },
]

function normPoint(p: Record<string, unknown>): RawPoint {
  return {
    ts: (Number(p.ts) || 0) * 1000, // 后端 ts 为秒，统一为毫秒
    cpu: (p.cpu as number | null) ?? null,
    mem_avail: (p.mem_avail as number | null) ?? null,
    swap_total: Number(p.swap_total) || 0,
    swap_free: Number(p.swap_free) || 0,
    disk_used: Number(p.disk_used) || 0,
    disk_total: Number(p.disk_total) || 0,
    rx: (p.rx as number | null) ?? null,
    tx: (p.tx as number | null) ?? null,
  }
}

function rebuildChart() {
  const cutoff = Date.now() - 25 * 3600_000
  for (const ts of rawByTs.keys()) {
    if (ts < cutoff) rawByTs.delete(ts)
  }
  history.value = [...rawByTs.values()].sort((a, b) => a.ts - b.ts)
}

function appendLivePoint(d: PhoneStatus) {
  if (!d || !d.ts) return
  const m = d.mem || {}
  const du = (d.disk || []).reduce((s, x) => s + (x.used || 0), 0)
  const dt = (d.disk || []).reduce((s, x) => s + (x.total || 0), 0)
  rawByTs.set(d.ts * 1000, {
    ts: d.ts * 1000,
    cpu: d.cpu ?? null,
    mem_avail: m.MemAvailable ?? null,
    swap_total: m.SwapTotal || 0,
    swap_free: m.SwapFree || 0,
    disk_used: du,
    disk_total: dt,
    rx: d.rx_kb_s ?? null,
    tx: d.tx_kb_s ?? null,
  })
  rebuildChart()
}

async function fetchSeries() {
  const now = Date.now()
  let from: number
  let to: number
  if (followLive.value && spanMs.value >= 10 * 60_000) {
    if (hasOverview) return
    from = now - 86400_000
    to = now
  } else {
    const pad = Math.max(spanMs.value * 0.1, 10_000)
    if (followLive.value) {
      from = now - spanMs.value * 3
      to = now + 10_000
    } else {
      from = fixedT0.value - pad
      to = fixedT1.value + pad
    }
    if (
      lastFetch.from !== 0 &&
      Math.abs(from - lastFetch.from) < spanMs.value * 0.5 &&
      Math.abs(to - lastFetch.to) < 60_000
    )
      return
  }
  lastFetch = { from, to }
  if (followLive.value && spanMs.value >= 10 * 60_000) hasOverview = true
  try {
    const res = await fetch(
      `/api/${monitorSource.value}/series?start=${Math.round(from / 1000)}&end=${Math.round(to / 1000)}&limit=900`,
      { cache: 'no-store' },
    )
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const j = (await res.json()) as { series?: Record<string, unknown>[] }
    for (const p of j.series || []) {
      const rp = normPoint(p)
      if (rp.ts) rawByTs.set(rp.ts, rp)
    }
    rebuildChart()
  } catch {
    /* 拉取失败保持现有数据 */
  }
}

function onViewport(v: { t0: number; t1: number }) {
  const now = Date.now()
  if (v.t1 >= now - 2000) {
    followLive.value = true
    spanMs.value = Math.max(30_000, Math.min(26 * 3600_000, v.t1 - v.t0))
  } else {
    followLive.value = false
    fixedT0.value = v.t0
    fixedT1.value = v.t1
  }
}

function resetLive() {
  followLive.value = true
  spanMs.value = 5 * 60_000
  nowTick.value = Date.now()
}

function resetCharts() {
  rawByTs.clear()
  history.value = []
  hasOverview = false
  lastFetch = { from: 0, to: 0 }
  followLive.value = true
  spanMs.value = 5 * 60_000
  nowTick.value = Date.now()
}

const spanText = computed(() => fmtSpan(spanMs.value))
function fmtSpan(ms: number) {
  if (ms < 60_000) return Math.round(ms / 1000) + ' 秒'
  if (ms < 3600_000) return Math.round(ms / 60_000) + ' 分钟'
  if (ms < 86400_000) return (ms / 3600_000).toFixed(1).replace(/\.0$/, '') + ' 小时'
  return Math.round(ms / 86400_000) + ' 天'
}

// 折线图点集：把原始采样换算成各图表用的百分比/速率序列
const chartPoints = computed<ChartPoint[]>(() => {
  const tot = data.value?.mem?.MemTotal || 0
  return history.value.map((p) => {
    const memPct =
      tot > 0 && p.mem_avail != null
        ? Math.min(100, Math.max(0, 100 - (p.mem_avail / tot) * 100))
        : null
    const swapPct =
      p.swap_total > 0 ? Math.min(100, Math.max(0, ((p.swap_total - p.swap_free) / p.swap_total) * 100)) : null
    return { ts: p.ts, cpu: p.cpu, memPct, swapPct, rx: p.rx, tx: p.tx }
  })
})

// 窗口变化（缩放/平移/实时滑动）→ 防抖拉取对应时间段
let fetchDeb: number | undefined
watch([t0, t1], () => {
  if (fetchDeb) window.clearTimeout(fetchDeb)
  fetchDeb = window.setTimeout(() => fetchSeries(), 200)
})

const fmtKB = (kb?: number) => {
  if (kb == null || kb <= 0) return '–'
  return kb >= 1048576 ? (kb / 1048576).toFixed(2) + ' G' : kb >= 1024 ? (kb / 1024).toFixed(0) + ' M' : kb + ' K'
}
const fmtRate = (kv?: number | null) => {
  if (kv == null || kv <= 0) return '–'
  if (kv >= 1048576) return (kv / 1048576).toFixed(2) + ' GB/s'
  if (kv >= 1024) return (kv / 1024).toFixed(1) + ' MB/s'
  return kv.toFixed(0) + ' KB/s'
}
const rxRate = computed(() => fmtRate(data.value?.rx_kb_s))
const txRate = computed(() => fmtRate(data.value?.tx_kb_s))
const lvl = (p: number) => (p > 90 ? 'bad' : p > 70 ? 'warn' : 'ok')
const pctColor = (p: number) => (p > 90 ? '#f87171' : p > 70 ? '#fbbf24' : '#34d399')
const clock = (ts: number) => (ts ? new Date(ts * 1000).toLocaleTimeString() : '–')
const uptime = (s?: number | null) => {
  if (s == null) return '–'
  return Math.floor(s / 3600) + ' 小时 ' + Math.floor((s % 3600) / 60) + ' 分'
}

const nproc = computed(() => data.value?.nproc || 8)
const cpuAvg = computed<number | null>(() => (data.value?.cpu != null ? data.value.cpu : null))
const cpuSrc = computed(() => data.value?.cpu_src ?? null)
const cpuSumPct = computed<number | null>(() => (cpuAvg.value != null ? cpuAvg.value * nproc.value : null))
const cpuSumTxt = computed<string>(() => {
  if (cpuAvg.value == null) return '—'
  if (cpuSrc.value === 'ps') {
    const raw = data.value?.cpu_sum_raw
    return '可见进程合计 ' + (raw != null ? raw.toFixed(0) : cpuAvg.value.toFixed(0)) + '%（近似）'
  }
  return '总和 ' + (cpuSumPct.value ?? 0).toFixed(0) + '%'
})
const cores = computed<number[] | null>(() => (data.value?.cores && data.value.cores.length ? data.value.cores : null))

const memTotal = computed(() => data.value?.mem?.MemTotal || 0)
const memAvail = computed(() => data.value?.mem?.MemAvailable || 0)
const memOk = computed(() => memTotal.value > 0)
const memUsedPct = computed(() => (memOk.value ? Math.max(0, Math.min(100, 100 - (100 * memAvail.value) / memTotal.value)) : 0))
const memUsedKB = computed(() => (memOk.value ? memTotal.value - memAvail.value : 0))

const swapTotal = computed(() => data.value?.mem?.SwapTotal || 0)
const swapOk = computed(() => swapTotal.value > 0)
const swapUsed = computed(() => (swapOk.value ? Math.max(0, Math.min(100, (100 * (swapTotal.value - (data.value?.mem?.SwapFree || 0))) / swapTotal.value)) : 0))

const diskList = computed(() => data.value?.disk || [])
const diskOk = computed(() => diskList.value.length > 0)
const diskAgg = computed(() => {
  let du = 0, dt = 0
  diskList.value.forEach((x) => { du += x.used; dt += x.total })
  return { du, dt, p: dt ? Math.max(0, Math.min(100, (100 * du) / dt)) : 0 }
})
const diskPct = (d: PhoneDisk): number => (d.total ? Math.max(0, Math.min(100, (100 * d.used) / d.total)) : 0)

const loadLine = computed(() => {
  const l = data.value?.load || []
  return l.length ? l.join(' / ') : null
})
const loadRate = computed<string | null>(() => {
  const l = data.value?.load || []
  if (!l.length) return null
  const v = parseFloat((l[0] || '').trim())
  return isNaN(v) ? null : Math.round((v * 100) / nproc.value) + '%'
})

const procs = computed(() => [...(data.value?.procs || [])].sort((a, b) => (parseFloat(b.cpu) || 0) - (parseFloat(a.cpu) || 0)))
const procMaxCpu = computed(() => Math.max(1, ...procs.value.map((p) => parseFloat(p.cpu) || 0)))
const cpuNum = (p: PhoneProc) => parseFloat(p.cpu) || 0

async function tick() {
  try {
    const d = monitorSource.value === 'win' ? await fetchWinStatus() : await fetchPhoneStatus()
    data.value = d
    error.value = ''
    loading.value = false
    fresh.value = d.ts ? Math.max(0, Math.round(Date.now() / 1000 - d.ts)) : 0
    nowTick.value = Date.now()
    appendLivePoint(d)
  } catch (e) {
    error.value = String(e)
    loading.value = false
  }
}

let documentVisibilityHandler: (() => void) | undefined = undefined

function startPolling() {
  stopPolling()
  tick()
  tickMc()
  timer = window.setInterval(tick, 3000)
  freshTimer = window.setInterval(() => {
    const ts = data.value?.ts
    fresh.value = ts ? Math.max(0, Math.round(Date.now() / 1000 - ts)) : null
  }, 1000)
  if (!document.hidden) {
    mcTimer = window.setInterval(tickMc, 60000)
  }
  documentVisibilityHandler = () => {
    if (document.hidden) {
      if (mcTimer) { window.clearInterval(mcTimer); mcTimer = undefined }
    } else {
      if (!mcTimer) { tickMc(); mcTimer = window.setInterval(tickMc, 60000) }
    }
  }
  document.addEventListener('visibilitychange', documentVisibilityHandler)
}

function stopPolling() {
  if (timer) { window.clearInterval(timer); timer = undefined }
  if (freshTimer) { window.clearInterval(freshTimer); freshTimer = undefined }
  if (mcTimer) { window.clearInterval(mcTimer); mcTimer = undefined }
  if (documentVisibilityHandler) {
    document.removeEventListener('visibilitychange', documentVisibilityHandler)
    documentVisibilityHandler = undefined
  }
}

onMounted(() => {
  if (authed.value) {
    startPolling()
    fetchSeries()
  }
})
onBeforeUnmount(stopPolling)

const isStale = computed(() => fresh.value != null && fresh.value > 10)
</script>

<template>
  <div class="monitor">
    <div class="bg-orbs">
      <div class="orb orb-1"></div>
      <div class="orb orb-2"></div>
      <div class="orb orb-3"></div>
    </div>
    <div class="grid-overlay"></div>

    <div class="content">
      <!-- ── 认证门 ── -->
      <div v-if="!authed" class="auth-card">
        <div class="auth-inner">
          <div class="auth-title">MC 服务器面板</div>
          <div class="auth-sub">输入访问密码以进入（控制台可向服务器发送命令）</div>
          <input v-model="authPwd" type="password" class="auth-input" placeholder="访问密码" autocomplete="current-password" @keyup.enter="doAuth" />
          <div v-if="authErr" class="auth-err">{{ authErr }}</div>
          <button class="auth-btn" :disabled="authing" @click="doAuth">{{ authing ? '验证中…' : '进入面板' }}</button>
        </div>
      </div>

      <template v-else>
        <header class="head">
          <div class="head-idx">
            <span class="idx">HOST</span>
            <span class="idx dim">{{ monitorSource === 'win' ? 'WIN-SRV' : 'TEL-AN10' }}</span>
          </div>
          <div class="head-title">
            <h1>MC 服务器面板 <span class="tag">{{ monitorSource === 'win' ? 'Windows Server · 150.138.72.66' : '华为 TEL-AN10 · Fabric 服' }}</span></h1>
          </div>
          <div class="srv-switch">
            <button :class="{ on: monitorSource === 'phone' }" @click="switchSource('phone')">手机</button>
            <button :class="{ on: monitorSource === 'win' }" @click="switchSource('win')">Windows</button>
          </div>
          <div class="meta">
            <span class="dot" :class="{ off: data && (!data.ok || isStale) }"></span>
            <span v-if="!data && loading">采样中…</span>
            <span v-else-if="data && data.ok">
              <span class="meta-ok">监控采样正常</span> · {{ clock(data.ts) }} · 开机 {{ uptime(data.uptime_s) }}
              <span v-if="fresh != null" class="fresh" :class="{ stale: isStale }">· {{ fresh }} 秒前刷新</span>
            </span>
            <span v-else class="err">监控无数据（手机不可达或系统限制）{{ data && data.err ? '· ' + data.err.slice(0, 80) : '' }}</span>
            <span class="meta-exit">
              <button class="logout-btn" @click="logout">退出面板</button>
            </span>
          </div>
        </header>

        <!-- ── 性能监控：CPU/内存/Swap/网络折线图（悬停看时间段 + 滚轮缩放）+ 磁盘百分比 ── -->
        <div v-if="!data && loading" class="empty">连接中…</div>
        <template v-else>
          <div class="charts-panel">
            <div class="charts-toolbar">
              <span class="span-chip">显示最近 {{ spanText }}</span>
              <span class="hint">滚轮缩放 · 拖动平移 · 悬停查看时间点 · 采样间隔 3s</span>
              <span class="live-dot" :class="{ off: !followLive }"></span>
              <button class="live-btn" :disabled="followLive" @click="resetLive">回到实时</button>
            </div>

            <div class="charts-grid">
              <section class="chart-block" style="--a:var(--accent)">
                <div class="chart-head">
                  <h2>CPU 使用率<template v-if="nproc">（{{ nproc }} 核）</template></h2>
                  <div class="cur" :style="{ color: cpuAvg != null ? pctColor(cpuAvg) : '#9a917f' }">
                    {{ cpuAvg != null ? cpuAvg.toFixed(1) + '%' : '无数据' }}
                  </div>
                </div>
                <div class="chart-sub">
                  负载 1/5/15：{{ loadLine ?? '无数据' }}<span v-if="loadRate" class="l dim">负载率 {{ loadRate }}</span>
                  <span class="l dim">{{ cpuSumTxt }}</span>
                </div>
                <LineChart :points="chartPoints" :series="cpuSeries" :t0="t0" :t1="t1" :y-max="100" @viewport="onViewport" />
                <div v-if="cores" class="cores">
                  <div v-for="(c, i) in cores" :key="i" class="core" :title="'CPU' + i + '：' + c.toFixed(0) + '%'">
                    <span class="corebar" :style="{ height: c + '%', background: pctColor(c), color: pctColor(c) }"></span>
                    <em>{{ i }}</em>
                  </div>
                </div>
                <div v-else-if="data" class="chart-sub dim">各核明细：需要 root 读取 /proc/stat，本机系统限制不可读</div>
              </section>

              <section class="chart-block" style="--a:var(--accent-bright)">
                <div class="chart-head">
                  <h2>内存 <span class="l dim">已用</span></h2>
                  <template v-if="memOk">
                    <div class="cur" :style="{ color: pctColor(memUsedPct) }">{{ memUsedPct.toFixed(0) }}%</div>
                  </template>
                  <template v-else>
                    <div class="cur muted">无数据</div>
                  </template>
                </div>
                <div class="chart-sub">
                  <template v-if="memOk">
                    已用 <b class="val">{{ fmtKB(memUsedKB) }}</b> · 可用 {{ fmtKB(memAvail) }} · 总量 {{ fmtKB(memTotal) }}
                  </template>
                  <template v-else>锁屏或后台时内存字段不可用</template>
                </div>
                <LineChart :points="chartPoints" :series="memSeries" :t0="t0" :t1="t1" :y-max="100" @viewport="onViewport" />
              </section>

              <section class="chart-block" style="--a:#fbbf24">
                <div class="chart-head">
                  <h2>Swap 交换区 <span class="l dim">已用</span></h2>
                  <template v-if="swapOk">
                    <div class="cur" :style="{ color: pctColor(swapUsed) }">{{ swapUsed.toFixed(0) }}%</div>
                  </template>
                  <template v-else>
                    <div class="cur muted">未启用</div>
                  </template>
                </div>
                <div class="chart-sub">
                  <template v-if="swapOk">
                    已用 <b class="val">{{ fmtKB(swapTotal - (data?.mem.SwapFree || 0)) }}</b> · 总量 {{ fmtKB(swapTotal) }}
                  </template>
                  <template v-else>本机无 Swap 分区</template>
                </div>
                <LineChart
                  v-if="swapOk"
                  :points="chartPoints"
                  :series="swapSeries"
                  :t0="t0"
                  :t1="t1"
                  :y-max="100"
                  @viewport="onViewport"
                />
                <div v-else class="chart-empty">未启用，无折线图</div>
              </section>

              <section class="chart-block" style="--a:#38bdf8">
                <div class="chart-head">
                  <h2>网络流量 <span class="l dim">入/出</span></h2>
                  <div class="cur-net">
                    <span class="net-cur" style="color: #38bdf8">↓ {{ rxRate }}</span>
                    <span class="net-cur" style="color: #a78bfa">↑ {{ txRate }}</span>
                  </div>
                </div>
                <div class="chart-sub">入站 / 出站速率（KB/s，Windows 源每 3s 采样）</div>
                <LineChart :points="chartPoints" :series="netSeries" :t0="t0" :t1="t1" @viewport="onViewport" />
              </section>
            </div>

            <div class="panel-divider"></div>

            <!-- 磁盘占用：基本不随时间变，保留百分比显示 -->
            <div class="disk-block">
              <div class="chart-head">
                <h2>存储 <span class="l dim">磁盘占用 · 变化缓慢，用百分比</span></h2>
                <template v-if="diskOk">
                  <div class="cur" :style="{ color: pctColor(diskAgg.p) }">{{ diskAgg.p.toFixed(0) }}%</div>
                </template>
                <template v-else>
                  <div class="cur muted">无数据</div>
                </template>
              </div>
              <div v-if="diskOk" class="disk-sub">已用 <b class="val">{{ fmtKB(diskAgg.du) }}</b> / {{ fmtKB(diskAgg.dt) }}</div>
              <div v-else class="chart-sub">未读到分区信息</div>
              <div v-for="d in diskList" :key="d.mnt" class="diskline">
                <div class="diskhead">
                  <span class="mono">{{ d.mnt }}</span>
                  <span class="dim">{{ diskPct(d).toFixed(0) }}% · 空闲 {{ fmtKB(d.avail) }}</span>
                </div>
                <div class="bar thin"><i :class="lvl(diskPct(d))" :style="{ width: diskPct(d) + '%' }"></i></div>
              </div>
            </div>
          </div>
        </template>

        <p class="note">{{ monitorSource === 'win' ? '监控为被动只读 SSH 采样（PowerShell 计数器，CPU 为真实占用，各核明细可用）；内存 / 存储 / 进程表始终可用。控制台命令经 RCON 真实发送（stop / save-all 等），请谨慎操作。' : '监控为被动只读采样（无 root，CPU 为「ps 可见进程近似值」，各核明细/负载不可读）；内存 / 存储 / 进程表始终可用。控制台命令会真实发送（stop / save-all 等），请谨慎操作。' }}</p>

        <!-- ── 下排：控制台 + 进程表 ── -->
        <div class="lower">
          <section class="console">
            <div class="console-head">
              <h2>服务器控制台 <span class="l dim">RCON</span></h2>
              <div class="mc-status">
                <span class="dot" :class="{ off: !mcOnline }"></span>
                <span v-if="mcOnline" class="meta-ok">服务器在线</span>
                <span v-else class="err">服务器离线{{ mc && mc.error ? ' · ' + mc.error.slice(0, 60) : '' }}</span>
                <span v-if="mc && mc.online" class="l dim">{{ mc.players ?? '?' }} / {{ mc.players_max ?? '?' }} 玩家</span>
                <span v-if="mc && mc.online && mc.names && mc.names.length" class="l dim">{{ mcNames }}</span>
              </div>
            </div>

            <div class="presets">
              <button v-for="p in presets" :key="p" class="preset" :disabled="cmdBusy" @click="runCmd(p)">{{ p }}</button>
              <button class="preset danger" :disabled="cmdBusy" @click="runStop">stop（RCON 关服）</button>
            </div>

            <div class="cmd-row">
              <input
                v-model="cmdInput"
                class="cmd-input"
                placeholder="输入命令，如 spark tps / list / say 大家好"
                spellcheck="false"
                :disabled="cmdBusy"
                @keyup.enter="runCmd()"
              />
              <button class="cmd-send" :disabled="cmdBusy" @click="runCmd()">{{ cmdBusy ? '发送中…' : '发送' }}</button>
            </div>

            <div class="console-out">
              <div v-for="(e, i) in log" :key="i" class="logline">
                <div class="logcmd">
                  <span class="l dim">{{ e.ts }}</span>
                  <span class="mono log-cmd-txt">{{ e.cmd }}</span>
                </div>
                <pre class="logout" :class="{ bad: !e.ok }">{{ e.out || '（无输出）' }}</pre>
              </div>
              <div v-if="!log.length" class="dim center empty-log">尚未发送命令。可用 list / spark tps / save-all / stop</div>
            </div>
          </section>

          <section v-if="data" class="procs">
            <h2>进程 TOP 10 <span class="l dim">按 CPU 排序</span></h2>
            <div class="tablewrap">
              <table>
                <thead>
                  <tr><th>PID</th><th>进程</th><th class="r">CPU%</th><th class="r">内存%</th><th class="r">RSS</th></tr>
                </thead>
                <tbody>
                  <tr v-for="(p, i) in procs" :key="p.pid" :class="{ top: i === 0 }">
                    <td class="mono dim">{{ i === 0 ? '▼ ' : '' }}{{ p.pid }}</td>
                    <td class="comm">{{ p.comm }}</td>
                    <td class="mono r">
                      <span class="cpuwrap">
                        <i class="cpubar" :style="{ width: (cpuNum(p) / procMaxCpu) * 100 + '%', background: pctColor(cpuNum(p)) }"></i>
                      </span>
                      <span class="mono">{{ p.cpu }}</span>
                    </td>
                    <td class="mono r">{{ p.mem }}</td>
                    <td class="mono r">{{ fmtKB(p.rss) }}</td>
                  </tr>
                  <tr v-if="!procs.length"><td colspan="5" class="dim center">暂无可见进程</td></tr>
                </tbody>
              </table>
            </div>
          </section>
        </div>

        <footer class="foot">服务器命令经 RCON（TCP 25575）发送 · 监控数据经 SSH 每 3 秒被动采样 · 由 AIToolkit 后端提供</footer>
      </template>
    </div>
  </div>
</template>

<style scoped>
/* ── 科幻背景：光球 + 网格 ── */
.bg-orbs { position: absolute; inset: 0; pointer-events: none; }
.orb { position: absolute; border-radius: 50%; filter: blur(140px); opacity: .22; animation: orb-drift 22s ease-in-out infinite alternate; }
.orb-1 { width: 620px; height: 620px; background: radial-gradient(circle, color-mix(in srgb, var(--accent) 27%, transparent), transparent 70%); top: -18%; left: -12%; }
.orb-2 { width: 520px; height: 520px; background: radial-gradient(circle, #a67c002e, transparent 70%); bottom: -22%; right: -10%; animation-delay: -7s; }
.orb-3 { width: 380px; height: 380px; background: radial-gradient(circle, color-mix(in srgb, var(--accent) 10%, transparent), transparent 70%); top: 48%; left: 55%; animation-delay: -14s; }
@keyframes orb-drift { 0% { transform: translate(0,0) scale(1); } 100% { transform: translate(40px,-30px) scale(1.15); } }
.grid-overlay { position: absolute; inset: 0; background-image: linear-gradient(rgba(255,255,255,.02) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,.02) 1px, transparent 1px); background-size: 44px 44px; pointer-events: none; mask-image: radial-gradient(ellipse at 50% 0%, black 20%, transparent 70%); opacity: .42; }

.content { position: relative; z-index: 1; max-width: 1480px; margin: 0 auto; }

/* ── 认证门 ── */
.auth-card { min-height: 55vh; display: flex; align-items: center; justify-content: center; }
.auth-inner { width: 100%; max-width: 360px; padding: 34px 30px; background: rgba(17, 15, 9, .78); border: 1px solid var(--line); border-radius: 12px; box-shadow: 0 20px 60px rgba(0,0,0,.45); backdrop-filter: blur(14px); }
.auth-title { font-size: 21px; font-weight: 800; letter-spacing: .04em; color: #f5efdf; text-align: center; text-shadow: 0 0 18px color-mix(in srgb, var(--accent) 45%, transparent); }
.auth-sub { font-size: 12.5px; color: var(--muted); text-align: center; margin: 10px 0 22px; line-height: 1.6; }
.auth-input { width: 100%; box-sizing: border-box; padding: 12px 14px; font-size: 15px; color: var(--text); background: rgba(255,255,255,.05); border: 1px solid var(--line-strong); border-radius: 8px; outline: none; transition: border-color .25s, box-shadow .25s; }
.auth-input:focus { border-color: var(--accent); box-shadow: 0 0 0 3px color-mix(in srgb, var(--accent) 18%, transparent); }
.auth-btn { width: 100%; margin-top: 16px; padding: 12px; font-size: 14px; font-weight: 700; color: #14110a; background: linear-gradient(90deg, var(--accent), var(--accent-bright)); border: none; border-radius: 8px; cursor: pointer; letter-spacing: .12em; }
.auth-btn:disabled { opacity: .55; cursor: default; }
.auth-err { margin-top: 12px; font-size: 12.5px; color: #f87171; text-align: center; }

/* ── 头 ── */
.head { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; padding-bottom: 12px; margin-bottom: 12px; border-bottom: 1px solid var(--line); }
.head-idx { display: flex; gap: 6px; }
.idx { font-size: 10.5px; letter-spacing: .18em; color: var(--accent); font-family: Consolas, monospace; border: 1px solid color-mix(in srgb, var(--accent) 35%, transparent); padding: 3px 8px; border-radius: 5px; background: color-mix(in srgb, var(--accent) 6%, transparent); }
.idx.dim { color: var(--muted); border-color: var(--line); background: rgba(255,255,255,.03); }
.head-title { flex: 1; min-width: 0; }
h1 { font-size: 23px; margin: 0; font-weight: 800; letter-spacing: .02em; color: #f5efdf; }
.tag, .l { font-size: 11.5px; color: var(--muted); background: color-mix(in srgb, var(--accent) 6%, transparent); padding: 2px 10px; border-radius: 99px; margin-left: 8px; border: 1px solid var(--line); }
.l.dim { margin-left: 6px; }
.meta { margin-left: auto; color: var(--muted); font-size: 13px; display: flex; align-items: center; gap: 8px; flex-wrap: wrap; padding: 7px 12px; background: rgba(17, 15, 9, .6); border: 1px solid var(--line); border-radius: 8px; }
.meta .err { color: #f87171; }
.meta-ok { color: #34d399; font-weight: 600; }
.fresh { color: #64748b; transition: color .3s; }
.fresh.stale { color: #f87171; font-weight: 600; }
.dot { width: 10px; height: 10px; border-radius: 50%; background: #34d399; box-shadow: 0 0 10px #34d399, 0 0 22px #34d399; transition: background .3s, box-shadow .3s; }
.dot.off { background: #f87171; box-shadow: 0 0 10px #f87171, 0 0 22px #f87171; }
.meta-exit { margin-left: auto; }
.logout-btn { font-size: 12px; color: var(--muted); background: rgba(255,255,255,.05); border: 1px solid var(--line-strong); border-radius: 6px; padding: 4px 12px; cursor: pointer; }
.logout-btn:hover { color: #f87171; border-color: #f8717166; }

/* ── 数据源切换（手机 / Windows） ── */
.srv-switch { display: flex; gap: 0; margin-left: 12px; border: 1px solid var(--line-strong); border-radius: 7px; overflow: hidden; align-self: center; }
.srv-switch button { font-size: 12px; font-family: Consolas, monospace; color: var(--muted); background: rgba(255,255,255,.03); border: none; padding: 6px 14px; cursor: pointer; transition: background .2s, color .2s; letter-spacing: .04em; }
.srv-switch button + button { border-left: 1px solid var(--line); }
.srv-switch button.on { color: #14110a; background: linear-gradient(90deg, var(--accent), var(--accent-bright)); font-weight: 700; }
.srv-switch button:not(.on):hover { color: var(--accent); background: color-mix(in srgb, var(--accent) 12%, transparent); }

/* ── 折线图工具条（整体面板内） ── */
.charts-toolbar { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-bottom: 12px; padding: 0 0 10px; border-bottom: 1px solid color-mix(in srgb, var(--accent) 10%, transparent); }
.span-chip { font-size: 12px; font-family: Consolas, monospace; color: #14110a; background: linear-gradient(90deg, var(--accent), var(--accent-bright)); padding: 3px 11px; border-radius: 99px; font-weight: 700; letter-spacing: .03em; }
.hint { font-size: 11.5px; color: var(--muted); }
.live-dot { width: 8px; height: 8px; border-radius: 50%; background: #34d399; box-shadow: 0 0 8px #34d399; transition: background .3s, box-shadow .3s; }
.live-dot.off { background: #f87171; box-shadow: 0 0 8px #f87171; }
.live-btn { margin-left: auto; font-size: 12px; color: var(--accent); background: color-mix(in srgb, var(--accent) 8%, transparent); border: 1px solid color-mix(in srgb, var(--accent) 30%, transparent); border-radius: 6px; padding: 4px 12px; cursor: pointer; font-family: Consolas, monospace; }
.live-btn:disabled { opacity: .4; cursor: default; }
.live-btn:not(:disabled):hover { background: color-mix(in srgb, var(--accent) 18%, transparent); box-shadow: 0 0 10px color-mix(in srgb, var(--accent) 20%, transparent); }

/* ── 整体面板（所有图放一起，不分子卡片） ── */
.charts-panel { background: rgba(17,15,9,.74); border: 1px solid var(--line); border-radius: 8px; padding: 13px 15px 12px; backdrop-filter: blur(14px); margin-bottom: 10px; }
.charts-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 6px 24px; }
.chart-block { position: relative; display: flex; flex-direction: column; gap: 5px; padding-top: 11px; }
.chart-block::before { content: ''; position: absolute; top: 0; left: 0; right: 0; height: 1px; background: linear-gradient(90deg, var(--a), transparent 70%); box-shadow: 0 0 8px var(--a); opacity: .55; }
.panel-divider { height: 1px; margin: 14px 0 10px; background: linear-gradient(90deg, transparent, color-mix(in srgb, var(--accent) 22%, transparent), transparent); }
.chart-head { display: flex; align-items: baseline; justify-content: space-between; gap: 10px; }
.chart-head h2 { font-size: 12.5px; color: #cfc6ad; margin: 0; font-weight: 600; letter-spacing: .05em; display: flex; align-items: center; flex-wrap: wrap; }
.cur { font-size: 25px; font-weight: 800; font-variant-numeric: tabular-nums; font-family: Consolas, monospace; text-shadow: 0 0 16px currentColor; line-height: 1; }
.cur.muted { color: var(--muted); font-weight: 600; text-shadow: none; font-size: 17px; }
.cur-net { display: flex; gap: 12px; align-items: baseline; }
.net-cur { font-size: 14.5px; font-weight: 700; font-family: Consolas, monospace; font-variant-numeric: tabular-nums; text-shadow: 0 0 12px currentColor; }
.chart-sub { font-size: 11.5px; color: var(--muted); min-height: 14px; line-height: 1.6; }
.chart-sub .val { color: #eef3fb; font-weight: 700; }
.chart-empty { height: 195px; display: flex; align-items: center; justify-content: center; color: var(--dim); font-size: 12px; }

/* ── 磁盘百分比块 ── */
.disk-block { padding-top: 2px; }
.disk-sub { font-size: 11.5px; color: var(--muted); margin: 4px 0 2px; }
.disk-sub .val { color: #eef3fb; font-weight: 700; }
.diskline { margin-top: 7px; padding-top: 5px; border-top: 1px dashed rgba(255,255,255,.07); }
.diskhead { display: flex; justify-content: space-between; gap: 8px; font-size: 11px; margin-bottom: 3px; }

/* ── 磁盘分区百分比条（保留） ── */
.bar { height: 7px; background: color-mix(in srgb, var(--accent) 10%, transparent); border-radius: 99px; overflow: hidden; margin: 3px 0; }
.bar.thin { height: 4px; margin: 4px 0 6px; }
.bar > i { display: block; height: 100%; width: 0; border-radius: 99px; transition: width .6s; }
.bar > i.ok { background: linear-gradient(90deg, #34d399, var(--accent)); box-shadow: 0 0 8px color-mix(in srgb, var(--accent) 53%, transparent); }
.bar > i.warn { background: linear-gradient(90deg, #fbbf24, #f97316); box-shadow: 0 0 8px #f9731688; }
.bar > i.bad { background: linear-gradient(90deg, #f87171, #ef4444); box-shadow: 0 0 8px #ef444488; }

/* ── 各核明细（直方分布，非横条百分比） ── */
.cores { display: flex; gap: 4px; align-items: stretch; margin-top: 6px; height: 30px; position: relative; padding-bottom: 12px; }
.cores::after { content: ''; position: absolute; left: 0; right: 0; bottom: 0; height: 1px; background: linear-gradient(90deg, transparent, color-mix(in srgb, var(--accent) 35%, transparent), transparent); }
.core { flex: 1; display: flex; flex-direction: column; justify-content: flex-end; align-items: center; gap: 3px; min-width: 0; }
.corebar { display: block; width: 72%; max-width: 18px; border-radius: 3px 3px 0 0; min-height: 2px; transition: height .5s; box-shadow: 0 0 8px currentColor; }
.core em { font-size: 9px; color: var(--dim); font-style: normal; font-family: Consolas, monospace; line-height: 1; margin-top: 2px; }

/* ── 说明 ── */
.note { font-size: 11px; color: var(--muted); background: rgba(243, 189, 104, .06); border: 1px solid rgba(243, 189, 104, .24); border-radius: 5px; padding: 6px 11px; margin-bottom: 10px; line-height: 1.6; border-left: 3px solid rgba(243,189,104,.5); }
.empty { color: var(--dim); padding: 36px; text-align: center; }

/* ── 下排：控制台 + 进程 ── */
.lower { display: grid; grid-template-columns: minmax(0, 5fr) minmax(0, 7fr); gap: 12px; align-items: stretch; }

.console { display: flex; flex-direction: column; background: rgba(17, 15, 9, .74); border: 1px solid var(--line); border-radius: 8px; padding: 13px 15px; backdrop-filter: blur(14px); }
.console-head { display: flex; align-items: center; justify-content: space-between; gap: 10px; flex-wrap: wrap; margin-bottom: 10px; }
.console-head h2 { font-size: 13px; color: #cfc6ad; margin: 0; font-weight: 600; letter-spacing: .05em; }
.mc-status { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; font-size: 12px; color: var(--muted); }
.presets { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 8px; }
.preset { font-size: 11.5px; font-family: Consolas, monospace; color: var(--accent); background: color-mix(in srgb, var(--accent) 8%, transparent); border: 1px solid color-mix(in srgb, var(--accent) 30%, transparent); border-radius: 6px; padding: 4px 10px; cursor: pointer; transition: background .2s, box-shadow .2s; }
.preset:hover { background: color-mix(in srgb, var(--accent) 18%, transparent); box-shadow: 0 0 10px color-mix(in srgb, var(--accent) 20%, transparent); }
.preset.danger { color: #f87171; border-color: #f8717155; background: rgba(248,113,113,.06); }
.preset.danger:hover { background: rgba(248,113,113,.16); }
.preset:disabled { opacity: .5; cursor: default; }
.cmd-row { display: flex; gap: 7px; }
.cmd-input { flex: 1; min-width: 0; padding: 9px 12px; font-size: 13px; font-family: Consolas, monospace; color: var(--text); background: rgba(255,255,255,.05); border: 1px solid var(--line-strong); border-radius: 6px; outline: none; }
.cmd-input:focus { border-color: var(--accent); box-shadow: 0 0 0 3px color-mix(in srgb, var(--accent) 16%, transparent); }
.cmd-send { padding: 0 18px; font-size: 12.5px; font-weight: 700; color: #14110a; background: linear-gradient(90deg, var(--accent), var(--accent-bright)); border: none; border-radius: 6px; cursor: pointer; letter-spacing: .08em; }
.cmd-send:disabled { opacity: .55; cursor: default; }
.console-out { flex: 1; margin-top: 9px; min-height: 120px; max-height: 240px; overflow-y: auto; background: rgba(0,0,0,.35); border: 1px solid var(--line); border-radius: 6px; padding: 8px 11px; }
.empty-log { padding: 14px 0; font-size: 11.5px; }
.logline { margin-bottom: 10px; }
.logline:last-child { margin-bottom: 0; }
.logcmd { display: flex; align-items: center; gap: 8px; margin-bottom: 3px; }
.log-cmd-txt { color: var(--accent); font-size: 11.5px; }
.logout { margin: 0; padding: 6px 9px; font-size: 11.5px; font-family: Consolas, monospace; line-height: 1.6; white-space: pre-wrap; word-break: break-all; color: #e7e2d3; background: rgba(255,255,255,.04); border-left: 2px solid color-mix(in srgb, var(--accent) 45%, transparent); border-radius: 0 5px 5px 0; }
.logout.bad { border-left-color: #f87171; color: #fca5a5; }

/* ── 进程表 ── */
.procs { background: rgba(17, 15, 9, .74); border: 1px solid var(--line); border-radius: 8px; padding: 13px 15px; backdrop-filter: blur(14px); }
.procs h2 { font-size: 13px; color: #cfc6ad; margin: 0 0 9px; font-weight: 600; letter-spacing: .05em; }
.tablewrap { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; font-size: 12px; }
th, td { text-align: left; padding: 6px 9px; border-bottom: 1px solid color-mix(in srgb, var(--accent) 10%, transparent); }
th { color: var(--dim); font-size: 11px; font-weight: 600; letter-spacing: .05em; }
tbody tr:hover { background: rgba(255,255,255,.03); }
tbody tr.top { background: color-mix(in srgb, var(--accent) 7%, transparent); box-shadow: inset 2px 0 0 var(--accent); }
tbody tr.top td:first-child { color: var(--accent); text-shadow: 0 0 8px color-mix(in srgb, var(--accent) 50%, transparent); }
.cpuwrap { position: relative; display: inline-block; width: 50px; height: 6px; background: rgba(255,255,255,.07); border-radius: 99px; overflow: hidden; vertical-align: 1px; margin-right: 6px; }
.cpubar { position: absolute; left: 0; top: 0; bottom: 0; border-radius: 99px; transition: width .5s; opacity: .9; }
.comm { max-width: 220px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.mono { font-family: Consolas, monospace; font-size: 11.5px; }
.dim { color: var(--muted); }
.r { text-align: right; }
.center { text-align: center; }
.foot { margin-top: 12px; text-align: center; font-size: 11px; color: var(--dim); letter-spacing: .04em; }

/* 窄屏适配 */
@media (max-width: 1180px) {
  .lower { grid-template-columns: 1fr; }
}
@media (max-width: 720px) {
  .charts-grid { grid-template-columns: 1fr; }
}
@media (max-width: 560px) {
  .monitor { padding: 16px 14px; }
  h1 { font-size: 18px; }
  .comm { max-width: 150px; }
  .head-title { width: 100%; }
  .meta { margin-left: 0; width: 100%; }
}
</style>
