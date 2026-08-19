<script setup lang="ts">
const toolGroups = [
  { code: 'WEB', title: '网页工具', description: '常用网页能力的统一入口。', state: '建设中' },
  { code: 'DATA', title: '数据处理', description: '面向文本、文件和数据的辅助操作。', state: '规划中' },
  { code: 'DEV', title: '开发辅助', description: '让日常开发检查更快、更可追踪。', state: '规划中' },
]
</script>

<template>
  <div class="tools-page">
    <section class="tools-intro">
      <div><div class="section-kicker">TOOL CENTER / 04</div><h2>工具中心</h2><p>把高频操作收纳在一个可观测、可扩展的工具台中。</p></div>
      <div class="module-status"><span></span><strong>模块正在搭建</strong><small>接口准备中</small></div>
    </section>
    <section class="tool-grid">
      <article v-for="tool in toolGroups" :key="tool.code" class="tool-card">
        <div class="tool-top"><span class="tool-code">{{ tool.code }}</span><span class="tool-state">{{ tool.state }}</span></div>
        <h3>{{ tool.title }}</h3><p>{{ tool.description }}</p>
        <div class="tool-line"></div><small>ACCESS PENDING</small>
      </article>
    </section>
  </div>
</template>

<style scoped>
.tools-page { max-width: 1080px; margin: 0 auto; padding-top: 34px; }.tools-intro { display: flex; align-items: end; justify-content: space-between; gap: 24px; padding: 28px 0 30px; border-bottom: 1px solid var(--line); }.section-kicker { color: var(--accent); font: 10px Consolas, monospace; letter-spacing: .18em; }.tools-intro h2 { margin: 12px 0 8px; font-size: 34px; letter-spacing: -.03em; }.tools-intro p { margin: 0; color: var(--muted); font-size: 13px; }.module-status { display: flex; align-items: center; gap: 9px; padding: 12px 14px; border: 1px solid rgba(243, 189, 104, .25); color: var(--amber); background: rgba(243, 189, 104, .06); font: 11px Consolas, monospace; }.module-status span { width: 7px; height: 7px; border-radius: 50%; background: var(--amber); box-shadow: 0 0 12px var(--amber); }.module-status small { color: var(--muted); }.tool-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 13px; padding-top: 24px; }.tool-card { min-height: 205px; padding: 19px; border: 1px solid var(--line); background: rgba(17, 15, 9, .66); }.tool-card:hover { border-color: color-mix(in srgb, var(--accent) 35%, transparent); }.tool-top { display: flex; justify-content: space-between; }.tool-code, .tool-state, .tool-card small { color: var(--dim); font: 10px Consolas, monospace; letter-spacing: .1em; }.tool-state { color: var(--amber); }.tool-card h3 { margin: 42px 0 9px; font-size: 19px; }.tool-card p { margin: 0; color: var(--muted); font-size: 12px; line-height: 1.7; }.tool-line { height: 1px; margin: 28px 0 10px; background: linear-gradient(90deg, var(--accent), transparent); opacity: .35; }
@media (max-width: 700px) { .tools-intro { align-items: start; flex-direction: column; }.tool-grid { grid-template-columns: 1fr; } }
</style>
