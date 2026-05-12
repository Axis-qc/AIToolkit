<script setup lang="ts">
import { ref, reactive, onMounted, computed } from 'vue'
import { useRouter } from 'vue-router'

interface ModelOption {
  value: string
  label: string
  provider: string
}

interface ProviderConfig {
  api_key: string
  base_url: string
  chat_model: string
}

interface SettingsData {
  chat_provider: string
  providers: Record<string, ProviderConfig>
  preheat_files: string
  available_models: ModelOption[]
  fetched_models?: Record<string, string[]>
}

const router = useRouter()
const loading = ref(false)
const saved = ref(false)
const error = ref('')
const activeProvider = ref('deepseek')
const fetchingModels = ref(false)

const providers = reactive<Record<string, ProviderConfig>>({
  deepseek: { api_key: '', base_url: '', chat_model: '' },
  openai: { api_key: '', base_url: '', chat_model: '' },
  anthropic: { api_key: '', base_url: '', chat_model: '' },
})

const chatProvider = ref('deepseek')
const preheatFiles = ref('')
const availableModels = ref<ModelOption[]>([])
const fetchedModels = reactive<Record<string, string[]>>({
  deepseek: [],
  openai: [],
  anthropic: [],
})

const showKey = reactive<Record<string, boolean>>({
  deepseek: false,
  openai: false,
  anthropic: false,
})

const activeConfig = computed(() => providers[activeProvider.value] || { api_key: '', base_url: '', chat_model: '' })
const activeFetchedModels = computed(() => fetchedModels[activeProvider.value])

async function loadSettings() {
  try {
    const res = await fetch('/api/settings')
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const data: SettingsData = await res.json()
    chatProvider.value = data.chat_provider
    const names = Object.keys(data.providers) as (keyof typeof providers)[]
    for (const name of names) {
      const cfg = data.providers[name]
      if (cfg) providers[name] = cfg
    }
    preheatFiles.value = data.preheat_files
    availableModels.value = data.available_models || []
    if (data.fetched_models) {
      for (const key of Object.keys(data.fetched_models)) {
        if (key in fetchedModels) {
          fetchedModels[key as keyof typeof fetchedModels] = data.fetched_models[key] || []
        }
      }
    }
  } catch (e: any) {
    error.value = `加载失败: ${e.message}`
  }
}

async function fetchModels(provider: string) {
  fetchingModels.value = true
  try {
    const res = await fetch(`/api/settings/models?provider=${provider}`)
    if (!res.ok) return
    const data = await res.json()
    fetchedModels[provider] = (data.data || []).map((m: any) => m.id)
  } catch {
    // ignore
  } finally {
    fetchingModels.value = false
  }
}

async function saveSettings() {
  loading.value = true
  error.value = ''
  saved.value = false
  try {
    const res = await fetch('/api/settings', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        chat_provider: chatProvider.value,
        providers: {
          deepseek: providers.deepseek,
          openai: providers.openai,
          anthropic: providers.anthropic,
        },
        preheat_files: preheatFiles.value,
      }),
    })
    if (!res.ok) {
      const data = await res.json()
      throw new Error(data.detail || `HTTP ${res.status}`)
    }
    const result = await res.json()
    if (result.providers) {
      for (const name of Object.keys(result.providers)) {
        if (providers[name]) {
          providers[name] = result.providers[name]
        }
      }
    }
    if (result.fetched_models) {
      for (const key of Object.keys(result.fetched_models)) {
        if (key in fetchedModels) {
          fetchedModels[key as keyof typeof fetchedModels] = result.fetched_models[key] || []
        }
      }
    }
    saved.value = true
    setTimeout(() => (saved.value = false), 3000)
  } catch (e: any) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

function isMasked(val: string): boolean {
  return val.includes('...') || val.startsWith('*')
}

onMounted(loadSettings)
</script>

<template>
  <div class="settings-page">
    <div class="bg-orbs">
      <div class="orb orb-1"></div>
      <div class="orb orb-2"></div>
    </div>
    <div class="grid-overlay"></div>

    <div class="content">
      <header class="page-header">
        <button class="back-btn" @click="router.push('/')">返回</button>
        <h1>设置</h1>
        <p class="subtitle">多 Provider 管理 · API 配置 · 模型切换</p>
      </header>

      <form class="settings-form" @submit.prevent="saveSettings">
        <div class="form-body">
          <!-- Provider 切换 -->
          <div class="field">
            <label>当前使用的 Provider</label>
            <select v-model="chatProvider">
              <option value="deepseek">DeepSeek</option>
              <option value="openai">OpenAI</option>
              <option value="anthropic">Anthropic</option>
            </select>
            <span class="hint">切换后对话将使用对应 Provider 的 API Key 和模型</span>
          </div>

          <!-- Per-Provider 配置区 -->
          <div class="provider-tabs">
            <button
              v-for="p in ['deepseek', 'openai', 'anthropic']"
              :key="p"
              type="button"
              class="tab-btn"
              :class="{ active: activeProvider === p }"
              @click="activeProvider = p"
            >
              {{ p === 'deepseek' ? 'DeepSeek' : p === 'openai' ? 'OpenAI' : 'Anthropic' }}
            </button>
          </div>

          <div class="provider-section">
            <div class="field">
              <label :for="'api_key_' + activeProvider">API Key</label>
              <div class="key-input-wrap">
                <input
                  :id="'api_key_' + activeProvider"
                  v-model="activeConfig.api_key"
                  :type="showKey[activeProvider] ? 'text' : 'password'"
                  :placeholder="isMasked(activeConfig.api_key) ? '已配置（留空不修改）' : 'sk-...'"
                />
                <button type="button" class="toggle-btn" @click="showKey[activeProvider] = !showKey[activeProvider]">
                  {{ showKey[activeProvider] ? '隐藏' : '显示' }}
                </button>
              </div>
              <span class="hint">修改后保存，含 <code>...</code> 或全星号表示不更新</span>
            </div>

            <div class="field">
              <label :for="'base_url_' + activeProvider">API 地址</label>
              <input
                :id="'base_url_' + activeProvider"
                v-model="activeConfig.base_url"
                type="text"
                placeholder="https://api.deepseek.com"
              />
            </div>

            <div class="field">
              <label :for="'chat_model_' + activeProvider">模型</label>
              <div class="model-row">
                <select
                  :id="'chat_model_' + activeProvider"
                  v-model="activeConfig.chat_model"
                  class="model-select"
                >
                  <option value="" disabled>选择模型...</option>
                  <optgroup
                    v-for="group in [...new Set(availableModels.filter(m => m.provider === activeProvider).map(m => m.provider))]"
                    :key="group"
                    :label="group === activeProvider ? activeProvider : group"
                  >
                    <option
                      v-for="m in availableModels.filter(x => x.provider === activeProvider)"
                      :key="m.value"
                      :value="m.value"
                    >
                      {{ m.label }}
                    </option>
                  </optgroup>
                  <option
                    v-for="mid in activeFetchedModels"
                    :key="mid"
                    :value="mid"
                  >
                    {{ mid }}
                  </option>
                </select>
                <button type="button" class="fetch-btn" :disabled="fetchingModels" @click="fetchModels(activeProvider)">
                  {{ fetchingModels ? '拉取中...' : '拉取列表' }}
                </button>
              </div>
            </div>
          </div>

          <!-- Preheat Files -->
          <div class="field">
            <label for="preheat_files">预热白名单</label>
            <input
              id="preheat_files"
              v-model="preheatFiles"
              type="text"
              placeholder="app/core/:*.py app/tools/:*.py"
            />
            <span class="hint">空格分隔，格式 path[:ext1,ext2]</span>
          </div>
        </div>

        <!-- Actions -->
        <div class="actions">
          <button type="submit" class="save-btn" :disabled="loading">
            {{ loading ? '保存中...' : '保存所有配置' }}
          </button>
          <span v-if="saved" class="toast-success">配置已保存</span>
          <span v-if="error" class="toast-error">{{ error }}</span>
        </div>
      </form>
    </div>
  </div>
</template>

<style scoped>
.settings-page {
  height: 100vh;
  background: #0b0d17;
  color: #e0e0e0;
  position: relative;
  overflow: hidden;
  display: flex;
  justify-content: center;
  padding: 20px;
  box-sizing: border-box;
}

.bg-orbs {
  position: absolute;
  inset: 0;
  pointer-events: none;
}

.orb {
  position: absolute;
  border-radius: 50%;
  filter: blur(120px);
  opacity: 0.25;
  animation: orb-drift 20s ease-in-out infinite alternate;
}

.orb-1 {
  width: 600px;
  height: 600px;
  background: radial-gradient(circle, #7c5cfc44, transparent 70%);
  top: -15%;
  left: -10%;
  animation-delay: 0s;
}

.orb-2 {
  width: 500px;
  height: 500px;
  background: radial-gradient(circle, #4da6d922, transparent 70%);
  bottom: -20%;
  right: -8%;
  animation-delay: -7s;
}

@keyframes orb-drift {
  0% { transform: translate(0, 0) scale(1); }
  100% { transform: translate(40px, -30px) scale(1.15); }
}

.grid-overlay {
  position: absolute;
  inset: 0;
  background-image:
    linear-gradient(rgba(255,255,255,0.02) 1px, transparent 1px),
    linear-gradient(90deg, rgba(255,255,255,0.02) 1px, transparent 1px);
  background-size: 64px 64px;
  pointer-events: none;
  mask-image: radial-gradient(ellipse at center, black 30%, transparent 70%);
}

.content {
  position: relative;
  z-index: 1;
  width: 100%;
  max-width: 680px;
}

.page-header {
  text-align: center;
  margin-bottom: 20px;
  position: relative;
}

.page-header h1 {
  font-size: 2rem;
  margin: 0 0 8px;
}

.subtitle {
  color: #888;
  font-size: 0.95rem;
}

.back-btn {
  position: absolute;
  left: 0;
  top: 8px;
  background: none;
  border: 1px solid #333;
  color: #aaa;
  padding: 6px 14px;
  border-radius: 8px;
  cursor: pointer;
  font-size: 0.9rem;
  transition: all 0.2s;
}

.back-btn:hover {
  border-color: #7c5cfc;
  color: #fff;
}

.settings-form {
  background: rgba(255,255,255,0.03);
  border: 1px solid #1e2030;
  border-radius: 16px;
  padding: 24px;
  backdrop-filter: blur(12px);
  max-height: calc(100vh - 140px);
  overflow-y: auto;
  display: flex;
  flex-direction: column;
}



.field {
  margin-bottom: 16px;
}

.field label {
  display: block;
  margin-bottom: 6px;
  font-weight: 600;
  color: #ccc;
  font-size: 0.9rem;
}

.field input,
.field select {
  width: 100%;
  padding: 10px 14px;
  background: #11131f;
  border: 1px solid #2a2d3a;
  border-radius: 10px;
  color: #e0e0e0;
  font-size: 0.95rem;
  outline: none;
  transition: border-color 0.2s;
  box-sizing: border-box;
}

.field input:focus,
.field select:focus {
  border-color: #7c5cfc;
}

.field .hint {
  display: block;
  margin-top: 4px;
  font-size: 0.8rem;
  color: #666;
}

.field .hint code {
  background: #1a1d2e;
  padding: 1px 5px;
  border-radius: 4px;
  font-size: 0.8rem;
}

.provider-tabs {
  display: flex;
  gap: 8px;
  margin-bottom: 16px;
}

.tab-btn {
  flex: 1;
  padding: 10px;
  background: #11131f;
  border: 1px solid #2a2d3a;
  border-radius: 10px;
  color: #888;
  font-size: 0.9rem;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.2s;
}

.tab-btn:hover {
  border-color: #444;
  color: #ccc;
}

.tab-btn.active {
  border-color: #7c5cfc;
  color: #fff;
  background: rgba(124, 92, 252, 0.1);
}

.provider-section {
  background: rgba(255,255,255,0.02);
  border: 1px solid #1e2030;
  border-radius: 12px;
  padding: 16px;
  margin-bottom: 16px;
}

.key-input-wrap {
  display: flex;
  gap: 8px;
}

.key-input-wrap input {
  flex: 1;
}

.toggle-btn {
  background: #1a1d2e;
  border: 1px solid #2a2d3a;
  color: #aaa;
  padding: 10px 14px;
  border-radius: 10px;
  cursor: pointer;
  font-size: 0.9rem;
  transition: border-color 0.2s;
}

.toggle-btn:hover {
  border-color: #7c5cfc;
}

.model-row {
  display: flex;
  gap: 8px;
}

.model-select {
  flex: 1;
}

.fetch-btn {
  background: #1a1d2e;
  border: 1px solid #2a2d3a;
  color: #aaa;
  padding: 10px 14px;
  border-radius: 10px;
  cursor: pointer;
  font-size: 0.85rem;
  white-space: nowrap;
  transition: border-color 0.2s;
}

.fetch-btn:hover:not(:disabled) {
  border-color: #7c5cfc;
  color: #fff;
}

.fetch-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

select {
  cursor: pointer;
}

select optgroup {
  background: #11131f;
  color: #888;
  font-style: normal;
}

select option {
  background: #11131f;
  color: #e0e0e0;
}

.form-body {
  flex: 1;
  overflow-y: auto;
  padding-right: 4px;
}

.form-body::-webkit-scrollbar {
  width: 6px;
}
.form-body::-webkit-scrollbar-track {
  background: transparent;
}
.form-body::-webkit-scrollbar-thumb {
  background: #2a2d3a;
  border-radius: 3px;
}

.actions {
  display: flex;
  align-items: center;
  gap: 16px;
  padding-top: 20px;
  border-top: 1px solid #1e2030;
  margin-top: 20px;
  flex-shrink: 0;
  flex-wrap: wrap;
}

.save-btn {
  padding: 12px 32px;
  background: linear-gradient(135deg, #7c5cfc, #5b3ecc);
  border: none;
  border-radius: 10px;
  color: #fff;
  font-size: 1rem;
  font-weight: 600;
  cursor: pointer;
  transition: opacity 0.2s, transform 0.1s;
}

.save-btn:hover:not(:disabled) {
  opacity: 0.9;
  transform: translateY(-1px);
}

.save-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.toast-success {
  color: #4ade80;
  font-size: 0.9rem;
}

.toast-error {
  color: #f87171;
  font-size: 0.9rem;
}
</style>
