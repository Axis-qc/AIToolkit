<template>
  <div class="games-page">
    <section class="games-banner">
      <div><div class="section-kicker">RECREATION MODULE / 05</div><h2>小游戏</h2><p>轻量、独立、随时可以启动的休闲模块。</p></div>
      <div class="coming-code">COMING<br />SOON</div>
    </section>
    <section class="games-empty">
      <div class="empty-mark"><span></span><span></span><span></span></div>
      <h3>模块尚未上线</h3>
      <p>游戏入口将在后续版本接入。</p>
      <div class="empty-progress"><i></i></div>
      <small>BUILD QUEUE / 01</small>
    </section>
  </div>
</template>

<style scoped>
.games-page { max-width: 1080px; margin: 0 auto; padding-top: 34px; }.games-banner { display: flex; align-items: end; justify-content: space-between; padding: 28px 0 30px; border-bottom: 1px solid var(--line); }.section-kicker { color: var(--accent); font: 10px Consolas, monospace; letter-spacing: .18em; }.games-banner h2 { margin: 12px 0 8px; font-size: 34px; letter-spacing: -.03em; }.games-banner p { margin: 0; color: var(--muted); font-size: 13px; }.coming-code { color: var(--amber); text-align: right; font: 11px/1.6 Consolas, monospace; letter-spacing: .14em; }.games-empty { min-height: 390px; display: flex; flex-direction: column; align-items: center; justify-content: center; border-bottom: 1px solid var(--line); }.empty-mark { display: flex; align-items: end; gap: 5px; height: 34px; margin-bottom: 25px; }.empty-mark span { display: block; width: 7px; background: var(--accent); opacity: .55; }.empty-mark span:nth-child(1) { height: 14px; }.empty-mark span:nth-child(2) { height: 26px; opacity: .8; }.empty-mark span:nth-child(3) { height: 34px; box-shadow: 0 0 20px color-mix(in srgb, var(--accent) 50%, transparent); }.games-empty h3 { margin: 0 0 8px; font-size: 18px; }.games-empty p { margin: 0; color: var(--muted); font-size: 12px; }.empty-progress { width: 190px; height: 3px; margin: 27px 0 10px; overflow: hidden; background: color-mix(in srgb, var(--accent) 12%, transparent); }.empty-progress i { display: block; width: 28%; height: 100%; background: var(--accent); box-shadow: 0 0 14px var(--accent); }.games-empty small { color: var(--dim); font: 10px Consolas, monospace; letter-spacing: .12em; }
</style>
