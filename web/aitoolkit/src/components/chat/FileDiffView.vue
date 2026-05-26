<script setup lang="ts">
import { computed, ref } from 'vue'

const props = defineProps<{
  toolName: string
  args: Record<string, unknown>
  result?: string
}>()

// ── Diff types ──
interface DiffLine {
  type: 'unchanged' | 'added' | 'removed'
  content: string
  oldNum: number | null
  newNum: number | null
}

// ── LCS-based line diff ──
function lcsLen(a: string[], b: string[]): number[][] {
  const m = a.length; const n = b.length
  const dp: number[][] = Array.from({ length: m + 1 }, () => new Array(n + 1).fill(0))
  for (let i = 1; i <= m; i++) {
    for (let j = 1; j <= n; j++) {
      dp[i]![j] = a[i - 1] === b[j - 1]
        ? dp[i - 1]![j - 1]! + 1
        : Math.max(dp[i - 1]![j]!, dp[i]![j - 1]!)
    }
  }
  return dp
}

function backtrack(dp: number[][], a: string[], b: string[]): DiffLine[] {
  const lines: DiffLine[] = []
  let i = a.length; let j = b.length
  const reversed: DiffLine[] = []

  while (i > 0 || j > 0) {
    if (i > 0 && j > 0 && a[i - 1] === b[j - 1]) {
      reversed.push({ type: 'unchanged', content: a[i - 1]!, oldNum: i, newNum: j })
      i--; j--
    } else if (j > 0 && (i === 0 || dp[i]![j - 1]! >= dp[i - 1]![j]!)) {
      reversed.push({ type: 'added', content: b[j - 1]!, oldNum: null, newNum: j })
      j--
    } else {
      reversed.push({ type: 'removed', content: a[i - 1]!, oldNum: i, newNum: null })
      i--
    }
  }

  for (let k = reversed.length - 1; k >= 0; k--) lines.push(reversed[k]!)
  return lines
}

function computeDiff(oldStr: string, newStr: string): DiffLine[] {
  const a = oldStr.split('\n')
  const b = newStr.split('\n')
  const dp = lcsLen(a, b)
  return backtrack(dp, a, b)
}

// ── Context collapse ──
interface CollapsedDiffLine {
  type: DiffLine['type'] | 'ellipsis'
  content: string
  oldNum: number | null
  newNum: number | null
  skipCount: number
}

function collapseDiff(diff: DiffLine[], context = 3): CollapsedDiffLine[] {
  const out: CollapsedDiffLine[] = []
  let skip = 0

  for (let i = 0; i < diff.length; i++) {
    const line = diff[i]!
    if (line.type === 'unchanged') {
      // check if within context of a change
      const hasChangeNearby = diff.slice(Math.max(0, i - context), Math.min(diff.length, i + context + 1))
        .some(l => l.type !== 'unchanged')
      if (hasChangeNearby) {
        if (skip > context * 2) {
          out.push({ type: 'ellipsis', content: '', oldNum: null, newNum: null, skipCount: skip })
        } else {
          for (let j = i - skip; j < i; j++) {
            const ln = diff[j]!
            out.push({ ...ln, skipCount: 0 })
          }
        }
        skip = 0
        out.push({ ...line, skipCount: 0 })
      } else {
        skip++
      }
    } else {
      if (skip > context * 2) {
        out.push({ type: 'ellipsis', content: '', oldNum: null, newNum: null, skipCount: skip })
      } else {
        for (let j = i - skip; j < i; j++) {
          const ln = diff[j]!
          out.push({ ...ln, skipCount: 0 })
        }
      }
      skip = 0
      out.push({ ...line, skipCount: 0 })
    }
  }

  return out
}

// ── Computed display data ──
const filePath = computed(() => {
  const p = props.args.path as string | undefined
  return p || '(unknown)'
})

const fileName = computed(() => {
  const parts = filePath.value.replace(/\\/g, '/').split('/')
  return parts[parts.length - 1] || filePath.value
})

const isEdit = computed(() => props.toolName === 'edit_file')
const isWrite = computed(() => props.toolName === 'write_file')
const isRead = computed(() => props.toolName === 'read_file')

const diffLines = computed<CollapsedDiffLine[]>(() => {
  if (isEdit.value) {
    const oldStr = (props.args.old_string as string) || ''
    const newStr = (props.args.new_string as string) || ''
    const raw = computeDiff(oldStr, newStr)
    return collapseDiff(raw)
  }
  if (isWrite.value) {
    const content = (props.args.content as string) || ''
    if (!content) return []
    return content.split('\n').map((line, i) => ({
      type: 'added' as const,
      content: line,
      oldNum: null,
      newNum: i + 1,
      skipCount: 0,
    }))
  }
  return []
})

const totalAdditions = computed(() => diffLines.value.filter(l => l.type === 'added').length)
const totalDeletions = computed(() => diffLines.value.filter(l => l.type === 'removed').length)
const hasCollapsed = computed(() => diffLines.value.some(l => l.type === 'ellipsis'))

// ── Icon ──
const icon = computed(() => {
  if (isEdit.value) return '✏️'
  if (isWrite.value) return '📄'
  if (isRead.value) return '📖'
  return '🔧'
})

function langClass(): string {
  const ext = fileName.value?.split('.').pop()?.toLowerCase()
  const map: Record<string, string> = {
    ts: 'ts', tsx: 'tsx', js: 'js', jsx: 'jsx', vue: 'vue',
    py: 'py', rs: 'rs', go: 'go', java: 'java', cpp: 'cpp',
    html: 'html', css: 'css', scss: 'scss', json: 'json',
    yaml: 'yaml', yml: 'yaml', md: 'md', sql: 'sql', sh: 'sh',
    toml: 'toml', xml: 'xml',
  }
  return map[ext || ''] || ''
}

const collapsed = ref(false)
function toggleCollapse() {
  collapsed.value = !collapsed.value
}
</script>

<template>
  <div class="file-diff">
    <!-- Header -->
    <button class="diff-header" @click="toggleCollapse" :title="collapsed ? '展开' : '折叠'">
      <span class="diff-icon">{{ icon }}</span>
      <span class="diff-tool">{{ toolName }}</span>
      <span class="diff-path">
        <span class="diff-dir" v-if="filePath !== fileName">{{ filePath.replace(/[^/\\]+$/, '') }}</span>
        <span class="diff-fname">{{ fileName }}</span>
      </span>
      <span v-if="isEdit" class="diff-stats">
        <span class="stat-add">+{{ totalAdditions }}</span>
        <span class="stat-del">−{{ totalDeletions }}</span>
      </span>
      <span v-else-if="isWrite" class="diff-stats">
        <span class="stat-add">+{{ totalAdditions }} 行</span>
      </span>
      <span class="diff-chevron">{{ collapsed ? '▶' : '▼' }}</span>
    </button>

    <div v-if="!collapsed" class="diff-body">
      <!-- Read file: show result content -->
      <div v-if="isRead && result" class="read-content">
        <pre><code>{{ result }}</code></pre>
      </div>
      <div v-else-if="isRead && !result" class="read-content empty">
        <span class="muted">读取中...</span>
      </div>

      <!-- Diff table -->
      <div v-if="diffLines.length > 0" class="diff-table" :class="langClass()">
        <div
          v-for="(line, i) in diffLines"
          :key="i"
          :class="['diff-row', line.type]"
        >
          <template v-if="line.type === 'ellipsis'">
            <span class="ln ln-old">···</span>
            <span class="ln ln-new">···</span>
            <span class="diff-content ellipsis-text">{{ line.skipCount }} 行未变更</span>
          </template>
          <template v-else>
            <span class="ln ln-old">{{ line.oldNum ?? '' }}</span>
            <span class="ln ln-new">{{ line.newNum ?? '' }}</span>
            <span class="diff-sign">{{ line.type === 'added' ? '+' : line.type === 'removed' ? '−' : ' ' }}</span>
            <span class="diff-content">{{ line.content }}</span>
          </template>
        </div>
      </div>

      <!-- Plain result fallback (non-file tools) -->
      <div v-if="result && !isEdit && !isWrite && !isRead" class="plain-result">
        {{ result }}
      </div>
    </div>
  </div>
</template>

<style scoped>
.file-diff {
  margin-top: 0.5rem;
  border: 1px solid rgba(255, 255, 255, 0.06);
  border-radius: 8px;
  overflow: hidden;
  font-family: 'SF Mono', 'Fira Code', 'Cascadia Code', 'Consolas', monospace;
  font-size: 0.78rem;
  background: rgba(0, 0, 0, 0.2);
}

/* ── Header ── */
.diff-header {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  width: 100%;
  padding: 0.5rem 0.75rem;
  background: rgba(255, 255, 255, 0.025);
  border: none;
  border-bottom: 1px solid rgba(255, 255, 255, 0.04);
  color: inherit;
  font-family: inherit;
  font-size: inherit;
  cursor: pointer;
  -webkit-appearance: none;
}
.diff-header:hover {
  background: rgba(255, 255, 255, 0.04);
}
.diff-chevron {
  margin-left: auto;
  font-size: 0.6rem;
  color: #505060;
  flex-shrink: 0;
}
.diff-body {
  /* wrapper for collapsible content */
}
.diff-icon { font-size: 0.85rem; }
.diff-tool {
  font-size: 0.65rem;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: #8888aa;
  background: rgba(255, 255, 255, 0.04);
  padding: 0.15rem 0.4rem;
  border-radius: 4px;
}
.diff-path {
  flex: 1;
  font-size: 0.75rem;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.diff-dir { color: #505060; }
.diff-fname { color: #c0c0d0; font-weight: 500; }

.diff-stats {
  display: flex;
  gap: 0.4rem;
  font-size: 0.7rem;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}
.stat-add { color: #4ade80; }
.stat-del { color: #f87171; }

/* ── Diff table ── */
.diff-table {
  overflow-x: auto;
}
.diff-row {
  display: flex;
  align-items: baseline;
  line-height: 1.55;
  min-height: 1.55em;
}
.diff-row.added {
  background: rgba(74, 222, 128, 0.06);
}
.diff-row.removed {
  background: rgba(248, 113, 113, 0.06);
}

.ln {
  flex-shrink: 0;
  width: 2.8em;
  text-align: right;
  padding-right: 0.6em;
  color: #404050;
  user-select: none;
  font-size: 0.7rem;
}
.ln-old { color: #4a3a3a; }
.ln-new { color: #3a4a3a; }
.diff-row.added .ln-old { color: #2a2a30; }
.diff-row.removed .ln-new { color: #2a2a30; }

.diff-sign {
  flex-shrink: 0;
  width: 1.2em;
  text-align: center;
  font-weight: 600;
  user-select: none;
}
.diff-row.added .diff-sign { color: #4ade80; }
.diff-row.removed .diff-sign { color: #f87171; }
.diff-row.unchanged .diff-sign { color: #303040; }

.diff-content {
  flex: 1;
  white-space: pre;
  color: #c8c8d8;
  padding-right: 0.5rem;
}
.diff-row.ellipsis .diff-content {
  color: #505060;
  font-style: italic;
}
.diff-row.removed .diff-content { color: #d4a0a0; }
.diff-row.added .diff-content { color: #a0d4a0; }

/* ── Read content ── */
.read-content {
  padding: 0.75rem;
  overflow-x: auto;
}
.read-content pre {
  margin: 0;
  color: #c0c0d0;
  white-space: pre-wrap;
  line-height: 1.55;
}
.read-content.empty .muted { color: #505060; }

/* ── Plain result ── */
.plain-result {
  padding: 0.6rem 0.75rem;
  color: #909098;
  font-size: 0.75rem;
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
