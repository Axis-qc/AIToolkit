<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { fmt } from './data'
import {
  save,
  rate,
  clickPow,
  facilityRows,
  upgradeRows,
  reachedMilestones,
  nextMilestone,
  loadGame,
  startNewGame,
  resetGame,
  clickMine,
  buyFacility,
  buyUpgrade,
  tick,
  persistNow,
} from './store'

const router = useRouter()

// ─── tick 循环 ───────────────────────────────────────────────
let lastNow = 0
let timer: number | undefined

function startTick() {
  lastNow = Date.now()
  timer = window.setInterval(() => {
    const now = Date.now()
    tick((now - lastNow) / 1000)
    lastNow = now
  }, 200)
}

// ─── 展示状态 ────────────────────────────────────────────────
const toastMsg = ref('')
let toastTimer: number | undefined
function showToast(msg: string) {
  if (!msg) return
  toastMsg.value = msg
  if (toastTimer) window.clearTimeout(toastTimer)
  toastTimer = window.setTimeout(() => {
    toastMsg.value = ''
  }, 3000)
}

const energyText = computed(() => fmt(save.value?.energy ?? 0))
const rateText = computed(() => fmt(rate.value))
const clickText = computed(() => fmt(clickPow.value))
const totalText = computed(() => fmt(total()))

function total(): number {
  return save.value?.totalProduced ?? 0
}

const milestoneProgress = computed(() => {
  const n = nextMilestone.value
  if (!n) return 100
  const t = total()
  return Math.min(100, (t / n.at) * 100)
})

const milestoneText = computed(() => {
  const n = nextMilestone.value
  if (!n) return '全部里程碑已达成'
  return `${n.title} · ${fmt(total())} / ${fmt(n.at)}`
})

// ─── 点击特效 ────────────────────────────────────────────────
interface FloatNum {
  id: number
  text: string
  x: number
  y: number
}
const floats = ref<FloatNum[]>([])
let floatSeq = 0

function onMineClick(e: MouseEvent) {
  clickMine()
  const rect = (e.currentTarget as HTMLElement).getBoundingClientRect()
  const x = e.clientX - rect.left + (Math.random() * 30 - 15)
  const y = e.clientY - rect.top
  const id = ++floatSeq
  floats.value.push({ id, text: '+' + clickText.value, x, y })
  window.setTimeout(() => {
    floats.value = floats.value.filter((f) => f.id !== id)
  }, 900)
}

// ─── 生命周期 ────────────────────────────────────────────────
function handleBeforeUnload() {
  persistNow()
}
function handleVisibility() {
  if (document.visibilityState === 'hidden') persistNow()
}

onMounted(() => {
  if (loadGame() && (save.value?.offlineGain ?? 0) > 1) {
    showToast(`离线归来，补给能源 +${fmt(save.value!.offlineGain)}`)
  } else if (!save.value) {
    startNewGame()
  }
  startTick()
  window.addEventListener('beforeunload', handleBeforeUnload)
  document.addEventListener('visibilitychange', handleVisibility)
})

onUnmounted(() => {
  if (timer) window.clearInterval(timer)
  if (toastTimer) window.clearTimeout(toastTimer)
  window.removeEventListener('beforeunload', handleBeforeUnload)
  document.removeEventListener('visibilitychange', handleVisibility)
  persistNow()
})

// ─── 操作 ────────────────────────────────────────────────────
function buyFac(id: string) {
  showToast(buyFacility(id))
}
function buyUp(id: string) {
  showToast(buyUpgrade(id))
}
function confirmReset() {
  if (window.confirm('确定重置殖民地？所有进度将被清除。')) {
    resetGame()
  }
}

// 升级分组
const clickUpgrades = computed(() => upgradeRows.value.filter((u) => u.kind === 'click'))
const globalUpgrades = computed(() => upgradeRows.value.filter((u) => u.kind === 'global'))
const facilityUpgrades = computed(() => upgradeRows.value.filter((u) => u.kind === 'facility'))
</script>

<template>
  <div class="ci-page">
    <!-- 顶栏 -->
    <header class="ci-top">
      <div class="ci-title">
        <div class="ci-kicker">INCREMENTAL // COLONY</div>
        <h2>太空殖民</h2>
      </div>
      <div class="ci-top-actions">
        <button class="ci-btn ghost" @click="confirmReset">重置</button>
        <button class="ci-btn" @click="router.push('/games')">返回</button>
      </div>
    </header>

    <!-- 状态区 -->
    <section class="ci-status">
      <div class="ci-energy">
        <span class="ci-energy-label">能源</span>
        <div class="ci-energy-num" v-text="energyText"></div>
        <div class="ci-energy-rate">+{{ rateText }} / 秒</div>
      </div>
      <div class="ci-status-side">
        <div class="ci-mini"><span>点击产出</span><b v-text="'+' + clickText"></b></div>
        <div class="ci-mini"><span>累计产量</span><b v-text="totalText"></b></div>
        <div class="ci-milestone">
          <div class="ci-milestone-label"><span v-text="milestoneText"></span><small>{{ reachedMilestones.length }}/{{ 7 }} 达成</small></div>
          <div class="ci-progress"><i :style="{ width: milestoneProgress + '%' }"></i></div>
        </div>
      </div>
    </section>

    <div class="ci-main">
      <!-- 左栏：采集 + 升级 -->
      <section class="ci-left">
        <div class="ci-clicker-wrap">
          <button class="ci-clicker" @click="onMineClick">
            <span class="ci-clicker-core"></span>
            <span class="ci-clicker-ring r1"></span>
            <span class="ci-clicker-ring r2"></span>
            <span class="ci-clicker-label">采集能源</span>
          </button>
          <div class="ci-float-layer">
            <span
              v-for="f in floats"
              :key="f.id"
              class="ci-float"
              :style="{ left: f.x + 'px', top: f.y + 'px' }"
              v-text="f.text"
            ></span>
          </div>
        </div>

        <div class="ci-panel">
          <div class="ci-panel-head"><span>强化</span><small>永久加成</small></div>
          <div class="ci-up-group">
            <div class="ci-up-title">操控</div>
            <button
              v-for="u in clickUpgrades"
              :key="u.id"
              class="ci-up"
              :disabled="!u.affordable"
              @click="buyUp(u.id)"
            >
              <div class="ci-up-copy"><b v-text="u.name"></b><small v-text="u.desc"></small></div>
              <div class="ci-up-cost" v-text="fmt(u.cost)"></div>
            </button>
            <div class="ci-up-title">输配网</div>
            <button
              v-for="u in globalUpgrades"
              :key="u.id"
              class="ci-up"
              :disabled="!u.affordable"
              @click="buyUp(u.id)"
            >
              <div class="ci-up-copy"><b v-text="u.name"></b><small v-text="u.desc"></small></div>
              <div class="ci-up-cost" v-text="fmt(u.cost)"></div>
            </button>
            <div class="ci-up-title">设施增效</div>
            <button
              v-for="u in facilityUpgrades"
              :key="u.id"
              class="ci-up"
              :disabled="!u.affordable"
              @click="buyUp(u.id)"
            >
              <div class="ci-up-copy"><b v-text="u.name"></b><small v-text="u.desc"></small></div>
              <div class="ci-up-cost" v-text="fmt(u.cost)"></div>
            </button>
            <div v-if="!clickUpgrades.length && !globalUpgrades.length && !facilityUpgrades.length" class="ci-up-empty">
              累计产量提升后解锁强化
            </div>
          </div>
        </div>
      </section>

      <!-- 右栏：设施 -->
      <section class="ci-right">
        <div class="ci-panel">
          <div class="ci-panel-head"><span>殖民设施</span><small>自动产出</small></div>
          <div class="ci-fac-list">
            <div v-for="f in facilityRows" :key="f.id" class="ci-fac">
              <div class="ci-fac-icon" :class="'fc-' + f.id"></div>
              <div class="ci-fac-copy">
                <div class="ci-fac-name"><b v-text="f.name"></b><span class="ci-fac-own">× {{ f.owned }}</span></div>
                <small v-text="f.desc"></small>
                <div class="ci-fac-output">产出 {{ fmt(f.output) }}/秒</div>
              </div>
              <button class="ci-btn fac" :disabled="!f.affordable" @click="buyFac(f.id)">
                <span v-text="fmt(f.cost)"></span>
                <em>建造</em>
              </button>
            </div>
            <div v-if="!facilityRows.length" class="ci-up-empty">继续采集以解锁首座设施</div>
          </div>
        </div>

        <!-- 里程碑 -->
        <div class="ci-panel">
          <div class="ci-panel-head"><span>里程碑</span><small>总产量推进</small></div>
          <div class="ci-ms-list">
            <div v-for="m in reachedMilestones" :key="m.at" class="ci-ms done">
              <div class="ci-ms-mark"></div>
              <div class="ci-ms-copy"><b v-text="m.title"></b><small v-text="m.desc"></small></div>
            </div>
            <div v-if="nextMilestone" class="ci-ms pending">
              <div class="ci-ms-mark"></div>
              <div class="ci-ms-copy"><b v-text="nextMilestone.title"></b><small v-text="nextMilestone.desc"></small></div>
            </div>
          </div>
        </div>
      </section>
    </div>

    <!-- toast -->
    <transition name="toast">
      <div v-if="toastMsg" class="ci-toast" v-text="toastMsg"></div>
    </transition>
  </div>
</template>

<style scoped>
.ci-page {
  max-width: 1180px;
  margin: 0 auto;
  padding: 26px 4px 60px;
  color: var(--text);
  /* 局部提亮：暗背景上保证文字清晰 */
  --dim: #b8ad93;
  --muted: #cfc6ad;
}
.ci-top {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 18px;
  padding-bottom: 18px;
  border-bottom: 1px solid var(--line);
}
.ci-kicker {
  color: var(--accent);
  font: 10px Consolas, monospace;
  letter-spacing: 0.2em;
}
.ci-title h2 {
  margin: 8px 0 0;
  font-size: 30px;
  letter-spacing: -0.02em;
}
.ci-top-actions {
  display: flex;
  gap: 10px;
}
.ci-btn {
  border: 1px solid var(--line-strong);
  border-radius: 7px;
  padding: 7px 13px;
  color: var(--text);
  background: color-mix(in srgb, var(--accent) 10%, transparent);
  cursor: pointer;
  font-size: 12px;
  transition: 160ms ease;
}
.ci-btn:hover:not(:disabled) {
  background: color-mix(in srgb, var(--accent) 22%, transparent);
  box-shadow: 0 0 14px color-mix(in srgb, var(--accent) 22%, transparent);
}
.ci-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}
.ci-btn.ghost {
  background: transparent;
  color: var(--muted);
}
.ci-btn.ghost:hover:not(:disabled) {
  color: var(--text);
}

/* 状态区 */
.ci-status {
  display: flex;
  align-items: center;
  gap: 28px;
  padding: 22px 24px;
  margin-top: 18px;
  border: 1px solid var(--line);
  border-radius: 14px;
  background: linear-gradient(160deg, rgba(20, 17, 9, 0.6), rgba(10, 9, 6, 0.4));
}
.ci-energy {
  flex: 1;
  display: grid;
}
.ci-energy-label {
  color: var(--dim);
  font-size: 11px;
  letter-spacing: 0.18em;
}
.ci-energy-num {
  font-size: 40px;
  font-weight: 750;
  letter-spacing: -0.02em;
  background: linear-gradient(120deg, var(--accent-bright), var(--accent) 60%, var(--accent-deep));
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
  font-family: Consolas, monospace;
}
.ci-energy-rate {
  color: var(--green);
  font: 13px Consolas, monospace;
  margin-top: 2px;
}
.ci-status-side {
  min-width: 280px;
  display: grid;
  gap: 10px;
}
.ci-mini {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  font-size: 12px;
}
.ci-mini span {
  color: var(--muted);
}
.ci-mini b {
  font-family: Consolas, monospace;
  color: var(--text);
}
.ci-milestone-label {
  display: flex;
  justify-content: space-between;
  font-size: 11px;
  color: var(--muted);
  margin-bottom: 5px;
}
.ci-milestone-label small {
  color: var(--dim);
  font-family: Consolas, monospace;
}
.ci-progress {
  height: 7px;
  border-radius: 5px;
  background: color-mix(in srgb, var(--accent) 10%, transparent);
  overflow: hidden;
}
.ci-progress i {
  display: block;
  height: 100%;
  border-radius: 5px;
  background: linear-gradient(90deg, var(--accent-deep), var(--accent-bright));
  transition: width 400ms ease;
}

/* 主区 */
.ci-main {
  display: grid;
  grid-template-columns: minmax(280px, 4fr) minmax(380px, 7fr);
  gap: 18px;
  margin-top: 18px;
  align-items: start;
}
.ci-left,
.ci-right {
  display: grid;
  gap: 18px;
}
.ci-panel {
  border: 1px solid var(--line);
  border-radius: 12px;
  background: rgba(10, 9, 6, 0.55);
  backdrop-filter: blur(8px);
  overflow: hidden;
}
.ci-panel-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 16px;
  border-bottom: 1px solid var(--line);
}
.ci-panel-head span {
  font-size: 13px;
  font-weight: 650;
  letter-spacing: 0.04em;
}
.ci-panel-head small {
  color: var(--dim);
  font-size: 10px;
  font-family: Consolas, monospace;
}

/* 采集按钮 */
.ci-clicker-wrap {
  position: relative;
  display: flex;
  justify-content: center;
  padding: 34px 0 30px;
}
.ci-clicker {
  position: relative;
  width: 170px;
  height: 170px;
  border-radius: 50%;
  border: 1px solid var(--line-strong);
  background: radial-gradient(circle at 35% 30%, color-mix(in srgb, var(--accent) 26%, #101010), #0a0a0d 70%);
  cursor: pointer;
  transition: transform 120ms ease, box-shadow 160ms ease;
}
.ci-clicker:hover {
  box-shadow: 0 0 34px color-mix(in srgb, var(--accent) 30%, transparent);
}
.ci-clicker:active {
  transform: scale(0.94);
}
.ci-clicker-core {
  position: absolute;
  inset: 30px;
  border-radius: 50%;
  background: radial-gradient(circle at 40% 35%, var(--accent-bright), var(--accent) 45%, var(--accent-deep));
  box-shadow: 0 0 26px color-mix(in srgb, var(--accent) 55%, transparent);
}
.ci-clicker-ring {
  position: absolute;
  inset: -12px;
  border-radius: 50%;
  border: 1px dashed color-mix(in srgb, var(--accent) 35%, transparent);
  animation: ci-rotate 14s linear infinite;
}
.ci-clicker-ring.r2 {
  inset: -24px;
  animation-duration: 22s;
  animation-direction: reverse;
  border-color: color-mix(in srgb, var(--accent) 18%, transparent);
}
@keyframes ci-rotate {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}
.ci-clicker-label {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #1a1405;
  font-size: 15px;
  font-weight: 750;
  letter-spacing: 0.1em;
}
.ci-float-layer {
  position: absolute;
  inset: 0;
  pointer-events: none;
  overflow: hidden;
}
.ci-float {
  position: absolute;
  color: var(--accent-bright);
  font: 15px Consolas, monospace;
  text-shadow: 0 0 10px color-mix(in srgb, var(--accent) 60%, transparent);
  animation: ci-float 900ms ease-out forwards;
}
@keyframes ci-float {
  from { opacity: 1; transform: translateY(0); }
  to { opacity: 0; transform: translateY(-46px); }
}

/* 升级 */
.ci-up-group {
  display: grid;
  gap: 2px;
  padding: 6px 12px 14px;
}
.ci-up-title {
  color: var(--dim);
  font: 9px Consolas, monospace;
  letter-spacing: 0.18em;
  padding: 10px 4px 4px;
}
.ci-up {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 9px 11px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: color-mix(in srgb, var(--accent) 4%, transparent);
  cursor: pointer;
  text-align: left;
  color: var(--text);
  transition: 160ms ease;
}
.ci-up:hover:not(:disabled) {
  border-color: var(--line-strong);
  background: color-mix(in srgb, var(--accent) 10%, transparent);
}
.ci-up:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}
.ci-up-copy {
  flex: 1;
  display: grid;
  gap: 2px;
  min-width: 0;
}
.ci-up-copy b {
  font-size: 12px;
}
.ci-up-copy small {
  color: var(--dim);
  font-size: 10px;
}
.ci-up-cost {
  font: 12px Consolas, monospace;
  color: var(--accent-bright);
  white-space: nowrap;
}
.ci-up-empty {
  color: var(--dim);
  font-size: 11px;
  text-align: center;
  padding: 22px 0;
}

/* 设施 */
.ci-fac-list {
  display: grid;
}
.ci-fac {
  display: flex;
  align-items: center;
  gap: 13px;
  padding: 13px 16px;
  border-bottom: 1px solid color-mix(in srgb, var(--accent) 6%, transparent);
}
.ci-fac:last-child {
  border-bottom: 0;
}
.ci-fac-icon {
  width: 40px;
  height: 40px;
  flex: 0 0 40px;
  border-radius: 10px;
  border: 1px solid var(--line-strong);
  background: radial-gradient(circle at 40% 35%, color-mix(in srgb, var(--accent) 24%, #101010), #0a0a0d);
}
.ci-fac-icon.fc-solar { background: radial-gradient(circle, #f3bd68 0%, #d47f1f 45%, transparent 62%); }
.ci-fac-icon.fc-geo { background: radial-gradient(circle, #f17878 0%, #a03a2a 45%, transparent 62%); }
.ci-fac-icon.fc-orbit { background: radial-gradient(circle at 50% 50%, #fff 0 8%, #7ee2a8 20%, #1f9a5a 45%, transparent 62%); }
.ci-fac-icon.fc-fusion { background: radial-gradient(circle, #7ee2a8 0%, #2a7a5a 45%, transparent 62%); }
.ci-fac-icon.fc-antimatter { background: radial-gradient(circle, #a78bfa 0%, #5a3aa0 45%, transparent 62%); }
.ci-fac-icon.fc-dyson { background: radial-gradient(circle, #60a5fa 0%, #1f3aa0 45%, transparent 62%); }
.ci-fac-copy {
  flex: 1;
  display: grid;
  gap: 2px;
  min-width: 0;
}
.ci-fac-name {
  display: flex;
  align-items: baseline;
  gap: 8px;
}
.ci-fac-name b {
  font-size: 13px;
}
.ci-fac-own {
  color: var(--accent-bright);
  font: 11px Consolas, monospace;
}
.ci-fac-copy small {
  color: var(--dim);
  font-size: 10px;
}
.ci-fac-output {
  color: var(--green);
  font: 10px Consolas, monospace;
  margin-top: 2px;
}
.ci-btn.fac {
  display: grid;
  justify-items: center;
  gap: 1px;
  padding: 8px 14px;
  white-space: nowrap;
}
.ci-btn.fac span {
  font: 12px Consolas, monospace;
  color: var(--accent-bright);
}
.ci-btn.fac em {
  font-style: normal;
  font-size: 10px;
  color: var(--muted);
}

/* 里程碑 */
.ci-ms-list {
  display: grid;
  padding: 8px 16px 14px;
  max-height: 250px;
  overflow-y: auto;
}
.ci-ms {
  display: flex;
  gap: 11px;
  align-items: flex-start;
  padding: 8px 0;
}
.ci-ms-mark {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  margin-top: 5px;
  flex: 0 0 8px;
}
.ci-ms.done .ci-ms-mark {
  background: var(--green);
  box-shadow: 0 0 10px var(--green);
}
.ci-ms.pending .ci-ms-mark {
  background: var(--dim);
}
.ci-ms-copy {
  display: grid;
  gap: 2px;
}
.ci-ms-copy b {
  font-size: 12px;
}
.ci-ms.done .ci-ms-copy b {
  color: var(--green);
}
.ci-ms.pending .ci-ms-copy b {
  color: var(--muted);
}
.ci-ms-copy small {
  color: var(--dim);
  font-size: 10px;
}

/* toast */
.ci-toast {
  position: fixed;
  left: 50%;
  bottom: 40px;
  transform: translateX(-50%);
  z-index: 60;
  padding: 10px 20px;
  border-radius: 8px;
  border: 1px solid var(--line-strong);
  font-size: 13px;
  color: var(--amber);
  background: #17130b;
  box-shadow: var(--shadow);
}
.toast-enter-active,
.toast-leave-active {
  transition: all 260ms ease;
}
.toast-enter-from,
.toast-leave-to {
  opacity: 0;
  transform: translateX(-50%) translateY(10px);
}

@media (max-width: 900px) {
  .ci-main {
    grid-template-columns: 1fr;
  }
  .ci-status {
    flex-direction: column;
    align-items: stretch;
    gap: 16px;
  }
  .ci-status-side {
    min-width: 0;
  }
  .ci-top {
    flex-direction: column;
    align-items: flex-start;
  }
}
</style>
