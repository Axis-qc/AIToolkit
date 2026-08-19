<script setup lang="ts">
import { RouterLink } from 'vue-router'

const modules = [
  { code: 'PH', title: '手机监控', description: '实时查看设备资源、进程排行和历史采样趋势。', route: '/phone-monitor', accent: 'cyan', metric: '3s', metricLabel: '采样周期' },
  { code: 'KG', title: '知识图谱', description: '检视实体关系、事实节点和记忆网络的结构。', route: '/graph', accent: 'violet', metric: 'LIVE', metricLabel: '图谱状态' },
  { code: 'SK', title: '主题外观', description: '自由调整主色，系统自动派生整套界面配色。', route: '/settings', accent: 'amber', metric: 'LIVE', metricLabel: '实时预览' },
  { code: 'TL', title: '工具中心', description: '集中放置日常使用的网页工具和快捷操作。', route: '/tools', accent: 'blue', metric: 'SOON', metricLabel: '模块状态' },
]
</script>

<template>
  <div class="overview-page">
    <section class="overview-hero panel-surface">
      <div class="hero-copy">
        <div class="section-kicker">SYSTEM OVERVIEW / 00</div>
        <h2>本地工作台<br /><span>运行态势总览</span></h2>
        <p>一处查看当前工具、设备与记忆系统的运行入口。数据优先，状态清晰，所有内容留在本地环境。</p>
        <div class="hero-actions">
          <RouterLink to="/phone-monitor" class="primary-action">打开手机监控 <span>→</span></RouterLink>
          <span class="hero-note"><i></i> 本地节点已连接</span>
        </div>
      </div>
      <div class="hero-visual" aria-hidden="true">
        <div class="radar-grid"></div>
        <div class="radar-ring ring-a"></div>
        <div class="radar-ring ring-b"></div>
        <div class="radar-core"><span></span></div>
        <div class="radar-label label-a">LOCAL / 01</div>
        <div class="radar-label label-b">NODE ACTIVE</div>
      </div>
    </section>

    <section class="overview-strip">
      <div><span class="strip-label">当前环境</span><strong>AIToolkit Desktop</strong></div>
      <div><span class="strip-label">数据策略</span><strong>LOCAL ONLY</strong></div>
      <div><span class="strip-label">可用模块</span><strong>04 <em>/ 06</em></strong></div>
      <div><span class="strip-label">系统时间</span><strong>{{ new Date().toLocaleDateString('zh-CN') }}</strong></div>
    </section>

    <section class="module-section">
      <div class="section-heading">
        <div><div class="section-kicker">MODULES / 01</div><h3>运行模块</h3></div>
        <span class="heading-note">SELECT MODULE TO CONTINUE</span>
      </div>
      <div class="module-grid">
        <RouterLink v-for="module in modules" :key="module.route" :to="module.route" :class="['module-card', `accent-${module.accent}`]">
          <div class="module-card-top"><span class="module-code">{{ module.code }}</span><span class="module-arrow">↗</span></div>
          <h4>{{ module.title }}</h4>
          <p>{{ module.description }}</p>
          <div class="module-card-foot"><span>{{ module.metricLabel }}</span><strong>{{ module.metric }}</strong></div>
        </RouterLink>
      </div>
    </section>
  </div>
</template>

<style>
.overview-page { max-width: 1280px; margin: 0 auto; padding-top: 28px; }
.panel-surface { border: 1px solid var(--line); background: linear-gradient(120deg, rgba(22, 20, 12, 0.88), rgba(12, 11, 8, 0.74)); box-shadow: var(--shadow); }
.overview-hero { min-height: 300px; display: flex; justify-content: space-between; gap: 32px; padding: 40px 44px; overflow: hidden; position: relative; }
.overview-hero::after { content: ''; position: absolute; left: 0; bottom: 0; width: 46%; height: 2px; background: linear-gradient(90deg, var(--accent), transparent); }
.hero-copy { position: relative; z-index: 1; max-width: 540px; }
.section-kicker { color: var(--accent); font-family: Consolas, monospace; font-size: 10px; letter-spacing: .18em; }
.hero-copy h2 { margin: 18px 0 14px; font-size: clamp(32px, 4vw, 52px); line-height: 1.05; font-weight: 650; letter-spacing: -.04em; }
.hero-copy h2 span { color: #d8c890; }
.hero-copy p { max-width: 470px; margin: 0; color: var(--muted); font-size: 14px; line-height: 1.75; }
.hero-actions { display: flex; align-items: center; gap: 18px; margin-top: 28px; flex-wrap: wrap; }
.primary-action { display: inline-flex; align-items: center; gap: 14px; padding: 11px 15px; border: 1px solid color-mix(in srgb, var(--accent) 55%, transparent); border-radius: 6px; color: var(--text); background: color-mix(in srgb, var(--accent) 13%, transparent); text-decoration: none; font-size: 12px; transition: 180ms ease; }
.primary-action:hover { background: color-mix(in srgb, var(--accent) 23%, transparent); box-shadow: 0 0 26px color-mix(in srgb, var(--accent) 14%, transparent); }
.primary-action span { color: var(--accent); font-size: 17px; }
.hero-note { color: var(--muted); font-family: Consolas, monospace; font-size: 10px; }
.hero-note i { display: inline-block; width: 6px; height: 6px; margin-right: 4px; border-radius: 50%; background: var(--green); box-shadow: 0 0 10px var(--green); }
.hero-visual { position: relative; width: 320px; min-width: 260px; height: 240px; margin: -5px -5px 0 0; opacity: .9; }
.radar-grid { position: absolute; inset: 15px; border: 1px solid color-mix(in srgb, var(--accent) 14%, transparent); background-image: linear-gradient(color-mix(in srgb, var(--accent) 12%, transparent) 1px, transparent 1px), linear-gradient(90deg, color-mix(in srgb, var(--accent) 12%, transparent) 1px, transparent 1px); background-size: 32px 32px; transform: perspective(360px) rotateX(55deg) rotateZ(-12deg); transform-origin: center bottom; }
.radar-ring { position: absolute; left: 50%; top: 48%; transform: translate(-50%, -50%); border: 1px solid color-mix(in srgb, var(--accent) 34%, transparent); border-radius: 50%; box-shadow: 0 0 36px color-mix(in srgb, var(--accent) 8%, transparent) inset; }
.ring-a { width: 170px; height: 170px; }.ring-b { width: 94px; height: 94px; border-color: rgba(126, 226, 168, .42); }
.radar-core { position: absolute; left: 50%; top: 48%; width: 18px; height: 18px; transform: translate(-50%, -50%); border: 1px solid var(--accent); border-radius: 50%; box-shadow: 0 0 30px color-mix(in srgb, var(--accent) 75%, transparent); }
.radar-core span { display: block; width: 4px; height: 4px; margin: 6px; border-radius: 50%; background: var(--green); }
.radar-label { position: absolute; color: var(--muted); font: 10px Consolas, monospace; letter-spacing: .1em; }.label-a { top: 20px; right: 12px; }.label-b { left: 5px; bottom: 32px; color: var(--green); }
.overview-strip { display: grid; grid-template-columns: repeat(4, 1fr); margin-top: 16px; border: 1px solid var(--line); background: rgba(16, 14, 9, .6); }
.overview-strip > div { padding: 14px 18px; border-right: 1px solid var(--line); }.overview-strip > div:last-child { border-right: 0; }.strip-label { display: block; margin-bottom: 6px; color: var(--dim); font: 10px Consolas, monospace; text-transform: uppercase; letter-spacing: .08em; }.overview-strip strong { color: #d8c890; font: 12px Consolas, monospace; }.overview-strip em { color: var(--dim); font-style: normal; }
.module-section { padding: 32px 0; }.section-heading { display: flex; align-items: end; justify-content: space-between; margin-bottom: 13px; }.section-heading h3 { margin: 6px 0 0; font-size: 19px; font-weight: 600; }.heading-note { color: var(--dim); font: 10px Consolas, monospace; letter-spacing: .08em; }
.module-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; }.module-card { min-height: 190px; display: flex; flex-direction: column; padding: 18px; border: 1px solid var(--line); border-radius: 7px; color: var(--text); text-decoration: none; background: rgba(17, 15, 9, .72); transition: 180ms ease; --card-accent: var(--accent); }.module-card:hover { transform: translateY(-3px); border-color: var(--card-accent); background: rgba(28, 24, 13, .85); box-shadow: 0 18px 40px rgba(0,0,0,.25); }.module-card-top { display: flex; justify-content: space-between; }.module-code { color: var(--card-accent); font: 11px Consolas, monospace; letter-spacing: .12em; }.module-arrow { color: var(--dim); }.module-card h4 { margin: 34px 0 8px; font-size: 17px; font-weight: 600; }.module-card p { min-height: 43px; margin: 0; color: var(--muted); font-size: 12px; line-height: 1.65; }.module-card-foot { display: flex; justify-content: space-between; align-items: end; margin-top: auto; padding-top: 18px; border-top: 1px solid var(--line); color: var(--dim); font: 10px Consolas, monospace; }.module-card-foot strong { color: var(--card-accent); font-size: 12px; }.accent-cyan { --card-accent: var(--accent); }.accent-violet { --card-accent: var(--accent-bright); }.accent-amber { --card-accent: color-mix(in srgb, var(--accent) 72%, #ffffff 28%); }.accent-blue { --card-accent: var(--accent-deep); }
@media (max-width: 960px) { .module-grid { grid-template-columns: repeat(2, 1fr); }.hero-visual { width: 250px; }.overview-hero { padding: 32px; } }
@media (max-width: 650px) { .overview-page { padding-top: 18px; }.overview-hero { min-height: 0; padding: 26px 22px; }.hero-visual { display: none; }.overview-strip { grid-template-columns: repeat(2, 1fr); }.overview-strip > div:nth-child(2) { border-right: 0; }.overview-strip > div:nth-child(-n+2) { border-bottom: 1px solid var(--line); }.module-grid { grid-template-columns: 1fr; }.section-heading { align-items: start; gap: 8px; flex-direction: column; } }
</style>
