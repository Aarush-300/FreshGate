// One animation engine drives the page ambience and all tab banners.
(() => {
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  const touch = matchMedia('(pointer: coarse)').matches;
  const banners = [...document.querySelectorAll('.banner-canvas, .hero-canvas')].map(canvas => ({ canvas, ctx: canvas.getContext('2d'), mode: canvas.dataset.mode || 'verify', w: 0, h: 0, visible: false, px: -1000, py: -1000, sx: -1000, sy: -1000, pulse: Math.random() * 2000 }));
  let raf = 0, started = performance.now(), mode = 'verify', changedAt = 0;
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));

  function size(canvas, ctx, owner) {
    const rect = owner ? owner.getBoundingClientRect() : { width: innerWidth, height: innerHeight };
    const scale = Math.min(devicePixelRatio || 1, 2);
    canvas.width = Math.max(1, Math.round(rect.width * scale)); canvas.height = Math.max(1, Math.round(rect.height * scale));
    canvas.style.width = `${rect.width}px`; canvas.style.height = `${rect.height}px`; ctx.setTransform(scale, 0, 0, scale, 0, 0);
    return [rect.width, rect.height];
  }
  function resize() {
    for (const b of banners) [b.w, b.h] = size(b.canvas, b.ctx, b.canvas.parentElement);
    draw(performance.now(), true);
  }
  function drawBanner(b, t) {
    const { ctx: c, w, h, mode: m } = b; if (!c || !w || !h) return;
    c.clearRect(0, 0, w, h);
    const light = document.documentElement.dataset.theme === 'light';
    const fade = c.createLinearGradient(0, 0, w, 0); fade.addColorStop(0, light ? 'rgba(98,82,188,.015)' : 'rgba(185,164,255,.02)'); fade.addColorStop(.42, light ? 'rgba(98,82,188,.10)' : 'rgba(185,164,255,.18)'); fade.addColorStop(1, light ? 'rgba(98,82,188,.55)' : 'rgba(217,204,255,.96)');
    const seconds = (t - started) / 1000;
    if (m === 'verify') {
      const bg = c.createLinearGradient(0, 0, w, h); bg.addColorStop(0, light ? '#f5f3fb' : '#07070d'); bg.addColorStop(.55, light ? 'rgba(180,169,218,.24)' : 'rgba(42,20,88,.32)'); bg.addColorStop(1, light ? 'rgba(98,82,188,.10)' : 'rgba(124,77,255,.55)'); c.fillStyle = bg; c.fillRect(0, 0, w, h);
      let gx = w * (.72 + .08 * Math.sin(t * .00068)), gy = h * (1 + .06 * Math.cos(t * .00073));
      let glow = c.createRadialGradient(gx, gy, 0, gx, gy, w * .62); glow.addColorStop(0, light ? 'rgba(98,82,188,.16)' : 'rgba(124,77,255,.48)'); glow.addColorStop(.48, light ? 'rgba(119,103,183,.10)' : 'rgba(83,42,166,.24)'); glow.addColorStop(1, light ? 'rgba(98,82,188,0)' : 'rgba(42,20,88,0)'); c.fillStyle = glow; c.fillRect(0, 0, w, h);
      gx = w * (.38 + .1 * Math.cos(t * .00058)); gy = h * (.3 + .08 * Math.sin(t * .0008)); glow = c.createRadialGradient(gx, gy, 0, gx, gy, w * .42); glow.addColorStop(0, light ? 'rgba(118,104,183,.12)' : 'rgba(70,35,150,.32)'); glow.addColorStop(1, light ? 'rgba(98,82,188,0)' : 'rgba(42,20,88,0)'); c.fillStyle = glow; c.fillRect(0, 0, w, h);
      const gap = 14, beamPhase = t % 6500, beamOn = beamPhase < 2200, beamPos = beamOn ? -.08 + 1.16 * beamPhase / 2200 : -10, diagonal = Math.hypot(w, h);
      for (let y = gap / 2; y < h; y += gap) for (let x = gap / 2; x < w; x += gap) {
        const v = (Math.sin(x * .011 + t * .00065) + Math.sin(y * .014 - t * .00078) + Math.sin((x + y) * .008 + t * .00055) + Math.sin((x - y) * .005 - t * .00072) + 4) / 8;
        const fade = clamp((x / w - .25) / .4, 0, 1), side = fade * fade * (3 - 2 * fade), dist = touch ? 1000 : Math.hypot(x - b.sx, y - b.sy), pointer = clamp(1 - dist / 140, 0, 1), beam = beamOn && Math.abs((x / w + 1 - y / h) / 2 - beamPos) * diagonal < 60;
        const radius = (v * v * 2.15 * (.08 + .92 * side) + pointer * 1.6) * (1 + pointer * .8), alpha = clamp((.09 + v * .36) * (.035 + .965 * side) * (1 + pointer) + (beam ? .2 : 0), 0, .85);
        if (radius < .3 || alpha < .01) continue; c.globalAlpha = alpha; c.fillStyle = beam ? (light ? '#51449d' : '#d9ccff') : (light ? '#7669b8' : '#beaaff'); c.beginPath(); c.arc(x, y, radius, 0, Math.PI * 2); c.fill();
      }
    } else if (m === 'system') {
      const gap = 16, ox = (seconds * 2.2) % gap, oy = (seconds * .8) % gap;
      const pulseTime = t + b.pulse, pulsePhase = (pulseTime % 1800) / 1800, pulseId = Math.floor(pulseTime / 1800);
      const randX = (Math.sin(pulseId * 127.1 + 3) * 43758.5453) % 1, randY = (Math.sin(pulseId * 269.5 + 7) * 24634.6345) % 1;
      const ringR = pulsePhase * Math.max(w, h) * .62, rx = w * (.25 + .7 * Math.abs(randX)), ry = h * (.2 + .6 * Math.abs(randY));
      c.fillStyle = fade;
      for (let y = -gap; y < h + gap; y += gap) for (let x = -gap; x < w + gap; x += gap) {
        const xx = x + ox, yy = y + oy, d = Math.hypot(xx - rx, yy - ry), near = Math.abs(d - ringR), ptr = touch ? 0 : Math.max(0, 1 - Math.hypot(xx - b.sx, yy - b.sy) / 140);
        const boost = clamp(1 - near / 15, 0, 1), radius = 1 + boost * 1.8 + ptr * 2.5;
        c.globalAlpha = .45 + boost * .55 + ptr * .5; c.beginPath(); c.arc(xx, yy, radius, 0, Math.PI * 2); c.fill();
      }
      c.globalAlpha = .65; c.strokeStyle = light ? '#6252bc' : '#d9ccff'; c.lineWidth = 1; c.beginPath(); c.arc(rx, ry, ringR, 0, Math.PI * 2); c.stroke();
      const beamX = ((seconds % 8) / 8) * (w + 120) - 60; c.globalAlpha = .35; c.fillStyle = light ? '#6252bc' : '#d9ccff'; c.fillRect(beamX, 0, 2, h);
    } else if (m === 'benchmarks') {
      const cols = 40, gap = w / (cols + 1); c.fillStyle = fade;
      for (let i = 0; i < cols; i++) {
        const x = gap * (i + 1), wave = (Math.sin(i * .52 + seconds * 1.05) + Math.sin(i * .23 - seconds * .72) + 2) / 4;
        const count = 2 + Math.round(wave * 8), ptr = touch ? 0 : Math.max(0, 1 - Math.hypot(x - b.sx, h * .5 - b.sy) / 140);
        for (let j = 0; j < count; j++) { const y = h - 26 - j * 13 - wave * 20; c.globalAlpha = .42 + wave * .46 + ptr * .5; c.fillStyle = wave > .82 ? (light ? '#51449d' : '#d9ccff') : fade; c.beginPath(); c.arc(x, y, 2 + ptr * 1.2, 0, Math.PI * 2); c.fill(); }
      }
      const sweep = ((seconds % 9) / 9) * (w + 180) - 90, beam = c.createLinearGradient(sweep - 55, 0, sweep + 55, 0); beam.addColorStop(0, 'rgba(217,204,255,0)'); beam.addColorStop(.5, 'rgba(217,204,255,.34)'); beam.addColorStop(1, 'rgba(217,204,255,0)'); c.globalAlpha = .8; c.fillStyle = beam; c.fillRect(sweep - 55, 0, 110, h);
    } else {
      c.strokeStyle = light ? 'rgba(76,66,139,.25)' : 'rgba(217,204,255,.3)'; c.lineWidth = 1; c.globalAlpha = .75;
      for (let y = 14; y < h; y += 28) { c.beginPath(); c.moveTo(0, y); c.lineTo(w, y); c.stroke(); }
      const phase = (seconds % 5) / 5, bandY = phase * h, band = c.createLinearGradient(0, bandY - 35, 0, bandY + 35); band.addColorStop(0, 'rgba(217,204,255,0)'); band.addColorStop(.5, 'rgba(217,204,255,.22)'); band.addColorStop(1, 'rgba(217,204,255,0)'); c.fillStyle = band; c.fillRect(0, bandY - 35, w, 70);
      for (let i = 0; i < 10; i++) { const y = 28 + i * 18, x = ((seconds * 38 + i * 107) % (w + 80)) - 40, ptr = touch ? 0 : Math.max(0, 1 - Math.hypot(x - b.sx, y - b.sy) / 140); c.globalAlpha = .5 + ptr * .5; c.fillStyle = light ? '#6252bc' : '#d9ccff'; c.fillRect(x, y - 3, 8 + (i % 3) * 3, 6); if (i < 9) { c.globalAlpha = .25; c.beginPath(); c.moveTo(x + 12, y); c.lineTo(x + 22, y + 18); c.stroke(); } }
    }
    const dim = c.createLinearGradient(0, 0, w, 0); dim.addColorStop(0, 'rgba(0,0,0,.88)'); dim.addColorStop(.46, 'rgba(0,0,0,.62)'); dim.addColorStop(.72, 'rgba(0,0,0,.08)'); dim.addColorStop(1, 'rgba(0,0,0,0)');
    c.globalCompositeOperation = 'destination-out'; c.globalAlpha = 1; c.fillStyle = dim; c.fillRect(0, 0, w, h); c.globalCompositeOperation = 'source-over'; c.globalAlpha = 1;
  }
  function draw(t, force = false) {
    if (document.hidden) return;
    if (!force && (reduced.matches || document.documentElement.dataset.motionPaused === 'true')) { raf = 0; return; }
    const blend = changedAt ? clamp((t - changedAt) / 600, 0, 1) : 1;
    for (const b of banners) if (b.visible && document.getElementById(b.mode === 'verify' ? 'tab-verify' : `tab-${b.mode === 'system' ? 'metrics' : b.mode === 'benchmarks' ? 'benchmark' : 'audits'}`)?.classList.contains('active')) {
      b.sx += (b.px - b.sx) * .12; b.sy += (b.py - b.sy) * .12; drawBanner(b, reduced.matches ? started + 8000 : t); b.canvas.style.opacity = `${blend}`;
    }
    if (!force && !reduced.matches && document.documentElement.dataset.motionPaused !== 'true') raf = requestAnimationFrame(draw);
    else if (!force) raf = 0;
  }
  function start() { if (raf) cancelAnimationFrame(raf); raf = 0; draw(performance.now(), true); if (!reduced.matches && document.documentElement.dataset.motionPaused !== 'true' && !document.hidden) raf = requestAnimationFrame(draw); }
  banners.forEach(b => {
    new ResizeObserver(() => { [b.w, b.h] = size(b.canvas, b.ctx, b.canvas.parentElement); start(); }).observe(b.canvas.parentElement);
    new IntersectionObserver(([e]) => { b.visible = e.isIntersecting; start(); }, { threshold: .01 }).observe(b.canvas.parentElement);
    b.canvas.parentElement.addEventListener('pointermove', e => { const r = b.canvas.getBoundingClientRect(); b.px = e.clientX - r.left; b.py = e.clientY - r.top; });
  });
  document.addEventListener('visibilitychange', start); document.addEventListener('freshgate:motionchange', start); reduced.addEventListener?.('change', start); addEventListener('resize', resize);
  new MutationObserver(() => { const next = document.querySelector('.tab-content.active')?.dataset.ambient || 'verify'; if (next !== mode) { mode = next; changedAt = performance.now(); start(); } }).observe(document.body, { attributes: true, attributeFilter: ['data-ambient'] });
  new MutationObserver(start).observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme', 'data-motion-paused'] });
  banners.forEach(b => b.canvas.addEventListener('pointerenter', () => { b.pulse = Math.random() * 1800; }));
  resize(); start();
})();
