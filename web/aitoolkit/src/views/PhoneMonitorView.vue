<script setup lang="ts">
import { onMounted, onBeforeUnmount, ref, computed } from 'vue'
import { fetchPhoneStatus, type PhoneStatus, type PhoneProc, type PhoneDisk } from '@/api/phoneMonitor'

const data = ref<PhoneStatus | null>(null)
const loading = ref(true)
const error = ref('')
const fresh = ref<number | null>(null) // 距上次成功采样秒数
let timer: number | undefined
let freshTimer: number | undefined

/* ---------- 格式化 ---------- */
const fmtKB = (kb?: number) => {
  if (kb == null || kb <= 0) return '–'
  return kb >= 1048576 ? (kb / 1048576).toFixed(2) + ' G' : kb >= 1024 ? (kb / 1024).toFixed(0) + ' M' : kb + ' K'
}
const lvl = (p: number) => (p > 90 ? 'bad' : p > 70 ? 'warn' : 'ok')
const pctColor = (p: number) => (p > 90 ? '#f87171' : p > 70 ? '#fbbf24' : '#34d399')
const fmtRate = () => '' // 网络数据不可用（Android 限制），占位
const clock = (ts: number) => (ts ? new Date(ts * 1000).toLocaleTimeString() : '–')
const uptime = (s?: number | null) => {
  if (s == null) return '–'
  return Math.floor(s / 3600) + ' 小时 ' + Math.floor((s % 3600) / 60) + ' 分'
}

/* ---------- 派生指标（口径全部统一为「已用%」） ---------- */
const nproc = computed(() => data.value?.nproc || 8)

// CPU：后端 /proc/stat 两次差分得到的总使用率（0-100%，即各核加权平均）；
// Android 无 root 读不到全局 /proc 时，后端回退为 ps 可见进程占用和（近似）。
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

/* 读取当前主题色（canvas 绘制需要具体颜色，从 CSS 变量取） */
const themeColor = (name: string) =>
  getComputedStyle(document.documentElement).getPropertyValue(name).trim() || '#d4af37'

/* ---------- 曲线（CPU 每核平均% / 内存已用%） ---------- */
function spark(cv: HTMLCanvasElement | null, arr: number[], color: string, max: number, latest?: number | null) {
  if (!cv) return
  const c = cv.getContext('2d')
  if (!c) return
  const dpr = window.devicePixelRatio || 1
  const w = Math.max(320, cv.clientWidth)
  const h = 76
  cv.width = w * dpr
  cv.height = h * dpr
  c.scale(dpr, dpr)
  c.clearRect(0, 0, w, h)
  const padTop = 16
  const padBottom = 8
  const plotH = h - padTop - padBottom
  const m = max || 1
  const X = (i: number) => (arr.length > 1 ? (i / (arr.length - 1)) * w : w / 2)
  const Y = (v: number) => padTop + plotH - (Math.min(Math.max(v, 0), m) / m) * plotH

  // 淡网格 3 档
  c.strokeStyle = 'rgba(255,255,255,.07)'
  c.lineWidth = 1
  for (let g = 1; g <= 3; g++) {
    const gy = padTop + (plotH * g) / 4
    c.beginPath()
    c.moveTo(0, gy)
    c.lineTo(w, gy)
    c.stroke()
  }

  // 最新值标注
  if (latest != null && arr.length) {
    const ly = Y(latest)
    c.beginPath()
    c.arc(w - 4, ly, 3, 0, Math.PI * 2)
    c.fillStyle = color
    c.fill()
    c.fillStyle = color
    c.font = '600 10px Consolas, monospace'
    c.textAlign = 'right'
    c.fillText(latest.toFixed(0) + '%', w - 10, ly - 6)
  }

  if (arr.length < 1) return
  const stroke = (close: boolean) => {
    c.beginPath()
    arr.forEach((v, i) => (i ? c.lineTo(X(i), Y(v)) : c.moveTo(X(i), Y(v))))
    if (close) {
      c.lineTo(w, h - 2)
      c.lineTo(0, h - 2)
      c.closePath()
    }
  }
  // 面积
  const g = c.createLinearGradient(0, 0, 0, h)
  g.addColorStop(0, color + '55')
  g.addColorStop(1, color + '00')
  c.fillStyle = g
  stroke(true)
  c.fill()
  // 线
  stroke(false)
  c.strokeStyle = color
  c.lineWidth = 2
  c.lineJoin = 'round'
  c.shadowColor = color
  c.shadowBlur = 8
  c.stroke()
  c.shadowBlur = 0
}

/* ---------- 轮询 ---------- */
function draw() {
  const d = data.value
  const tot = d?.mem?.MemTotal || 0
  const cpuArr = d?.hist.cpu || [] // 已是 0-100 总使用率
  const memArr = tot > 0 ? (d?.hist.mem || []).map((av) => Math.min(100, Math.max(0, 100 - (av / tot) * 100))) : []
  requestAnimationFrame(() => {
    spark(document.getElementById('cpuChart') as HTMLCanvasElement, cpuArr, themeColor('--accent'), 100, cpuAvg.value)
    spark(document.getElementById('memChart') as HTMLCanvasElement, memArr, themeColor('--accent-bright'), 100, memUsedPct.value)
  })
}

async function tick() {
  try {
    const d = await fetchPhoneStatus()
    data.value = d
    error.value = ''
    loading.value = false
    fresh.value = d.ts ? Math.max(0, Math.round(Date.now() / 1000 - d.ts)) : 0
    draw()
  } catch (e) {
    error.value = String(e)
    loading.value = false
  }
}

onMounted(() => {
  tick()
  timer = window.setInterval(tick, 3000)
  freshTimer = window.setInterval(() => {
    const ts = data.value?.ts
    fresh.value = ts ? Math.max(0, Math.round(Date.now() / 1000 - ts)) : null
  }, 1000)
})
onBeforeUnmount(() => {
  if (timer) window.clearInterval(timer)
  if (freshTimer) window.clearInterval(freshTimer)
})

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
    <header class="head">
      <div class="head-idx">
        <span class="idx">HOST</span>
        <span class="idx dim">TEL-AN10</span>
      </div>
      <div class="head-title">
        <h1>手机负载监控 <span class="tag">华为 TEL-AN10 · Termux 原版服</span></h1>
      </div>
      <div class="meta">
        <span class="dot" :class="{ off: data && (!data.ok || isStale) }"></span>
        <span v-if="!data && loading">采样中…</span>
        <span v-else-if="data && data.ok">
          <span class="meta-ok">采样正常</span> · {{ clock(data.ts) }} · 开机 {{ uptime(data.uptime_s) }}
          <span v-if="fresh != null" class="fresh" :class="{ stale: isStale }">· {{ fresh }} 秒前刷新</span>
        </span>
        <span v-else class="err">无数据（手机不可达或系统限制）{{ data && data.err ? '· ' + data.err.slice(0, 80) : '' }}</span>
      </div>
    </header>

    <div class="note">仅被动读取：本机（华为 TEL-AN10，无 root）Android 沙箱禁止普通应用读全局 /proc，故 CPU 为「ps 可见进程近似值」、各核明细/负载不可读；内存 / 存储 / 进程表始终可用。本页面不会对手机执行任何进程操作。</div>

    <div v-if="!data && loading" class="empty">连接中…</div>

    <div v-else class="kpis">
      <section class="kpi" style="--a:var(--accent)">
        <h2>CPU 使用率<template v-if="nproc">（{{ nproc }} 核）</template><span class="l dim">{{ cpuSumTxt }}</span></h2>
        <div class="big" :style="{ color: cpuAvg != null ? pctColor(cpuAvg) : '#9a917f' }">
          {{ cpuAvg != null ? cpuAvg.toFixed(1) + '%' : '无数据' }}
        </div>
        <div class="bar"><i :class="cpuAvg != null ? lvl(cpuAvg) : ''" :style="{ width: (cpuAvg ?? 0) + '%' }"></i></div>
        <div class="subline">
          负载 1/5/15：{{ loadLine ?? '无数据' }}<span v-if="loadRate" class="l dim">负载率 {{ loadRate }}</span>
        </div>
        <div v-if="cores" class="cores">
          <div v-for="(c, i) in cores" :key="i" class="core" :title="'CPU' + i + '：' + c.toFixed(0) + '%'">
            <span class="corebar" :style="{ height: c + '%', background: pctColor(c), color: pctColor(c) }"></span>
            <em>{{ i }}</em>
          </div>
        </div>
        <div v-else-if="data" class="subline dim">各核明细：需要 root 读取 /proc/stat，本机系统限制不可读</div>
        <canvas id="cpuChart"></canvas>
      </section>

      <section class="kpi" style="--a:var(--accent-bright)">
        <h2>内存 <span class="l dim">已用</span></h2>
        <template v-if="memOk">
          <div class="big" :style="{ color: pctColor(memUsedPct) }">{{ memUsedPct.toFixed(0) }}%</div>
          <div class="bar"><i :class="lvl(memUsedPct)" :style="{ width: memUsedPct + '%' }"></i></div>
          <div class="subline">
            已用 <b class="val">{{ fmtKB(memUsedKB) }}</b> · 可用 {{ fmtKB(memAvail) }} · 总量 {{ fmtKB(memTotal) }}
          </div>
        </template>
        <template v-else>
          <div class="big muted">无数据</div>
          <div class="subline">锁屏或后台时内存字段不可用</div>
        </template>
        <canvas id="memChart"></canvas>
      </section>

      <section class="kpi" style="--a:#fbbf24">
        <h2>Swap 交换区 <span class="l dim">已用</span></h2>
        <template v-if="swapOk">
          <div class="big" :style="{ color: pctColor(swapUsed) }">{{ swapUsed.toFixed(0) }}%</div>
          <div class="bar"><i :class="lvl(swapUsed)" :style="{ width: swapUsed + '%' }"></i></div>
          <div class="subline">
            已用 <b class="val">{{ fmtKB(swapTotal - (data?.mem.SwapFree || 0)) }}</b> · 总量 {{ fmtKB(swapTotal) }}
          </div>
        </template>
        <template v-else>
          <div class="big muted">未启用</div>
          <div class="subline">本机无 Swap 分区</div>
        </template>
      </section>

      <section class="kpi" style="--a:var(--accent-deep)">
        <h2>存储 <span class="l dim">分区</span></h2>
        <template v-if="diskOk">
          <div class="big" :style="{ color: pctColor(diskAgg.p) }">{{ diskAgg.p.toFixed(0) }}%</div>
          <div class="bar"><i :class="lvl(diskAgg.p)" :style="{ width: diskAgg.p + '%' }"></i></div>
          <div class="subline">已用 {{ fmtKB(diskAgg.du) }} / {{ fmtKB(diskAgg.dt) }}</div>
          <div v-for="d in diskList" :key="d.mnt" class="diskline">
            <div class="diskhead">
              <span class="mono">{{ d.mnt }}</span>
              <span class="dim">{{ diskPct(d).toFixed(0) }}% · 空闲 {{ fmtKB(d.avail) }}</span>
            </div>
            <div class="bar thin"><i :class="lvl(diskPct(d))" :style="{ width: diskPct(d) + '%' }"></i></div>
          </div>
        </template>
        <template v-else>
          <div class="big muted">无数据</div>
          <div class="subline">未读到分区信息</div>
        </template>
      </section>
    </div>

    <section v-if="data" class="procs">
      <h2>进程 TOP 10 <span class="l dim">亮屏时可见 mc 服务端</span></h2>
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

    <footer class="foot">数据经 SSH 每 3 秒被动采样（仅读），由 AIToolkit 后端提供 · {{ fmtRate() }}网络上下行因 Android 权限不可用</footer>
    </div>
  </div>
</template>

<style scoped>
/* ── 科幻背景：光球 + 网格 ── */
.bg-orbs { position: absolute; inset: 0; pointer-events: none; }
.orb { position: absolute; border-radius: 50%; filter: blur(120px); opacity: .22; animation: orb-drift 22s ease-in-out infinite alternate; }
.orb-1 { width: 620px; height: 620px; background: radial-gradient(circle, color-mix(in srgb, var(--accent) 27%, transparent), transparent 70%); top: -18%; left: -12%; }
.orb-2 { width: 520px; height: 520px; background: radial-gradient(circle, #a67c002e, transparent 70%); bottom: -22%; right: -10%; animation-delay: -7s; }
.orb-3 { width: 380px; height: 380px; background: radial-gradient(circle, color-mix(in srgb, var(--accent) 10%, transparent), transparent 70%); top: 48%; left: 55%; animation-delay: -14s; }
@keyframes orb-drift { 0% { transform: translate(0,0) scale(1); } 100% { transform: translate(40px,-30px) scale(1.15); } }
.grid-overlay { position: absolute; inset: 0; background-image: linear-gradient(rgba(255,255,255,.02) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,.02) 1px, transparent 1px); background-size: 64px 64px; pointer-events: none; mask-image: radial-gradient(ellipse at 50% 0%, black 20%, transparent 70%); }

.content { position: relative; z-index: 1; }

.head { display: flex; align-items: center; gap: 14px; flex-wrap: wrap; margin-bottom: 6px; }
.head-idx { display: flex; gap: 6px; margin-right: 2px; }
.idx { font-size: 10.5px; letter-spacing: .18em; color: var(--accent); font-family: Consolas, monospace; border: 1px solid color-mix(in srgb, var(--accent) 35%, transparent); padding: 3px 8px; border-radius: 5px; background: color-mix(in srgb, var(--accent) 6%, transparent); box-shadow: 0 0 14px color-mix(in srgb, var(--accent) 12%, transparent) inset; }
.idx.dim { color: #5f5747; border-color: rgba(255,255,255,.1); background: rgba(255,255,255,.03); box-shadow: none; }
.head-title { flex: 1; min-width: 0; }
h1 { font-size: 22px; margin: 0; font-weight: 800; letter-spacing: .02em; color: #f5efdf; text-shadow: 0 0 18px color-mix(in srgb, var(--accent) 45%, transparent), 0 0 40px color-mix(in srgb, var(--accent) 15%, transparent); }
.tag, .l { font-size: 11.5px; color: #9a917f; background: rgba(255,255,255,.06); padding: 2px 10px; border-radius: 99px; margin-left: 8px; border: 1px solid rgba(255,255,255,.08); }
.l.dim { margin-left: 6px; }
.meta { margin: 8px 0 14px; color: #9a917f; font-size: 13px; display: flex; align-items: center; gap: 8px; flex-wrap: wrap; padding: 8px 14px; background: rgba(255,255,255,.03); border: 1px solid rgba(255,255,255,.07); border-radius: 12px; box-shadow: 0 0 30px rgba(0,0,0,.35) inset; }
.meta .err { color: #f87171; text-shadow: 0 0 10px rgba(248,113,113,.5); }
.meta-ok { color: #34d399; font-weight: 600; text-shadow: 0 0 10px rgba(52,211,153,.45); }
.fresh { color: #64748b; transition: color .3s; }
.fresh.stale { color: #f87171; font-weight: 600; }
.dot { width: 10px; height: 10px; border-radius: 50%; background: #34d399; box-shadow: 0 0 10px #34d399, 0 0 22px #34d399; transition: background .3s, box-shadow .3s; }
.dot.off { background: #f87171; box-shadow: 0 0 10px #f87171, 0 0 22px #f87171; }
.note { font-size: 12px; color: #9a917f; background: rgba(251,191,36,.07); border: 1px solid rgba(251,191,36,.22); border-radius: 10px; padding: 9px 12px; margin-bottom: 16px; line-height: 1.6; border-left: 3px solid rgba(251,191,36,.5); }
.empty { color: #5f5747; padding: 40px; text-align: center; }
.kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 16px; margin-bottom: 16px; }
.kpi { position: relative; background: rgba(255,255,255,.04); border: 1px solid rgba(255,255,255,.08); border-radius: 14px; padding: 18px 16px 16px; backdrop-filter: blur(6px); transition: border-color .35s, box-shadow .35s, transform .35s; }
.kpi::before { content: ''; position: absolute; top: 0; left: 0; right: 0; height: 2px; background: linear-gradient(90deg, var(--a), transparent 70%); box-shadow: 0 0 16px var(--a); }
.kpi:hover { border-color: color-mix(in srgb, var(--a) 45%, transparent); box-shadow: 0 0 40px color-mix(in srgb, var(--a) 12%, transparent), 0 8px 40px rgba(0,0,0,.4); transform: translateY(-2px); }
.kpi h2 { font-size: 13px; color: #9a917f; margin: 0 0 10px; font-weight: 600; display: flex; align-items: center; flex-wrap: wrap; letter-spacing: .06em; }
.big { font-size: 30px; font-weight: 800; font-variant-numeric: tabular-nums; font-family: Consolas, monospace; text-shadow: 0 0 22px currentColor; }
.big.muted { color: #9a917f; font-weight: 600; text-shadow: none; }
.subline { font-size: 12px; color: #9a917f; margin-top: 8px; min-height: 16px; line-height: 1.7; }
.subline .val { color: #eef3fb; font-weight: 700; }
.bar { height: 8px; background: rgba(255,255,255,.07); border-radius: 99px; overflow: hidden; margin: 10px 0 4px; box-shadow: 0 0 8px rgba(0,0,0,.4) inset; }
.bar.thin { height: 5px; margin: 5px 0 7px; }
.bar > i { display: block; height: 100%; width: 0; border-radius: 99px; transition: width .6s; }
.bar > i.ok { background: linear-gradient(90deg, #34d399, var(--accent)); box-shadow: 0 0 10px color-mix(in srgb, var(--accent) 53%, transparent); }
.bar > i.warn { background: linear-gradient(90deg, #fbbf24, #f97316); box-shadow: 0 0 10px #f9731688; }
.bar > i.bad { background: linear-gradient(90deg, #f87171, #ef4444); box-shadow: 0 0 10px #ef444488; }
.diskline { margin-top: 8px; padding-top: 6px; border-top: 1px dashed rgba(255,255,255,.07); }
.diskhead { display: flex; justify-content: space-between; gap: 8px; font-size: 11.5px; margin-bottom: 3px; }
.cores { display: flex; gap: 4px; align-items: stretch; margin-top: 12px; height: 48px; position: relative; padding-bottom: 14px; }
.cores::after { content: ''; position: absolute; left: 0; right: 0; bottom: 0; height: 1px; background: linear-gradient(90deg, transparent, color-mix(in srgb, var(--accent) 35%, transparent), transparent); }
.core { flex: 1; display: flex; flex-direction: column; justify-content: flex-end; align-items: center; gap: 3px; min-width: 0; }
.corebar { display: block; width: 72%; max-width: 20px; border-radius: 3px 3px 0 0; min-height: 2px; transition: height .5s; box-shadow: 0 0 8px currentColor; }
.core em { font-size: 9px; color: #5f5747; font-style: normal; font-family: Consolas, monospace; line-height: 1; margin-top: 2px; }
canvas { width: 100%; height: 76px; display: block; margin-top: 10px; }
.procs { background: rgba(255,255,255,.04); border: 1px solid rgba(255,255,255,.08); border-radius: 14px; padding: 16px; backdrop-filter: blur(6px); }
.procs h2 { font-size: 13px; color: #9a917f; margin: 0 0 10px; font-weight: 600; letter-spacing: .06em; }
.tablewrap { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; font-size: 12.5px; }
th, td { text-align: left; padding: 7px 10px; border-bottom: 1px solid rgba(255,255,255,.05); }
th { color: #5f5747; font-size: 11.5px; font-weight: 600; letter-spacing: .05em; }
thead th { border-bottom: 1px solid color-mix(in srgb, var(--accent) 25%, transparent); }
tbody tr:hover { background: rgba(255,255,255,.03); }
tbody tr.top { background: color-mix(in srgb, var(--accent) 7%, transparent); box-shadow: inset 2px 0 0 var(--accent); }
tbody tr.top td:first-child { color: var(--accent); text-shadow: 0 0 8px color-mix(in srgb, var(--accent) 50%, transparent); }
.cpuwrap { position: relative; display: inline-block; width: 56px; height: 6px; background: rgba(255,255,255,.07); border-radius: 99px; overflow: hidden; vertical-align: 1px; margin-right: 6px; }
.cpubar { position: absolute; left: 0; top: 0; bottom: 0; border-radius: 99px; transition: width .5s; opacity: .9; }
.comm { max-width: 260px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.mono { font-family: Consolas, monospace; font-size: 12px; }
.dim { color: #9a917f; }
.r { text-align: right; }
.center { text-align: center; }
.foot { margin-top: 16px; text-align: center; font-size: 11.5px; color: #5f5747; letter-spacing: .04em; }

/* 窄屏适配 */
@media (max-width: 560px) {
  .monitor { padding: 14px; }
  .kpis { grid-template-columns: 1fr; }
  h1 { font-size: 18px; }
  .kpi { padding: 14px; }
  .big { font-size: 26px; }
  .comm { max-width: 150px; }
}

/* 监控台重做：降低装饰噪音，强化数据层级 */
.monitor { position: relative; min-height: 0; padding: 28px 0 24px; background: transparent; color: var(--text); font-family: inherit; overflow: visible; }
.bg-orbs { opacity: .28; }.orb { filter: blur(140px); }.grid-overlay { opacity: .42; background-size: 44px 44px; }
.content { max-width: 1280px; margin: 0 auto; }
.head { gap: 12px; padding-bottom: 15px; margin-bottom: 12px; border-bottom: 1px solid var(--line); }.head-idx { order: 2; }.head-title { order: 1; }.meta { order: 3; width: 100%; margin: 0; padding: 10px 12px; border-color: var(--line); border-radius: 5px; background: rgba(17, 15, 9, .6); box-shadow: none; }.head-title h1 { text-shadow: none; font-size: 25px; }.tag, .l { color: var(--muted); background: color-mix(in srgb, var(--accent) 6%, transparent); border-color: var(--line); }.idx { color: var(--accent); border-color: var(--line-strong); background: color-mix(in srgb, var(--accent) 8%, transparent); box-shadow: none; }.idx.dim { color: var(--muted); }
.note { margin-bottom: 14px; border-color: rgba(243, 189, 104, .24); border-left-color: var(--amber); background: rgba(243, 189, 104, .06); color: var(--muted); border-radius: 5px; }.kpis { gap: 12px; margin-bottom: 12px; grid-template-columns: repeat(4, minmax(220px, 1fr)); }.kpi { min-height: 220px; padding: 17px; border-color: var(--line); border-radius: 6px; background: rgba(17, 15, 9, .74); box-shadow: none; backdrop-filter: blur(14px); }.kpi::before { height: 1px; box-shadow: 0 0 10px var(--a); }.kpi:hover { transform: translateY(-2px); border-color: var(--line-strong); box-shadow: 0 15px 34px rgba(0,0,0,.25); }.kpi h2 { color: #cfc6ad; font-size: 12px; }.big { font-size: 34px; text-shadow: 0 0 18px currentColor; }.subline, .dim { color: var(--muted); }.bar { background: color-mix(in srgb, var(--accent) 10%, transparent); }.procs { padding: 17px; border-color: var(--line); border-radius: 6px; background: rgba(17, 15, 9, .74); box-shadow: none; }.procs h2 { color: #cfc6ad; }.thead th, thead th { border-bottom-color: var(--line-strong); } th, td { border-bottom-color: color-mix(in srgb, var(--accent) 10%, transparent); }.foot { color: var(--dim); }
@media (max-width: 1120px) { .kpis { grid-template-columns: repeat(2, minmax(220px, 1fr)); } }
@media (max-width: 650px) { .monitor { padding-top: 18px; }.content { width: 100%; }.head-title h1 { font-size: 20px; }.head-idx { order: 1; }.head-title { order: 2; width: 100%; }.meta { order: 3; }.kpis { grid-template-columns: 1fr; }.kpi { min-height: 0; }.note { font-size: 11px; }.tablewrap { margin: 0 -4px; } }
</style>
