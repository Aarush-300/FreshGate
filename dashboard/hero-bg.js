(() => {
  const motion = window.matchMedia('(prefers-reduced-motion: reduce)');
  const benchmarkLink = document.getElementById('hero-benchmarks');
  benchmarkLink?.addEventListener('click', () => document.querySelector('.tab-btn[data-tab="tab-benchmark"]')?.click());

  const revealTargets = document.querySelectorAll('.provider-strip, #verify-workspace, .workspace-note, #tab-metrics > .card, #tab-benchmark > .card, #tab-audits > .card');
  if (motion.matches) revealTargets.forEach(element => element.classList.add('is-revealed'));
  else {
    const revealObserver = new IntersectionObserver(entries => entries.forEach(entry => {
      if (entry.isIntersecting) { entry.target.classList.add('is-revealed'); revealObserver.unobserve(entry.target); }
    }), { threshold: .12 });
    revealTargets.forEach(element => { element.setAttribute('data-reveal', ''); revealObserver.observe(element); });
  }

  const claim = document.querySelector('[data-demo-claim]');
  const result = document.querySelector('[data-demo-result]');
  const status = document.querySelector('[data-demo-status]');
  if (claim && result && status) {
    const sample = 'Python 2.7 is the latest version';
    const pause = ms => new Promise(resolve => window.setTimeout(resolve, ms));
    const reduced = motion.matches;
    if (reduced) {
      claim.textContent = sample; status.textContent = 'STALE'; status.classList.add('is-stale'); result.classList.add('is-visible');
    } else {
      (async function demoLoop() {
        while (true) {
          status.textContent = 'CHECKING\u2026'; status.classList.remove('is-stale'); result.classList.remove('is-visible'); claim.textContent = '';
          for (let i = 0; i < sample.length; i++) { claim.textContent = sample.slice(0, i + 1); await pause(38); }
          await pause(520); status.textContent = 'STALE'; status.classList.add('is-stale');
          await pause(220); result.classList.add('is-visible'); await pause(3000); result.classList.remove('is-visible'); await pause(600);
        }
      })();
    }
  }
})();


