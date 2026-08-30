<template>
  <div class="games-page">
    <section class="games-banner">
      <div><div class="section-kicker">RECREATION MODULE / 05</div><h2>小游戏</h2><p>轻量、独立、随时可以启动的休闲模块。</p></div>
      <div class="coming-code">ONLINE</div>
    </section>
    <section class="games-grid">
      <RouterLink to="/games/colony-idle" class="game-card">
        <div class="game-card-mark">CI</div>
        <h3>殖民地放置</h3>
        <p>资源采集与设施经营的放置小游戏。</p>
        <span class="game-card-go">进入 ↗</span>
      </RouterLink>
      <RouterLink to="/games/voxel4x" class="game-card">
        <div class="game-card-mark">VX</div>
        <h3>体素四叉</h3>
        <p>程序化地形生成的体素沙盒。</p>
        <span class="game-card-go">进入 ↗</span>
      </RouterLink>
    </section>
  </div>
</template>

<style scoped>
.games-page { max-width: 1080px; margin: 0 auto; padding-top: 34px; }.games-banner { display: flex; align-items: end; justify-content: space-between; padding: 28px 0 30px; border-bottom: 1px solid var(--line); }.section-kicker { color: var(--accent); font: 10px Consolas, monospace; letter-spacing: .18em; }.games-banner h2 { margin: 12px 0 8px; font-size: 34px; letter-spacing: -.03em; }.games-banner p { margin: 0; color: var(--muted); font-size: 13px; }.coming-code { color: var(--amber); text-align: right; font: 11px/1.6 Consolas, monospace; letter-spacing: .14em; }.games-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 18px; padding: 30px 0; border-bottom: 1px solid var(--line); }.game-card { display: flex; flex-direction: column; gap: 10px; padding: 22px; border: 1px solid var(--line); border-radius: 12px; background: var(--panel-soft); color: var(--text); text-decoration: none; transition: border-color .2s, transform .2s, box-shadow .2s; }.game-card:hover { border-color: color-mix(in srgb, var(--accent) 55%, transparent); transform: translateY(-2px); box-shadow: 0 0 24px color-mix(in srgb, var(--accent) 18%, transparent); }.game-card-mark { width: 42px; height: 42px; display: flex; align-items: center; justify-content: center; border-radius: 9px; background: color-mix(in srgb, var(--accent) 14%, transparent); color: var(--accent); font: 700 15px Consolas, monospace; letter-spacing: .04em; }.game-card h3 { margin: 4px 0 0; font-size: 17px; }.game-card p { margin: 0; color: var(--muted); font-size: 12px; line-height: 1.5; }.game-card-go { margin-top: 6px; color: var(--accent); font: 11px Consolas, monospace; letter-spacing: .12em; }
</style>
