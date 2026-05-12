<script setup lang="ts">
import { RouterLink } from 'vue-router'

interface FeatureCard {
  title: string
  description: string
  icon: string
  route: string
  available: boolean
  accent: string
}

const cards: FeatureCard[] = [
  {
    title: '知识图谱对话',
    description: '以你为中心的知识图谱记忆系统，AI 持久化记忆，跨对话检索。每次对话都基于过去的记忆。',
    icon: '◈',
    route: '/chat',
    available: true,
    accent: '#7c5cfc',
  },
  {
    title: '网页工具',
    description: '各类实用网页工具的集合，提升日常工作效率。',
    icon: '⬡',
    route: '/tools',
    available: false,
    accent: '#4da6d9',
  },
  {
    title: '小游戏',
    description: '闲暇时刻的放松之选，轻量有趣的网页小游戏。',
    icon: '◆',
    route: '/games',
    available: false,
    accent: '#f0a050',
  },
  {
    title: '设置',
    description: '系统配置与个性化设置，管理你的偏好与数据。',
    icon: '◇',
    route: '/settings',
    available: true,
    accent: '#6b7280',
  },
]
</script>

<template>
  <div class="home">
    <div class="bg-orbs">
      <div class="orb orb-1"></div>
      <div class="orb orb-2"></div>
      <div class="orb orb-3"></div>
    </div>

    <div class="grid-overlay"></div>

    <div class="content">
      <header class="hero">
        <div class="logo-mark">◈</div>
        <h1 class="title">AI 工作台</h1>
        <p class="subtitle">智能 · 高效 · 持续记忆</p>
      </header>

      <div class="card-grid">
        <component
          :is="card.available ? RouterLink : 'button'"
          v-for="(card, i) in cards"
          :key="card.title"
          :to="card.available ? card.route : undefined"
          :class="['card', { available: card.available }]"
          :style="{ '--accent': card.accent, '--delay': `${i * 0.1}s` }"
          :disabled="!card.available ? true : undefined"
        >
          <div class="card-icon" :style="{ background: `${card.accent}18` }">
            <span :style="{ color: card.accent }">{{ card.icon }}</span>
          </div>
          <div class="card-body">
            <div class="card-header">
              <h3 class="card-title">{{ card.title }}</h3>
              <span :class="['badge', card.available ? 'badge-on' : 'badge-off']">
                {{ card.available ? '可用' : '开发中' }}
              </span>
            </div>
            <p class="card-desc">{{ card.description }}</p>
          </div>
          <div v-if="card.available" class="card-arrow">→</div>
        </component>
      </div>

      <footer class="footer">
        <span>v0.1.0 · 本地运行 · 数据由你掌控</span>
      </footer>
    </div>
  </div>
</template>

<style scoped>
.home {
  position: relative;
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #08080c;
  overflow: hidden;
  font-family: inherit;
}

/* ── Background Orbs ── */
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

.orb-3 {
  width: 400px;
  height: 400px;
  background: radial-gradient(circle, #f0a05018, transparent 70%);
  top: 50%;
  left: 55%;
  animation-delay: -14s;
}

@keyframes orb-drift {
  0% { transform: translate(0, 0) scale(1); }
  100% { transform: translate(40px, -30px) scale(1.15); }
}

/* ── Grid Overlay ── */
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

/* ── Content ── */
.content {
  position: relative;
  z-index: 1;
  width: 100%;
  max-width: 960px;
  padding: 2rem;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 3rem;
}

/* ── Hero ── */
.hero {
  text-align: center;
  animation: fade-up 0.8s ease-out;
}

.logo-mark {
  font-size: 2.5rem;
  color: #7c5cfc;
  margin-bottom: 0.5rem;
  filter: drop-shadow(0 0 20px #7c5cfc44);
}

.title {
  font-size: 2.8rem;
  font-weight: 700;
  color: #f0f0f3;
  letter-spacing: -0.02em;
  margin: 0;
}

.subtitle {
  font-size: 1.05rem;
  color: #6b6b80;
  margin: 0.6rem 0 0;
  letter-spacing: 0.15em;
  font-weight: 400;
}

/* ── Card Grid ── */
.card-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 1.25rem;
  width: 100%;
  animation: fade-up 0.8s ease-out 0.15s both;
}

@media (max-width: 640px) {
  .card-grid {
    grid-template-columns: 1fr;
  }
  .title {
    font-size: 2rem;
  }
}

/* ── Card ── */
.card {
  display: flex;
  align-items: flex-start;
  gap: 1rem;
  padding: 1.5rem;
  background: rgba(255, 255, 255, 0.025);
  border: 1px solid rgba(255, 255, 255, 0.06);
  border-radius: 16px;
  cursor: pointer;
  transition: all 0.35s cubic-bezier(0.25, 0.1, 0.25, 1);
  text-align: left;
  text-decoration: none;
  color: inherit;
  font-family: inherit;
  font-size: inherit;
  position: relative;
  overflow: hidden;
  animation: fade-up 0.6s ease-out var(--delay) both;
}

.card::before {
  content: '';
  position: absolute;
  inset: 0;
  border-radius: 16px;
  background: radial-gradient(600px circle at var(--mouse-x, 50%) var(--mouse-y, 50%), var(--accent)06, transparent 40%);
  opacity: 0;
  transition: opacity 0.4s;
}

.card:hover::before {
  opacity: 1;
}

.card:hover {
  border-color: rgba(255, 255, 255, 0.12);
  transform: translateY(-2px);
  box-shadow: 0 8px 40px rgba(0, 0, 0, 0.4);
}

.card.available:hover {
  border-color: var(--accent);
  box-shadow:
    0 8px 40px rgba(0, 0, 0, 0.5),
    0 0 80px var(--accent)10;
}

button.card {
  width: 100%;
  cursor: default;
  opacity: 0.55;
}

button.card:hover {
  transform: none;
  border-color: rgba(255, 255, 255, 0.06);
  box-shadow: none;
}

/* ── Card Icon ── */
.card-icon {
  flex-shrink: 0;
  width: 48px;
  height: 48px;
  border-radius: 12px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 1.4rem;
  transition: transform 0.35s cubic-bezier(0.25, 0.1, 0.25, 1);
}

.available:hover .card-icon {
  transform: scale(1.1);
}

/* ── Card Body ── */
.card-body {
  flex: 1;
  min-width: 0;
}

.card-header {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  margin-bottom: 0.4rem;
}

.card-title {
  font-size: 1.1rem;
  font-weight: 600;
  color: #e0e0e8;
  margin: 0;
  white-space: nowrap;
}

.badge {
  font-size: 0.7rem;
  font-weight: 500;
  padding: 2px 10px;
  border-radius: 100px;
  letter-spacing: 0.02em;
}

.badge-on {
  background: #7c5cfc18;
  color: #a78bfa;
  border: 1px solid #7c5cfc33;
}

.badge-off {
  background: rgba(255,255,255,0.04);
  color: #5a5a6e;
  border: 1px solid rgba(255,255,255,0.06);
}

.card-desc {
  font-size: 0.9rem;
  color: #6b6b80;
  line-height: 1.55;
  margin: 0;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

/* ── Card Arrow ── */
.card-arrow {
  flex-shrink: 0;
  color: #444;
  font-size: 1.1rem;
  transition: all 0.35s;
  align-self: center;
}

.available:hover .card-arrow {
  color: var(--accent);
  transform: translateX(4px);
}

/* ── Footer ── */
.footer {
  color: #3a3a4a;
  font-size: 0.8rem;
  letter-spacing: 0.04em;
  animation: fade-up 0.8s ease-out 0.55s both;
}

/* ── Animations ── */
@keyframes fade-up {
  from {
    opacity: 0;
    transform: translateY(24px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}
</style>
