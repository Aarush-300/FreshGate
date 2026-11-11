(() => {
  const motion = window.matchMedia('(prefers-reduced-motion: reduce)');
  const root = document.documentElement;
  const benchmarkLink = document.getElementById('hero-benchmarks');
  benchmarkLink?.addEventListener('click', () => document.querySelector('.tab-btn[data-tab="tab-benchmark"]')?.click());

  const claim = document.querySelector('[data-demo-claim]');
  const result = document.querySelector('[data-demo-result]');
  const status = document.querySelector('[data-demo-status]');
  if (claim && result && status) {
    const sample = 'Python 2.7 is the latest version';
    let resume = null;
    const shouldPause = () => motion.matches || document.hidden || root.dataset.motionPaused === 'true';
    const pause = async ms => {
      let remaining = ms;
      while (remaining > 0) {
        if (shouldPause()) await new Promise(resolve => { resume = resolve; });
        const start = performance.now();
        await new Promise(resolve => window.setTimeout(resolve, Math.min(remaining, 40)));
        if (!shouldPause()) remaining -= performance.now() - start;
      }
    };
    const resumeDemo = () => { if (!shouldPause() && resume) { const next = resume; resume = null; next(); } };
    document.addEventListener('freshgate:motionchange', resumeDemo);
    document.addEventListener('visibilitychange', resumeDemo);
    motion.addEventListener?.('change', resumeDemo);
    const reduced = motion.matches;
    if (reduced) {
      claim.textContent = sample; status.textContent = 'STALE'; status.classList.add('is-stale'); result.classList.add('is-visible');
    } else {
      (async function demoLoop() {
        while (true) {
          status.textContent = 'CHECKING\u2026'; status.classList.remove('is-stale'); claim.textContent = '';
          for (let i = 0; i < sample.length; i++) { claim.textContent = sample.slice(0, i + 1); await pause(38); }
          await pause(520); status.textContent = 'STALE'; status.classList.add('is-stale');
          await pause(220); result.classList.add('is-visible'); await pause(3000); await pause(600);
        }
      })();
    }
  }
})();


