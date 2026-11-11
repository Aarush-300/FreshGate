(() => {
  const root = document.documentElement;
  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
  const themeKey = 'freshgate.theme';
  const motionKey = 'freshgate.motion';
  const read = key => { try { return localStorage.getItem(key); } catch { return null; } };
  const write = (key, value) => { try { localStorage.setItem(key, value); } catch { /* Preference remains active for this page. */ } };

  const savedTheme = read(themeKey);
  root.dataset.theme = savedTheme === 'light' ? 'light' : 'dark';

  let savedMotion = read(motionKey);
  const isPaused = () => reducedMotion.matches || savedMotion === 'paused';
  function updateMotionState() {
    const paused = isPaused();
    root.dataset.motionPaused = String(paused);
    document.dispatchEvent(new CustomEvent('freshgate:motionchange', { detail: { paused } }));
    const button = document.getElementById('motion-toggle');
    if (!button) return;
    button.setAttribute('aria-pressed', String(paused));
    button.setAttribute('aria-label', paused ? 'Resume animations' : 'Pause animations');
    button.title = reducedMotion.matches ? 'Animations follow your reduced-motion setting' : (paused ? 'Resume animations' : 'Pause animations');
    button.disabled = reducedMotion.matches;
  }

  function updateThemeState() {
    const light = root.dataset.theme === 'light';
    const button = document.getElementById('theme-toggle');
    const label = document.getElementById('theme-label');
    const meta = document.querySelector('meta[name="theme-color"]');
    if (button) {
      button.setAttribute('aria-pressed', String(light));
      button.setAttribute('aria-label', light ? 'Switch to dark mode' : 'Switch to light mode');
      button.title = light ? 'Switch to dark mode' : 'Switch to light mode';
    }
    if (label) label.textContent = light ? 'Light' : 'Dark';
    if (meta) meta.content = light ? '#f3f4f8' : '#07070d';
  }

  document.addEventListener('DOMContentLoaded', () => {
    updateThemeState();
    updateMotionState();
    document.getElementById('theme-toggle')?.addEventListener('click', () => {
      root.dataset.theme = root.dataset.theme === 'dark' ? 'light' : 'dark';
      write(themeKey, root.dataset.theme);
      updateThemeState();
    });
    document.getElementById('motion-toggle')?.addEventListener('click', () => {
      if (reducedMotion.matches) return;
      savedMotion = isPaused() ? 'running' : 'paused';
      write(motionKey, savedMotion);
      updateMotionState();
    });
    reducedMotion.addEventListener?.('change', updateMotionState);
  });
})();
