<script setup lang="ts">
import { ref, onMounted } from 'vue'
import {
  PRESETS,
  DEFAULT_ACCENT,
  normalizeAccent,
  applyTheme,
  initTheme,
  saveTheme,
  resetTheme,
} from '@/stores/theme'

const hexInput = ref(DEFAULT_ACCENT)
const saving = ref(false)
const saved = ref(false)
const error = ref('')

onMounted(async () => {
  await initTheme()
  hexInput.value = normalizeAccent(document.documentElement.style.getPropertyValue('--accent')) || DEFAULT_ACCENT
})

/** 实时预览：仅本地应用，不落盘 */
function onInput(hex: string) {
  const v = (hex || '').trim()
  hexInput.value = v
  if (normalizeAccent(v)) applyTheme(v)
}

async function onSave() {
  error.value = ''
  saved.value = false
  saving.value = true
  const res = await saveTheme(hexInput.value)
  saving.value = false
  if (res.ok) {
    saved.value = true
    setTimeout(() => (saved.value = false), 1800)
  } else {
    error.value = res.error || '保存失败'
  }
}

async function onReset() {
  hexInput.value = DEFAULT_ACCENT
  await resetTheme()
  error.value = ''
}
</script>

<template>
  <div class="theme-page">
    <section class="theme-intro">
      <div>
        <div class="section-kicker">APPEARANCE / THEME CONTROL</div>
        <h2>主题外观</h2>
        <p>自由调整主色，系统自动派生高光、暗色与线条配色。选择即实时预览，保存后写入本机配置。</p>
      </div>
      <div class="live-chip">
        <span class="live-dot"></span>
        <strong>LIVE PREVIEW</strong>
      </div>
    </section>

    <section class="theme-card">
      <div class="theme-row">
        <label>主色</label>
        <div class="picker-wrap">
          <input
            type="color"
            class="color-input"
            :value="normalizeAccent(hexInput) || '#000000'"
            @input="onInput(($event.target as HTMLInputElement).value)"
          />
          <input
            class="hex-input"
            :value="hexInput"
            spellcheck="false"
            placeholder="#d4af37"
            @input="onInput(($event.target as HTMLInputElement).value)"
          />
        </div>
      </div>

      <div class="theme-row">
        <label>快捷预设</label>
        <div class="presets">
          <button
            v-for="p in PRESETS"
            :key="p.color"
            :class="['preset', { active: normalizeAccent(hexInput) === p.color }]"
            :style="{ '--swatch': p.color }"
            :title="p.name"
            @click="onInput(p.color)"
          >
            <span class="swatch"></span>
            <span class="preset-name">{{ p.name }}</span>
          </button>
        </div>
      </div>

      <div class="preview">
        <div class="preview-card">
          <div class="preview-head">
            <span class="pv-kicker">PREVIEW / SAMPLE</span>
            <strong>模块卡片</strong>
          </div>
          <div class="preview-kpi">
            <span class="pv-label">主色示例</span>
            <strong class="pv-big">{{ normalizeAccent(hexInput) || '无效' }}</strong>
          </div>
          <div class="pv-bar"><i></i></div>
          <div class="pv-actions">
            <button class="pv-btn">主要操作</button>
            <span class="pv-tag">状态标签</span>
          </div>
        </div>
      </div>

      <div class="theme-actions">
        <button class="save-btn" :disabled="saving" @click="onSave">
          {{ saving ? '保存中' : '保存主题' }}
        </button>
        <button class="reset-btn" @click="onReset">恢复默认</button>
        <span v-if="saved" class="toast-success">已保存到本机配置</span>
        <span v-else-if="error" class="toast-error">{{ error }}</span>
      </div>
    </section>
  </div>
</template>

<style scoped>
.theme-page { max-width: 1080px; margin: 0 auto; padding-top: 34px; }

.section-kicker { color: var(--accent); font: 10px Consolas, monospace; letter-spacing: .18em; }
.theme-intro { display: flex; align-items: end; justify-content: space-between; gap: 24px; padding: 28px 0 30px; border-bottom: 1px solid var(--line); }
.theme-intro h2 { margin: 12px 0 8px; font-size: 34px; letter-spacing: -.03em; }
.theme-intro p { margin: 0; color: var(--muted); font-size: 13px; }

.live-chip { display: flex; align-items: center; gap: 9px; padding: 12px 14px; border: 1px solid var(--line-strong); color: var(--accent); background: color-mix(in srgb, var(--accent) 6%, transparent); font: 11px Consolas, monospace; }
.live-dot { width: 7px; height: 7px; border-radius: 50%; background: var(--accent); box-shadow: 0 0 12px var(--accent); animation: live-pulse 1.6s ease-in-out infinite; }
@keyframes live-pulse { 0%, 100% { opacity: 1; } 50% { opacity: .35; } }

.theme-card { margin-top: 20px; padding: 24px; border: 1px solid var(--line); border-radius: 8px; background: var(--panel); box-shadow: var(--shadow); }

.theme-row { display: flex; align-items: center; gap: 18px; padding: 13px 0; border-bottom: 1px solid var(--line); }
.theme-row label { flex: 0 0 92px; color: var(--muted); font: 11px Consolas, monospace; letter-spacing: .1em; text-transform: uppercase; }

.picker-wrap { display: flex; align-items: center; gap: 12px; }
.color-input { width: 46px; height: 34px; padding: 0; border: 1px solid var(--line-strong); border-radius: 6px; background: transparent; cursor: pointer; }
.color-input::-webkit-color-swatch-wrapper { padding: 3px; }
.color-input::-webkit-color-swatch { border: none; border-radius: 4px; }
.hex-input { width: 150px; padding: 9px 12px; color: var(--text); background: var(--bg-1); border: 1px solid var(--line); border-radius: 6px; font: 13px Consolas, monospace; outline: none; transition: border-color .2s; }
.hex-input:focus { border-color: var(--accent); box-shadow: 0 0 0 3px color-mix(in srgb, var(--accent) 10%, transparent); }

.presets { display: flex; flex-wrap: wrap; gap: 8px; }
.preset { display: flex; align-items: center; gap: 7px; padding: 6px 11px 6px 7px; border: 1px solid var(--line); border-radius: 6px; color: var(--muted); background: var(--bg-1); cursor: pointer; font-size: 12px; transition: 180ms ease; }
.preset:hover { color: var(--text); border-color: var(--line-strong); }
.preset.active { color: var(--text); border-color: var(--accent); background: color-mix(in srgb, var(--accent) 10%, transparent); }
.swatch { width: 16px; height: 16px; border-radius: 50%; background: var(--swatch); box-shadow: 0 0 10px color-mix(in srgb, var(--swatch) 55%, transparent); }
.preset-name { font-family: Consolas, monospace; font-size: 11px; letter-spacing: .05em; }

.preview { padding: 18px 0; }
.preview-card { max-width: 460px; padding: 18px; border: 1px solid var(--line); border-radius: 8px; background: var(--panel-soft); transition: border-color .2s; }
.preview-card:hover { border-color: var(--line-strong); }
.preview-head { display: flex; align-items: center; justify-content: space-between; padding-bottom: 12px; border-bottom: 1px solid var(--line); }
.pv-kicker { color: var(--accent); font: 10px Consolas, monospace; letter-spacing: .16em; }
.preview-head strong { color: var(--text); font-size: 13px; font-weight: 600; }
.preview-kpi { display: flex; align-items: baseline; justify-content: space-between; padding: 16px 0 10px; }
.pv-label { color: var(--muted); font-size: 11px; }
.pv-big { color: var(--accent); font: 700 20px Consolas, monospace; text-shadow: 0 0 18px color-mix(in srgb, var(--accent) 45%, transparent); letter-spacing: .04em; }
.pv-bar { height: 4px; overflow: hidden; border-radius: 2px; background: color-mix(in srgb, var(--accent) 12%, transparent); }
.pv-bar i { display: block; width: 62%; height: 100%; background: linear-gradient(90deg, var(--accent-deep), var(--accent-bright)); box-shadow: 0 0 14px var(--accent); }
.pv-actions { display: flex; align-items: center; gap: 12px; padding-top: 16px; }
.pv-btn { padding: 9px 16px; border: 1px solid var(--accent); border-radius: 6px; color: var(--bg-0); background: linear-gradient(135deg, var(--accent-bright), var(--accent)); font: 600 12px Consolas, monospace; box-shadow: 0 0 20px color-mix(in srgb, var(--accent) 22%, transparent); }
.pv-tag { padding: 5px 11px; border: 1px solid var(--line-strong); border-radius: 99px; color: var(--accent); background: color-mix(in srgb, var(--accent) 8%, transparent); font: 10px Consolas, monospace; letter-spacing: .08em; }

.theme-actions { display: flex; align-items: center; gap: 12px; padding-top: 18px; border-top: 1px solid var(--line); }
.save-btn { padding: 11px 22px; border: 1px solid var(--accent); border-radius: 6px; color: var(--bg-0); background: linear-gradient(135deg, var(--accent-bright), var(--accent)); font: 600 12px Consolas, monospace; cursor: pointer; transition: 180ms ease; }
.save-btn:hover:not(:disabled) { box-shadow: 0 0 22px color-mix(in srgb, var(--accent) 28%, transparent); }
.save-btn:disabled { opacity: .55; cursor: not-allowed; }
.reset-btn { padding: 11px 18px; border: 1px solid var(--line); border-radius: 6px; color: var(--muted); background: var(--bg-1); font: 12px Consolas, monospace; cursor: pointer; transition: 180ms ease; }
.reset-btn:hover { color: var(--text); border-color: var(--line-strong); }
.toast-success { color: var(--green); font: 12px Consolas, monospace; }
.toast-error { color: var(--red); font: 12px Consolas, monospace; }

@media (max-width: 700px) {
  .theme-intro { align-items: start; flex-direction: column; }
  .theme-row { align-items: start; flex-direction: column; gap: 10px; }
  .presets { gap: 7px; }
}
</style>
