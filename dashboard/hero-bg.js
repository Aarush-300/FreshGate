(() => {
  const motion = window.matchMedia('(prefers-reduced-motion: reduce)');
  const root = document.documentElement;
  const benchmarkLink = document.getElementById('hero-benchmarks');
  benchmarkLink?.addEventListener('click', () => document.querySelector('.tab-btn[data-tab="tab-benchmark"]')?.click());

  // Live Gateway Demo typing simulation
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
      claim.textContent = sample;
      status.textContent = 'STALE';
      status.classList.add('is-stale');
      result.classList.add('is-visible');
    } else {
      (async function demoLoop() {
        while (true) {
          status.textContent = 'CHECKING\u2026';
          status.classList.remove('is-stale');
          claim.textContent = '';
          result.classList.remove('is-visible');
          for (let i = 0; i < sample.length; i++) {
            claim.textContent = sample.slice(0, i + 1);
            await pause(38);
          }
          await pause(520);
          status.textContent = 'STALE';
          status.classList.add('is-stale');
          await pause(220);
          result.classList.add('is-visible');
          await pause(3000);
          await pause(600);
        }
      })();
    }
  }

  // Interactive Live Telemetry Waveform Range Selector
  const timeBtns = document.querySelectorAll('.time-filter-btn');
  const waveformArea = document.getElementById('waveform-area');
  const waveformLine = document.getElementById('waveform-line');

  const wavePaths = {
    '1h': {
      line: 'M 0 170 Q 70 140, 140 160 T 280 120 T 420 145 T 560 100 T 700 130 T 840 95 T 980 115 T 1100 80',
      area: 'M 0 170 Q 70 140, 140 160 T 280 120 T 420 145 T 560 100 T 700 130 T 840 95 T 980 115 T 1100 80 L 1100 240 L 0 240 Z'
    },
    '24h': {
      line: 'M 0 160 Q 90 120, 180 145 T 360 110 T 540 135 T 720 90 T 900 125 T 1100 85',
      area: 'M 0 160 Q 90 120, 180 145 T 360 110 T 540 135 T 720 90 T 900 125 T 1100 85 L 1100 240 L 0 240 Z'
    },
    '7d': {
      line: 'M 0 150 Q 110 95, 220 135 T 440 90 T 660 140 T 880 85 T 1100 95',
      area: 'M 0 150 Q 110 95, 220 135 T 440 90 T 660 140 T 880 85 T 1100 95 L 1100 240 L 0 240 Z'
    },
    '1m': {
      line: 'M 0 140 Q 140 85, 280 120 T 560 80 T 840 110 T 1100 70',
      area: 'M 0 140 Q 140 85, 280 120 T 560 80 T 840 110 T 1100 70 L 1100 240 L 0 240 Z'
    },
    'all': {
      line: 'M 0 180 Q 160 110, 320 130 T 640 70 T 960 90 T 1100 65',
      area: 'M 0 180 Q 160 110, 320 130 T 640 70 T 960 90 T 1100 65 L 1100 240 L 0 240 Z'
    }
  };

  timeBtns.forEach(btn => {
    btn.addEventListener('click', () => {
      timeBtns.forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      const range = btn.dataset.time || '24h';
      const pathData = wavePaths[range] || wavePaths['24h'];
      if (waveformLine && pathData.line) {
        waveformLine.setAttribute('d', pathData.line);
      }
      if (waveformArea && pathData.area) {
        waveformArea.setAttribute('d', pathData.area);
      }
    });
  });
})();
