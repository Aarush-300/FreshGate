// FreshGate Production Dashboard Controller (Phase 7)
// Pure DOM manipulation with ZERO innerHTML to guarantee XSS prevention

document.addEventListener('DOMContentLoaded', () => {
  setupTabs();
  setupVerificationForm();
  setupMetricsPolling();
  setupBenchmarkView();
  setupAuditView();
});

// --- Tab Switching ---
function setupTabs() {
  const tabBtns = document.querySelectorAll('.tab-btn');
  const tabContents = document.querySelectorAll('.tab-content');

  tabBtns.forEach(btn => {
    btn.addEventListener('click', () => {
      const targetId = btn.getAttribute('data-tab');
      tabBtns.forEach(b => b.classList.remove('active'));
      tabContents.forEach(c => c.classList.remove('active'));

      btn.classList.add('active');
      const targetContent = document.getElementById(targetId);
      if (targetContent) {
        targetContent.classList.add('active');
      }
    });
  });
}

// --- Verification Console ---
function setupVerificationForm() {
  const form = document.getElementById('verify-form');
  const input = document.getElementById('verify-input');
  const resultsContainer = document.getElementById('verify-results');
  const submitBtn = document.getElementById('verify-submit-btn');

  if (!form) return;

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const text = input.value.trim();
    if (!text) return;

    submitBtn.disabled = true;
    submitBtn.textContent = 'Verifying...';
    clearElement(resultsContainer);

    const loadingP = document.createElement('p');
    loadingP.style.color = '#9ca3af';
    loadingP.textContent = 'Extracting claims and verifying with live web evidence...';
    resultsContainer.appendChild(loadingP);

    try {
      const res = await fetch('/verify', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ answer: text })
      });

      if (!res.ok) {
        throw new Error(`Verification failed: HTTP ${res.status}`);
      }

      const data = await res.json();
      renderVerificationResults(data, resultsContainer);
    } catch (err) {
      clearElement(resultsContainer);
      const errBox = document.createElement('div');
      errBox.style.color = '#ef4444';
      errBox.style.padding = '12px';
      errBox.style.background = 'rgba(239, 68, 68, 0.1)';
      errBox.style.borderRadius = '6px';
      errBox.textContent = String(err.message || err);
      resultsContainer.appendChild(errBox);
    } finally {
      submitBtn.disabled = false;
      submitBtn.textContent = 'Verify Live Answer';
    }
  });
}

function renderVerificationResults(data, container) {
  clearElement(container);

  const summary = data.summary || {};
  const claims = data.claims || [];

  // Summary Card
  const summaryBox = document.createElement('div');
  summaryBox.className = 'claim-card';
  summaryBox.style.marginBottom = '16px';
  summaryBox.style.borderLeft = summary.stale > 0 ? '4px solid #ef4444' : '4px solid #10b981';

  const title = document.createElement('h3');
  title.style.fontSize = '1.05rem';
  title.style.marginBottom = '6px';
  title.textContent = `Verification Complete: Freshness Score ${summary.avg_score || 0}/100`;
  summaryBox.appendChild(title);

  const statsP = document.createElement('p');
  statsP.style.color = '#9ca3af';
  statsP.style.fontSize = '0.9rem';
  statsP.textContent = `Total Claims: ${summary.total || 0} | Checked: ${summary.checked || 0} | Stale: ${summary.stale || 0}`;
  summaryBox.appendChild(statsP);

  container.appendChild(summaryBox);

  if (claims.length === 0) {
    const emptyP = document.createElement('p');
    emptyP.style.color = '#9ca3af';
    emptyP.textContent = 'No volatile factual claims found. Response passed through as stable.';
    container.appendChild(emptyP);
    return;
  }

  // Claim Cards
  claims.forEach((c) => {
    const card = document.createElement('div');
    card.className = 'claim-card';

    const header = document.createElement('div');
    header.className = 'claim-header';

    const typeSpan = document.createElement('span');
    typeSpan.style.color = '#9ca3af';
    typeSpan.style.fontSize = '0.8rem';
    typeSpan.style.fontWeight = '600';
    typeSpan.textContent = `[${(c.type || 'unknown').toUpperCase()}]`;
    header.appendChild(typeSpan);

    const badge = document.createElement('span');
    badge.className = `badge badge-${c.verdict || 'unverifiable'}`;
    badge.textContent = (c.verdict || 'unverifiable').replace('_', ' ');
    header.appendChild(badge);

    card.appendChild(header);

    const claimText = document.createElement('p');
    claimText.style.fontWeight = '600';
    claimText.style.fontSize = '0.95rem';
    claimText.style.marginBottom = '8px';
    claimText.textContent = c.text;
    card.appendChild(claimText);

    if (c.explanation) {
      const exp = document.createElement('p');
      exp.style.color = '#9ca3af';
      exp.style.fontSize = '0.85rem';
      exp.style.marginBottom = '8px';
      exp.textContent = c.explanation;
      card.appendChild(exp);
    }

    if (c.correction) {
      const corrBox = document.createElement('div');
      corrBox.style.background = 'rgba(239, 68, 68, 0.1)';
      corrBox.style.padding = '8px 12px';
      corrBox.style.borderRadius = '6px';
      corrBox.style.marginBottom = '8px';
      corrBox.style.fontSize = '0.85rem';
      corrBox.style.color = '#f87171';
      corrBox.textContent = `Correction: ${c.correction}`;
      card.appendChild(corrBox);
    }

    // Sources list
    if (c.sources && c.sources.length > 0) {
      const sourcesTitle = document.createElement('div');
      sourcesTitle.style.fontSize = '0.8rem';
      sourcesTitle.style.color = '#9ca3af';
      sourcesTitle.style.fontWeight = '600';
      sourcesTitle.style.marginTop = '8px';
      sourcesTitle.textContent = 'Evidence & Citations:';
      card.appendChild(sourcesTitle);

      c.sources.forEach(s => {
        const sItem = document.createElement('div');
        sItem.className = 'source-item';

        const link = document.createElement('a');
        link.href = s.url;
        link.target = '_blank';
        link.rel = 'noopener noreferrer';
        link.textContent = s.url;
        sItem.appendChild(link);

        const snip = document.createElement('p');
        snip.style.color = '#94a3b8';
        snip.style.marginTop = '4px';
        snip.textContent = s.snippet;
        sItem.appendChild(snip);

        card.appendChild(sItem);
      });
    }

    container.appendChild(card);
  });
}

// --- Operational Metrics Polling ---
function setupMetricsPolling() {
  const fetchAndUpdate = async () => {
    try {
      const [depRes, readyRes] = await Promise.all([
        fetch('/api/deployment/status'),
        fetch('/health/ready')
      ]);

      if (depRes.ok) {
        const depData = await depRes.json();
        const modeEl = document.getElementById('stat-mode');
        const cbEl = document.getElementById('stat-cb');
        if (modeEl) modeEl.textContent = (depData.mode || 'enforce').toUpperCase();
        if (cbEl) cbEl.textContent = (depData.circuit_breaker?.state || 'closed').toUpperCase();
      }

      if (readyRes.ok) {
        const readyData = await readyRes.json();
        const dbEl = document.getElementById('stat-db');
        if (dbEl) dbEl.textContent = (readyData.database || 'connected').toUpperCase();
      }
    } catch (_) {}
  };

  fetchAndUpdate();
  setInterval(fetchAndUpdate, 5000);
}

// --- Benchmark & Reliability View ---
function setupBenchmarkView() {
  const btn = document.getElementById('load-benchmark-btn');
  const container = document.getElementById('benchmark-content');

  const loadData = async () => {
    if (!container) return;
    try {
      const res = await fetch('/api/benchmark');
      if (!res.ok) return;
      const data = await res.json();
      clearElement(container);

      const pre = document.createElement('pre');
      pre.style.whiteSpace = 'pre-wrap';
      pre.style.fontFamily = 'monospace';
      pre.style.fontSize = '0.85rem';
      pre.style.lineHeight = '1.5';
      pre.style.color = '#e2e8f0';
      pre.textContent = data.results_md || 'No benchmark report generated yet.';
      container.appendChild(pre);
    } catch (e) {
      container.textContent = 'Failed to load benchmark results.';
    }
  };

  if (btn) btn.addEventListener('click', loadData);
  loadData();
}

// --- Historical Audit Inspector ---
function setupAuditView() {
  const btn = document.getElementById('refresh-audits-btn');
  const exportBtn = document.getElementById('export-audits-btn');
  const container = document.getElementById('audits-table-body');
  const adminKeyInput = document.getElementById('admin-key-input');

  const fetchAudits = async () => {
    if (!container) return;
    const adminKey = adminKeyInput ? adminKeyInput.value.trim() : '';
    clearElement(container);

    try {
      const headers = adminKey ? { 'X-Admin-Key': adminKey } : {};
      const res = await fetch('/api/audits/recent?limit=25', { headers });

      if (res.status === 401 || res.status === 403) {
        const tr = document.createElement('tr');
        const td = document.createElement('td');
        td.colSpan = 6;
        td.style.color = '#f87171';
        td.textContent = 'Admin Key required to view security audit logs. Enter key above and click Refresh.';
        tr.appendChild(td);
        container.appendChild(tr);
        return;
      }

      if (!res.ok) throw new Error(`HTTP ${res.status}`);

      const data = await res.json();
      const audits = data.audits || [];

      if (audits.length === 0) {
        const tr = document.createElement('tr');
        const td = document.createElement('td');
        td.colSpan = 6;
        td.textContent = 'No audit records logged yet.';
        tr.appendChild(td);
        container.appendChild(tr);
        return;
      }

      window._recentAudits = audits;

      audits.forEach(a => {
        const tr = document.createElement('tr');

        const tdTime = document.createElement('td');
        tdTime.textContent = new Date(a.created_at * 1000).toLocaleTimeString();
        tr.appendChild(tdTime);

        const tdCorr = document.createElement('td');
        tdCorr.textContent = (a.correlation_id || '').substring(0, 12);
        tr.appendChild(tdCorr);

        const tdText = document.createElement('td');
        tdText.textContent = (a.input_text || '').substring(0, 60) + '...';
        tr.appendChild(tdText);

        const tdScore = document.createElement('td');
        tdScore.textContent = `${a.avg_score}/100`;
        tr.appendChild(tdScore);

        const tdStale = document.createElement('td');
        tdStale.textContent = a.stale_claims;
        tdStale.style.color = a.stale_claims > 0 ? '#f87171' : '#34d399';
        tr.appendChild(tdStale);

        const tdLatency = document.createElement('td');
        tdLatency.textContent = `${a.latency_ms} ms`;
        tr.appendChild(tdLatency);

        container.appendChild(tr);
      });
    } catch (err) {
      const tr = document.createElement('tr');
      const td = document.createElement('td');
      td.colSpan = 6;
      td.textContent = `Error loading audits: ${err.message}`;
      tr.appendChild(td);
      container.appendChild(tr);
    }
  };

  if (btn) btn.addEventListener('click', fetchAudits);

  if (exportBtn) {
    exportBtn.addEventListener('click', () => {
      const audits = window._recentAudits || [];
      const blob = new Blob([JSON.stringify(audits, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `freshgate_audit_export_${Date.now()}.json`;
      a.click();
      URL.revokeObjectURL(url);
    });
  }

  fetchAudits();
}

// Utility: Safe DOM Clear
function clearElement(el) {
  while (el && el.firstChild) {
    el.removeChild(el.firstChild);
  }
}
