<script setup lang="ts">
import { onMounted, onBeforeUnmount, ref, computed } from 'vue'
import { fetchPhoneStatus, type PhoneStatus, type PhoneProc, type PhoneDisk } from '@/api/phoneMonitor'
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

/* ================= 手机负载监控（沿用原页面逻辑） ================= */
const data = ref<PhoneStatus | null>(null)
const loading = ref(true)
const error = ref('')
const fresh = ref<number | null>(null) // 距上次成功采样秒数
let timer: number | undefined
let freshTimer: number | undefined

const fmtKB = (kb?: number) => {
  if (kb == null || kb <= 0) return '–'
  return kb >= 1048576 ? (kb / 1048576).toFixed(2) + ' G' : kb >= 1024 ? (kb / 1024).toFixed(0) + ' M' : kb + ' K'
}
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

const themeColor = (name: string) =>
  getComputedStyle(document.documentElement).getPropertyValue(name).trim() || '#d4af37'

function spark(cv: HTMLCanvasElement | null, arr: number[], color: string, max: number, latest?: number | null) {
  if (!cv) return
  const c = cv.getContext('2d')
  if (!c) return
  const dpr = window.devicePixelRatio || 1
  const w = Math.max(320, cv.clientWidth)
  const h = 44
  cv.width = w * dpr
  cv.height = h * dpr
  c.scale(dpr, dpr)
  c.clearRect(0, 0, w, h)
  const padTop = 12
  const padBottom = 6
  const plotH = h - padTop - padBottom
  const m = max || 1
  const X = (i: number) => (arr.length > 1 ? (i / (arr.length - 1)) * w : w / 2)
  const Y = (v: number) => padTop + plotH - (Math.min(Math.max(v, 0), m) / m) * plotH

  c.strokeStyle = 'rgba(255,255,255,.07)'
  c.lineWidth = 1
  for (let g = 1; g <= 3; g++) {
    const gy = padTop + (plotH * g) / 4
    c.beginPath()
    c.moveTo(0, gy)
    c.lineTo(w, gy)
    c.stroke()
  }

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
  const g = c.createLinearGradient(0, 0, 0, h)
  g.addColorStop(0, color + '55')
  g.addColorStop(1, color + '00')
  c.fillStyle = g
  stroke(true)
  c.fill()
  stroke(false)
  c.strokeStyle = color
  c.lineWidth = 2
  c.lineJoin = 'round'
  c.shadowColor = color
  c.shadowBlur = 8
  c.stroke()
  c.shadowBlur = 0
}

function draw() {
  const d = data.value
  const tot = d?.mem?.MemTotal || 0
  const cpuArr = d?.hist.cpu || []
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
  if (authed.value) startPolling()
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
            <span class="idx dim">TEL-AN10</span>
          </div>
          <div class="head-title">
            <h1>MC 服务器面板 <span class="tag">华为 TEL-AN10 · Fabric 26.2 服</span></h1>
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

        <!-- ── 性能监控 KPI（首屏直出） ── -->
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

        <p class="note">监控为被动只读采样（无 root，CPU 为「ps 可见进程近似值」，各核明细/负载不可读）；内存 / 存储 / 进程表始终可用。控制台命令会真实发送（stop / save-all 等），请谨慎操作。</p>

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

/* ── KPI ── */
.kpis { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; margin-bottom: 10px; }
.kpi { position: relative; display: flex; flex-direction: column; min-height: 210px; background: rgba(17, 15, 9, .74); border: 1px solid var(--line); border-radius: 8px; padding: 14px 15px 12px; backdrop-filter: blur(14px); }
.kpi::before { content: ''; position: absolute; top: 0; left: 0; right: 0; height: 1px; background: linear-gradient(90deg, var(--a), transparent 70%); box-shadow: 0 0 10px var(--a); }
.kpi h2 { font-size: 12px; color: #cfc6ad; margin: 0 0 6px; font-weight: 600; display: flex; align-items: center; flex-wrap: wrap; letter-spacing: .05em; }
.big { font-size: 30px; font-weight: 800; font-variant-numeric: tabular-nums; font-family: Consolas, monospace; text-shadow: 0 0 18px currentColor; }
.big.muted { color: var(--muted); font-weight: 600; text-shadow: none; }
.subline { font-size: 11.5px; color: var(--muted); margin-top: 6px; min-height: 15px; line-height: 1.6; }
.subline .val { color: #eef3fb; font-weight: 700; }
.bar { height: 7px; background: color-mix(in srgb, var(--accent) 10%, transparent); border-radius: 99px; overflow: hidden; margin: 7px 0 3px; }
.bar.thin { height: 4px; margin: 4px 0 6px; }
.bar > i { display: block; height: 100%; width: 0; border-radius: 99px; transition: width .6s; }
.bar > i.ok { background: linear-gradient(90deg, #34d399, var(--accent)); box-shadow: 0 0 8px color-mix(in srgb, var(--accent) 53%, transparent); }
.bar > i.warn { background: linear-gradient(90deg, #fbbf24, #f97316); box-shadow: 0 0 8px #f9731688; }
.bar > i.bad { background: linear-gradient(90deg, #f87171, #ef4444); box-shadow: 0 0 8px #ef444488; }
.diskline { margin-top: 6px; padding-top: 5px; border-top: 1px dashed rgba(255,255,255,.07); }
.diskhead { display: flex; justify-content: space-between; gap: 8px; font-size: 11px; margin-bottom: 2px; }
.cores { display: flex; gap: 4px; align-items: stretch; margin-top: 8px; height: 30px; position: relative; padding-bottom: 12px; }
.cores::after { content: ''; position: absolute; left: 0; right: 0; bottom: 0; height: 1px; background: linear-gradient(90deg, transparent, color-mix(in srgb, var(--accent) 35%, transparent), transparent); }
.core { flex: 1; display: flex; flex-direction: column; justify-content: flex-end; align-items: center; gap: 3px; min-width: 0; }
.corebar { display: block; width: 72%; max-width: 18px; border-radius: 3px 3px 0 0; min-height: 2px; transition: height .5s; box-shadow: 0 0 8px currentColor; }
.core em { font-size: 9px; color: var(--dim); font-style: normal; font-family: Consolas, monospace; line-height: 1; margin-top: 2px; }
canvas { width: 100%; height: 44px; display: block; margin-top: auto; padding-top: 4px; }

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
  .kpis { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .lower { grid-template-columns: 1fr; }
}
@media (max-width: 560px) {
  .monitor { padding: 16px 14px; }
  .kpis { grid-template-columns: 1fr; }
  .kpi { min-height: 0; }
  h1 { font-size: 18px; }
  .big { font-size: 26px; }
  .comm { max-width: 150px; }
  .head-title { width: 100%; }
  .meta { margin-left: 0; width: 100%; }
}
</style>
