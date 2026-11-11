// FreshGate Production Dashboard Controller (Phase 7)
// Pure DOM manipulation with ZERO innerHTML to guarantee XSS prevention

// Unregister any stale service workers from browser cache on origin
if ('serviceWorker' in navigator) {
  navigator.serviceWorker.getRegistrations().then(registrations => {
    for (const registration of registrations) {
      registration.unregister().catch(() => {});
    }
  }).catch(() => {});
}

document.addEventListener('DOMContentLoaded', () => {
  setupTabs();
  setupVerificationForm();
  setupMetricsPolling();
  setupBenchmarkView();
  setupAuditView();
});

function setStatusTone(element, tone) {
  if (!element) return;
  element.classList.remove('status-positive', 'status-warning', 'status-danger', 'status-accent', 'status-neutral');
  element.classList.add(`status-${tone}`);
}

// --- Tab Switching ---
function setupTabs() {
  const tabBtns = document.querySelectorAll('.tab-btn');
  const tabContents = document.querySelectorAll('.tab-content');
  const nav = document.querySelector('.nav-tabs');
  let switching = false;

  tabBtns.forEach(btn => {
    btn.addEventListener('click', () => {
      if (switching || btn.classList.contains('active')) return;
      const targetId = btn.getAttribute('data-tab');
      switching = true;
      nav?.classList.add('is-switching');
      const current = document.querySelector('.tab-content.active');
      current?.classList.add('is-exiting');
      window.setTimeout(() => {
        tabBtns.forEach(b => b.classList.toggle('active', b === btn));
        tabContents.forEach(c => c.classList.remove('active', 'is-exiting', 'view-enter'));
      const targetContent = document.getElementById(targetId);
      if (targetContent) {
        document.body.dataset.ambient = targetContent.dataset.ambient || 'verify';
        targetContent.classList.add('view-enter');
        targetContent.classList.add('active');
      }
        window.setTimeout(() => { targetContent?.classList.remove('view-enter'); switching = false; }, 500);
        window.setTimeout(() => nav?.classList.remove('is-switching'), 460);
      }, 160);
    });
  });
}

// --- Verification Console ---
function setupVerificationForm() {
  const form = document.getElementById('verify-form');
  const input = document.getElementById('verify-input');
  const resultsContainer = document.getElementById('verify-results');
  const submitBtn = document.getElementById('verify-submit-btn');
  const submitLabel = document.getElementById('verify-button-label');
  const previewBtn = document.getElementById('preview-submit-btn');
  const openaiKey = document.getElementById('openai-api-key');
  const tavilyKey = document.getElementById('tavily-api-key');

  if (!form) return;

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const text = input.value.trim();
    if (!text || !openaiKey.value.trim() || !tavilyKey.value.trim()) {
      form.reportValidity();
      return;
    }

    submitBtn.disabled = true;
    submitLabel.textContent = 'Verifying…';
    clearElement(resultsContainer);

    const loadingP = document.createElement('p');
    loadingP.className = 'result-loading';
    loadingP.textContent = 'Extracting claims and verifying with live web evidence...';
    resultsContainer.appendChild(loadingP);

    try {
      const res = await fetch('/verify', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-OpenAI-API-Key': openaiKey.value.trim(),
          'X-Tavily-API-Key': tavilyKey.value.trim()
        },
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
      errBox.className = 'result-error';
      errBox.textContent = String(err.message || err);
      resultsContainer.appendChild(errBox);
    } finally {
      openaiKey.value = '';
      tavilyKey.value = '';
      submitBtn.disabled = false;
      submitLabel.textContent = 'Verify live';
    }
  });

  previewBtn?.addEventListener('click', async () => {
    const text = input.value.trim();
    if (!text) {
      input.focus();
      input.setCustomValidity('Paste an answer to preview its claims.');
      input.reportValidity();
      input.addEventListener('input', () => input.setCustomValidity(''), { once: true });
      return;
    }
    previewBtn.disabled = true;
    previewBtn.textContent = 'Previewing…';
    clearElement(resultsContainer);
    const loading = document.createElement('p');
    loading.className = 'preview-loading';
    loading.textContent = 'Finding recognizable claim patterns locally…';
    resultsContainer.appendChild(loading);
    try {
      const res = await fetch('/preview', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ answer: text })
      });
      if (!res.ok) throw new Error(`Preview failed: HTTP ${res.status}`);
      renderOfflinePreview(await res.json(), resultsContainer);
    } catch (err) {
      clearElement(resultsContainer);
      const error = document.createElement('p');
      error.className = 'preview-error';
      error.textContent = String(err.message || err);
      resultsContainer.appendChild(error);
    } finally {
      previewBtn.disabled = false;
      previewBtn.textContent = 'Preview offline';
    }
  });
}

function renderOfflinePreview(data, container) {
  clearElement(container);
  const panel = document.createElement('section');
  panel.className = 'offline-preview';
  const heading = document.createElement('div');
  heading.className = 'preview-heading';
  const title = document.createElement('h3');
  title.textContent = 'Offline extraction preview';
  const status = document.createElement('span');
  status.className = 'preview-status';
  status.textContent = data.claims?.length ? `${data.claims.length} CANDIDATE${data.claims.length === 1 ? '' : 'S'}` : 'NO PATTERN MATCH';
  heading.append(title, status);
  panel.appendChild(heading);
  const note = document.createElement('p');
  note.className = 'preview-disclaimer';
  note.textContent = data.message || 'This preview is local and has not verified any claims.';
  panel.appendChild(note);
  for (const claim of data.claims || []) {
    const item = document.createElement('article');
    item.className = 'preview-claim';
    const type = document.createElement('span');
    type.className = 'preview-type';
    type.textContent = (claim.type || 'claim').replace(/_/g, ' ');
    const text = document.createElement('p');
    text.textContent = claim.text;
    const verdict = document.createElement('span');
    verdict.className = 'preview-unverified';
    verdict.textContent = 'NOT LIVE-VERIFIED';
    item.append(type, text, verdict);
    panel.appendChild(item);
  }
  container.appendChild(panel);
}

function renderVerificationResults(data, container) {
  clearElement(container);

  const summary = data.summary || {};
  const claims = data.claims || [];
  const status = summary.status || data.status || 'completed';
  const extractionStatus = summary.extraction_status || (data.extraction ? data.extraction.status : 'success');
  const rawMessage = summary.message || data.error || '';
  const quotaExhausted = /insufficient[_ ]quota|quota exhausted|no credits remaining|credit balance|billing limit/i.test(rawMessage);

  // Summary Card
  const summaryBox = document.createElement('div');
  let tone = 'positive';
  if (status === 'extraction_failed' || status === 'verification_failed' || status === 'external_provider_unavailable' || summary.stale > 0) tone = 'error';
  else if (status === 'no_claims_detected') tone = 'neutral';
  else if (status === 'partially_verified' || status === 'completed_with_warnings' || (summary.avg_score != null && summary.avg_score < 70)) tone = 'warning';
  summaryBox.className = `claim-card result-summary tone-${tone}`;

  // Header row in summary box
  const headerRow = document.createElement('div');
  headerRow.className = 'result-summary-header';

  const title = document.createElement('h3');
  title.className = 'result-title';

  if (summary.avg_score !== null && summary.avg_score !== undefined) {
    title.textContent = `Freshness Score: ${summary.avg_score}/100`;
  } else {
    title.textContent = 'Freshness Score: Unavailable (N/A)';
  }
  headerRow.appendChild(title);

  // Status Badge
  const statusBadge = document.createElement('span');
  statusBadge.className = `badge badge-${status}`;
  statusBadge.textContent = status.replace(/_/g, ' ');
  headerRow.appendChild(statusBadge);

  summaryBox.appendChild(headerRow);

  // Stats paragraph
  const statsP = document.createElement('p');
  statsP.className = 'result-stats';
  statsP.textContent = `Total Claims: ${summary.total || 0} | Checked: ${summary.checked || 0} | Stale: ${summary.stale || 0} | Extraction: ${extractionStatus.replace(/_/g, ' ')}`;
  summaryBox.appendChild(statsP);

  // Informative / Alert Message Banner
  if (summary.message || data.error) {
    const banner = document.createElement('div');
    const msgText = quotaExhausted
      ? 'The configured LLM provider is out of credits. Use “Preview offline” to inspect supported claim patterns without provider calls. Live verification still requires available model and web-search providers.'
      : rawMessage;
    banner.className = 'alert-banner';
    if (quotaExhausted) {
      banner.className += ' alert-banner-warning';
    } else if (status === 'extraction_failed' || status === 'verification_failed' || status === 'external_provider_unavailable') {
      banner.className += ' alert-banner-error';
    } else if (status === 'completed_with_warnings' || status === 'partially_verified') {
      banner.className += ' alert-banner-warning';
    } else {
      banner.className += ' alert-banner-neutral';
    }
    banner.textContent = msgText;
    summaryBox.appendChild(banner);
  }

  container.appendChild(summaryBox);

  // Handle empty claims cases based on status
  if (claims.length === 0) {
    const emptyBox = document.createElement('div');
    emptyBox.className = 'claim-card result-empty';

    const p = document.createElement('p');
    p.className = 'result-empty-copy';

    if (status === 'no_claims_detected') {
      p.textContent = 'No volatile factual claims found. Response passed through as stable (conversational, subjective, or general statements).';
    } else if (status === 'extraction_failed') {
      p.textContent = quotaExhausted
        ? 'No live freshness verdict is available. Preview supported claims offline, then retry live verification when provider credits are available.'
        : 'Claim extraction failed. Unable to verify response freshness.';
      p.classList.add('is-error');
    } else if (status === 'verification_failed') {
      p.textContent = 'Fact verification failed due to engine or external service error.';
      p.classList.add('is-error');
    } else {
      p.textContent = 'No claims were processed.';
    }
    emptyBox.appendChild(p);
    container.appendChild(emptyBox);
    return;
  }

  // Separate claims into Verified vs Unverified / Inconclusive
  const verifiedClaims = claims.filter(c => c.verdict && c.verdict !== 'unverifiable');
  const unverifiableClaims = claims.filter(c => !c.verdict || c.verdict === 'unverifiable');

  function renderClaimCard(c, targetContainer) {
    const card = document.createElement('div');
    card.className = 'claim-card';
    card.classList.add('result-claim-card');

    const header = document.createElement('div');
    header.className = 'claim-header';

    const typeSpan = document.createElement('span');
    typeSpan.className = 'claim-type';
    typeSpan.textContent = `[${(c.category || c.type || 'unknown').toUpperCase()}]`;
    header.appendChild(typeSpan);

    const badgesWrapper = document.createElement('div');
    badgesWrapper.className = 'claim-badges';

    if (c.extraction_method) {
      const extBadge = document.createElement('span');
      extBadge.className = `method-badge extraction-${c.extraction_method === 'heuristic' ? 'heuristic' : 'model'}`;
      extBadge.textContent = `Extracted: ${c.extraction_method.toUpperCase()}`;
      badgesWrapper.appendChild(extBadge);
    }

    if (c.verification_method) {
      const verBadge = document.createElement('span');
      verBadge.className = `method-badge verification-${c.verification_method === 'heuristic_fallback' ? 'heuristic' : 'model'}`;
      verBadge.textContent = `Verified: ${c.verification_method.replace(/_/g, ' ').toUpperCase()}`;
      badgesWrapper.appendChild(verBadge);
    }

    const badge = document.createElement('span');
    const verdict = c.verdict || 'unverifiable';
    badge.className = `badge badge-${verdict}`;
    badge.textContent = verdict.replace(/_/g, ' ');
    badgesWrapper.appendChild(badge);

    header.appendChild(badgesWrapper);

    card.appendChild(header);

    const claimText = document.createElement('p');
    claimText.className = 'claim-text';
    claimText.textContent = c.text;
    card.appendChild(claimText);

    if (c.verdict === 'unverifiable') {
      const unverifiableNote = document.createElement('p');
      unverifiableNote.className = 'claim-note';
      unverifiableNote.textContent = 'No conclusive web evidence was found to confirm or contradict this claim.';
      card.appendChild(unverifiableNote);
    }

    if (c.explanation) {
      const exp = document.createElement('p');
      exp.className = 'claim-explanation';
      exp.textContent = c.explanation;
      card.appendChild(exp);
    }

    if (c.correction) {
      const corrBox = document.createElement('div');
      corrBox.className = 'claim-correction';
      corrBox.textContent = `Correction: ${c.correction}`;
      card.appendChild(corrBox);
    }

    // Sources list
    if (c.sources && c.sources.length > 0) {
      const sourcesTitle = document.createElement('div');
      sourcesTitle.className = 'sources-title';
      sourcesTitle.textContent = 'Evidence & Citations:';
      card.appendChild(sourcesTitle);

      c.sources.forEach(s => {
        const sItem = document.createElement('div');
        sItem.className = 'source-item';

        const link = document.createElement('a');
        let sourceUrl;
        try {
          sourceUrl = new URL(s.url, window.location.origin);
        } catch (_) {
          return;
        }
        if (!['http:', 'https:'].includes(sourceUrl.protocol)) return;
        link.href = sourceUrl.href;
        link.target = '_blank';
        link.rel = 'noopener noreferrer';
        link.textContent = s.url;
        sItem.appendChild(link);

        if (s.snippet) {
          const snip = document.createElement('p');
          snip.className = 'source-snippet';
          snip.textContent = s.snippet;
          sItem.appendChild(snip);
        }

        card.appendChild(sItem);
      });
    }

    targetContainer.appendChild(card);
  }

  if (verifiedClaims.length > 0) {
    const verifiedSectionHeader = document.createElement('div');
    verifiedSectionHeader.className = 'section-subheading';
    verifiedSectionHeader.classList.add('verified-section');
    verifiedSectionHeader.textContent = `Verified Claims (${verifiedClaims.length})`;
    container.appendChild(verifiedSectionHeader);

    verifiedClaims.forEach(c => renderClaimCard(c, container));
  }

  if (unverifiableClaims.length > 0) {
    const unverifiedSectionHeader = document.createElement('div');
    unverifiedSectionHeader.className = 'section-subheading';
    unverifiedSectionHeader.classList.add('unverified-section');
    unverifiedSectionHeader.textContent = `Unverified / Inconclusive Claims (${unverifiableClaims.length})`;
    container.appendChild(unverifiedSectionHeader);

    unverifiableClaims.forEach(c => renderClaimCard(c, container));
  }
}

// --- Operational Metrics Polling ---
function setupMetricsPolling() {
  const stateNote = document.getElementById('system-status');
  const fetchAndUpdate = async () => {
    try {
      const [depRes, readyRes, provRes] = await Promise.all([
        fetch('/api/deployment/status'),
        fetch('/health/ready'),
        fetch('/api/provider/status')
      ]);
      const healthyResponses = depRes.ok && readyRes.ok && provRes.ok;
      if (stateNote) {
        stateNote.textContent = healthyResponses ? '' : 'Some system details are unavailable. Showing the last received values.';
        stateNote.classList.toggle('is-error', !healthyResponses);
        stateNote.classList.toggle('is-loading', false);
      }

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

      if (provRes.ok) {
        const provData = await provRes.json();
        const llm = provData.llm || {};
        const metrics = provData.metrics || {};
        const cbs = provData.circuit_breakers || {};

        // OpenAI Status
        const llmStatusEl = document.getElementById('stat-llm-status');
        const llmModelEl = document.getElementById('stat-llm-model');
        const llmTokensEl = document.getElementById('stat-llm-tokens');
        const llmTokensDetailEl = document.getElementById('stat-llm-tokens-detail');
        const llmBudgetEl = document.getElementById('stat-llm-budget');
        const llmRetriesEl = document.getElementById('stat-llm-retries');

        if (llmStatusEl) {
          const status = (llm.status || 'configured').toUpperCase();
          if (status === 'HEALTHY' || status === 'CONFIGURED') {
            llmStatusEl.textContent = 'HEALTHY';
            setStatusTone(llmStatusEl, 'positive');
          } else if (status === 'MISSING_CREDENTIALS') {
            llmStatusEl.textContent = 'MISSING KEY';
            setStatusTone(llmStatusEl, 'danger');
          } else if (status === 'QUOTA_EXHAUSTED') {
            llmStatusEl.textContent = 'QUOTA EXHAUSTED';
            setStatusTone(llmStatusEl, 'danger');
          } else if (status === 'RATE_LIMITED' || status === 'DEGRADED') {
            llmStatusEl.textContent = status.replace(/_/g, ' ');
            setStatusTone(llmStatusEl, 'warning');
          } else {
            llmStatusEl.textContent = status.replace(/_/g, ' ');
            setStatusTone(llmStatusEl, 'danger');
          }
        }
        if (llmModelEl) llmModelEl.textContent = `Model: ${llm.model || 'gpt-4o-mini'}`;
        if (llmTokensEl) llmTokensEl.textContent = Number(llm.total_tokens || 0).toLocaleString();
        if (llmTokensDetailEl) llmTokensDetailEl.textContent = `Prompt: ${Number(llm.prompt_tokens || 0).toLocaleString()} | Completion: ${Number(llm.completion_tokens || 0).toLocaleString()}`;
        if (llmBudgetEl) {
          if (llm.status === 'quota_exhausted') {
            llmBudgetEl.textContent = 'EXHAUSTED';
            setStatusTone(llmBudgetEl, 'danger');
          } else if (llm.status === 'missing_credentials') {
            llmBudgetEl.textContent = 'UNCONFIGURED';
            setStatusTone(llmBudgetEl, 'danger');
          } else if (llm.status === 'rate_limited') {
            llmBudgetEl.textContent = 'RATE LIMITED';
            setStatusTone(llmBudgetEl, 'warning');
          } else {
            llmBudgetEl.textContent = 'AVAILABLE';
            setStatusTone(llmBudgetEl, 'positive');
          }
        }
        if (llmRetriesEl) llmRetriesEl.textContent = `Retries: ${llm.retries_count || 0} | 429s: ${llm.rate_limit_count || 0}`;

        // Tavily Status
        const tavily = provData.tavily || {};
        const tavilyStatusEl = document.getElementById('stat-tavily-status');
        const tavilyFailuresEl = document.getElementById('stat-tavily-failures');
        if (tavilyStatusEl) {
          const tStatus = (tavily.status || 'configured').toUpperCase();
          if (tStatus === 'HEALTHY' || tStatus === 'CONFIGURED') {
            tavilyStatusEl.textContent = 'CONNECTED';
            setStatusTone(tavilyStatusEl, 'positive');
          } else if (tStatus === 'MISSING_CREDENTIALS') {
            tavilyStatusEl.textContent = 'MISSING KEY';
            setStatusTone(tavilyStatusEl, 'danger');
          } else if (tStatus === 'RATE_LIMITED') {
            tavilyStatusEl.textContent = 'RATE LIMITED';
            setStatusTone(tavilyStatusEl, 'warning');
          } else {
            tavilyStatusEl.textContent = tStatus;
            setStatusTone(tavilyStatusEl, 'danger');
          }
        }
        if (tavilyFailuresEl) {
          tavilyFailuresEl.textContent = `Search Failures: ${tavily.failed_searches || metrics.search_failures || 0}`;
        }

        // Gateway Verification
        const claimsRatioEl = document.getElementById('stat-claims-ratio');
        const claimsStaleEl = document.getElementById('stat-claims-stale');
        const cacheHitRateEl = document.getElementById('stat-cache-hitrate');
        const cacheOpsEl = document.getElementById('stat-cache-ops');
        const avgLatencyEl = document.getElementById('stat-avg-latency');

        if (claimsRatioEl) claimsRatioEl.textContent = `${metrics.claims_extracted || 0} / ${metrics.claims_checked || 0}`;
        if (claimsStaleEl) claimsStaleEl.textContent = `Stale Detected: ${metrics.claims_stale || 0}`;
        if (cacheHitRateEl) {
          const hitRate = Number(metrics.cache_hit_rate_pct);
          cacheHitRateEl.textContent = Number.isFinite(hitRate) ? `${hitRate}%` : '—';
        }
        if (cacheOpsEl) cacheOpsEl.textContent = `Hits: ${metrics.cache_hits || 0} | Misses: ${metrics.cache_misses || 0}`;
        if (avgLatencyEl) avgLatencyEl.textContent = `${metrics.avg_latency_ms || 0} ms`;

        // Concurrency & Queues
        const concurrencyEl = document.getElementById('stat-concurrency');
        const queuedEl = document.getElementById('stat-queued');
        if (concurrencyEl) concurrencyEl.textContent = `${llm.active_requests || 0} ACTIVE`;
        if (queuedEl) queuedEl.textContent = `Queue Depth: ${llm.queued_requests || 0}`;
        recordMetricHistory();
      }
    } catch (_) {
      if (stateNote) {
        stateNote.textContent = 'System metrics could not be refreshed. Check the gateway connection.';
        stateNote.classList.remove('is-loading');
        stateNote.classList.add('is-error');
      }
    }
  };

  fetchAndUpdate();
  setInterval(fetchAndUpdate, 5000);
}

const freshGateMetricHistory = new Map();
function recordMetricHistory() {
  const ids = ['stat-llm-status','stat-llm-tokens','stat-llm-budget','stat-tavily-status','stat-claims-ratio','stat-cache-hitrate','stat-avg-latency','stat-concurrency'];
  for (const id of ids) {
    const valueEl = document.getElementById(id), box = valueEl?.closest('.metric-box');
    if (!valueEl || !box) continue;
    if (!box.querySelector('.metric-viz')) { const viz = document.createElement('div'); viz.className = 'metric-viz'; box.appendChild(viz); }
    const text = valueEl.textContent.trim();
    const numeric = id === 'stat-llm-status' || id === 'stat-llm-budget' || id === 'stat-tavily-status' ? null : Number((text.match(/-?\d+(?:\.\d+)?/) || [])[0]);
    const tone = /missing|exhaust|error|down|unavailable/i.test(text) ? 'bad' : /rate limited|degraded|warning/i.test(text) ? 'warn' : 'good';
    const history = freshGateMetricHistory.get(id) || [];
    history.push({ value: numeric, tone }); if (history.length > 20) history.shift(); freshGateMetricHistory.set(id, history);
    drawMetricHistory(box.querySelector('.metric-viz'), history, numeric === null);
  }
}
function drawMetricHistory(container, history, status) {
  if (!container) return;
  container.replaceChildren();
  if (history.length < 2) { const line = document.createElement('span'); line.className = 'history-waiting-line'; container.appendChild(line); const note = document.createElement('small'); note.textContent = 'WAITING FOR DATA'; container.appendChild(note); return; }
  if (status) { const dots = document.createElement('div'); dots.className = 'history-dots'; history.forEach(sample => { const dot = document.createElement('i'); dot.className = `tone-${sample.tone}`; dots.appendChild(dot); }); container.append(dots); return; }
  const values = history.map(sample => sample.value).filter(Number.isFinite), min = Math.min(...values), max = Math.max(...values), span = max - min || 1;
  const svg = document.createElementNS('http://www.w3.org/2000/svg','svg'); svg.setAttribute('viewBox','0 0 160 24'); svg.setAttribute('preserveAspectRatio','none'); svg.setAttribute('aria-hidden','true');
  const polyline = document.createElementNS('http://www.w3.org/2000/svg','polyline'); polyline.setAttribute('fill','none'); polyline.setAttribute('stroke','rgba(185,164,255,.8)'); polyline.setAttribute('stroke-width','1.5'); polyline.setAttribute('points', values.map((value,index) => `${index * 160 / (values.length - 1)},${22 - (value - min) / span * 18}`).join(' ')); svg.appendChild(polyline); container.appendChild(svg);
}

// --- Benchmark & Reliability View ---
function setupBenchmarkView() {
  const btn = document.getElementById('load-benchmark-btn');
  const container = document.getElementById('benchmark-content');

  const loadData = async () => {
    if (!container) return;
    container.replaceChildren();
    const skeleton = document.createElement('div');
    skeleton.className = 'benchmark-skeleton';
    skeleton.setAttribute('role', 'status');
    skeleton.setAttribute('aria-label', 'Loading benchmark results');
    for (let i = 0; i < 3; i++) skeleton.appendChild(document.createElement('i'));
    container.appendChild(skeleton);
    try {
      const res = await fetch('/api/benchmark');
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      clearElement(container);

      renderBenchmarkMarkdown(data.results_md || 'No benchmark report generated yet.', container);
    } catch (e) {
      clearElement(container);
      const error = document.createElement('p');
      error.className = 'view-error';
      error.textContent = 'Could not load benchmark results. Try reloading the report.';
      container.appendChild(error);
    }
  };

  if (btn) btn.addEventListener('click', loadData);
  loadData();
}

function renderBenchmarkMarkdown(markdown, container) {
  const clean = value => String(value || '').replace(/[\*_`]/g, '').trim();
  const lines = markdown.split(/\r?\n/);
  const parsedTables = [];
  let activeTable = null;
  for (const line of lines) {
    if (line.trim().startsWith('|')) {
      const cells = line.trim().replace(/^\||\|$/g, '').split('|').map(clean);
      if (cells.every(x => /^:?-{3,}:?$/.test(x))) continue;
      if (!activeTable) { activeTable = { rows: [] }; parsedTables.push(activeTable); }
      activeTable.rows.push(cells);
    } else activeTable = null;
  }
  const header = container.closest('.tab-content')?.querySelector('.tab-banner');
  const countMatch = markdown.match(/\*\*(\d+) independently labelled claims\*\*/i);
  if (header) header.querySelector('.section-subtitle').textContent = countMatch ? `${countMatch[1]} labelled claims across volatile factual categories` : 'Results parsed from the current benchmark report';
  const stats = document.createElement('div'); stats.className = 'benchmark-stats';
  const first = parsedTables[0]?.rows || [];
  const metrics = first.slice(1).map(row => ({ label: clean(row[0]), value: clean(row[1]), target: clean(row[2]), assessment: clean(row[3]) }));
  const chosen = ['Precision', 'Recall (Stale Claim Detection)', 'F1 Score', '95th Percentile (p95) Latency'];
  for (const wanted of chosen) {
    const item = metrics.find(x => x.label.toLowerCase().includes(wanted.toLowerCase().split(' (')[0]));
    if (!item) continue;
    const card = document.createElement('article'); card.className = 'benchmark-stat';
    const label = document.createElement('span'); label.className = 'metric-title'; label.textContent = item.label;
    const value = document.createElement('strong'); value.textContent = item.value;
    const target = document.createElement('small'); target.textContent = `Target ${item.target}`;
    const measured = Number((item.value.match(/[\d.]+/) || [])[0]);
    const threshold = Number((item.target.match(/[\d.]+/) || [])[0]);
    const isLatency = /latency/i.test(item.label);
    const met = Number.isFinite(measured) && Number.isFinite(threshold) && (isLatency ? measured <= threshold : measured >= threshold);
    const chip = document.createElement('span'); chip.className = `benchmark-chip${met ? '' : ' below'}`; chip.textContent = met ? 'MET' : 'BELOW TARGET';
    card.append(label, value, target, chip); stats.appendChild(card);
  }
  container.appendChild(stats);
  let tableIndex = 0;
  for (const tableData of parsedTables) {
    const section = document.createElement('section'); section.className = 'benchmark-section';
    const title = document.createElement('h3'); title.textContent = tableIndex++ === 0 ? 'Core quality metrics' : 'Multi-suite performance breakdown'; section.appendChild(title);
    const wrap = document.createElement('div'); wrap.className = 'benchmark-table-scroll';
    const table = document.createElement('table'); table.className = 'benchmark-table';
    tableData.rows.forEach((row, ri) => { const tr = document.createElement('tr'); row.forEach(cell => { const el = document.createElement(ri === 0 ? 'th' : 'td'); el.textContent = clean(cell); tr.appendChild(el); }); table.appendChild(tr); });
    wrap.appendChild(table); section.appendChild(wrap); container.appendChild(section);
  }
  const prose = document.createElement('div'); prose.className = 'benchmark-prose';
  let list = null, defense = null, kv = null;
  const flushList = () => { list = null; };
  const flushKv = () => { if (kv?.children.length) prose.appendChild(kv); kv = null; };
  const appendText = (parent, source) => {
    const value = String(source || ''); const pattern = /(`[^`]+`|\*\*[^*]+\*\*|_[^_]+_)/g; let last = 0, match;
    while ((match = pattern.exec(value))) { parent.appendChild(document.createTextNode(value.slice(last, match.index))); const node = document.createElement(match[0][0] === '`' ? 'code' : 'strong'); node.textContent = clean(match[0]); parent.appendChild(node); last = pattern.lastIndex; }
    parent.appendChild(document.createTextNode(value.slice(last)));
  };
  for (const raw of lines) {
    const line = raw.trim();
    const normalized = clean(line);
    if (!line || line.startsWith('|') || /^\|?\s*:?-{3,}/.test(line)) continue;
    if (line === '---') { flushList(); flushKv(); defense = null; prose.appendChild(document.createElement('hr')); continue; }
    const hash = normalized.match(/^(#{1,3})\s+(.+)/);
    const numberedHeading = !hash && normalized.match(/^(\d+)\.\s+(.{4,})$/) && !/:\s*\S/.test(normalized) && !/:$/.test(normalized);
    if (hash || numberedHeading) {
      flushList(); flushKv(); defense = null;
      const section = document.createElement('section'); section.className = 'report-section';
      const title = document.createElement('h3'); const number = numberedHeading ? numberedHeading[1] : (hash[2].match(/^(\d+)\./)?.[1] || '');
      if (number) { const eyebrow = document.createElement('span'); eyebrow.className = 'report-number'; eyebrow.textContent = number.padStart(2, '0'); section.appendChild(eyebrow); }
      title.textContent = clean(numberedHeading ? numberedHeading[2] : hash[2].replace(/^\d+\.\s*/, '')); section.appendChild(title); prose.appendChild(section); continue;
    }
    const defenseHeading = normalized.match(/^(\d+)\.\s+([^:]{4,}):$/);
    if (defenseHeading) {
      flushList(); flushKv(); defense = document.createElement('article'); defense.className = 'defense-card';
      const title = document.createElement('h4'); title.textContent = clean(defenseHeading[2]); defense.appendChild(title); prose.appendChild(defense); continue;
    }
    const item = line.match(/^(?:[-*]\s+|\d+\.\s+)(.+)$/);
    if (item) {
      const normalizedItem = clean(item[1]);
      const defensePair = defense && normalizedItem.match(/^([^,:]{2,40}):\s+(.+)$/);
      if (defensePair) { if (!defense.querySelector('dl')) defense.appendChild(document.createElement('dl')); const dl = defense.querySelector('dl'), dt = document.createElement('dt'), dd = document.createElement('dd'); dt.textContent = clean(defensePair[1]); appendText(dd, defensePair[2]); dl.append(dt, dd); continue; }
      const keyValue = normalizedItem.match(/^(.+?):\s+(.+)$/);
      if (keyValue && !defense) { if (!kv) { kv = document.createElement('dl'); kv.className = 'report-kv'; } const dt = document.createElement('dt'), dd = document.createElement('dd'); dt.textContent = clean(keyValue[1]); appendText(dd, keyValue[2]); kv.append(dt, dd); continue; }
      flushKv();
      if (!list) { list = document.createElement(/^\d+\./.test(line) ? 'ol' : 'ul'); (defense || prose).appendChild(list); }
      const li = document.createElement('li'); appendText(li, item[1]); list.appendChild(li); continue;
    }
    flushList();
    const pair = line.match(/^(.{2,50}?):\s+(.+)$/);
    if (pair) { if (!kv) { kv = document.createElement('dl'); kv.className = 'report-kv'; } const dt = document.createElement('dt'), dd = document.createElement('dd'); dt.textContent = clean(pair[1]); appendText(dd, pair[2]); kv.append(dt, dd); continue; }
    flushKv(); const p = document.createElement('p'); appendText(p, line); (defense || prose).appendChild(p);
  }
  flushList(); flushKv();
  container.appendChild(prose);
}

// --- Historical Audit Inspector ---
function setupAuditView() {
  const btn = document.getElementById('refresh-audits-btn');
  const exportBtn = document.getElementById('export-audits-btn');
  const container = document.getElementById('audits-table-body');
  const adminKeyInput = document.getElementById('admin-key-input');
  if (exportBtn) exportBtn.disabled = true;
  document.getElementById('toggle-admin-key')?.addEventListener('click', event => {
    if (!adminKeyInput) return;
    const reveal = adminKeyInput.type === 'password';
    adminKeyInput.type = reveal ? 'text' : 'password';
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg'); svg.setAttribute('viewBox', '0 0 24 24'); svg.setAttribute('aria-hidden', 'true');
    if (reveal) { const path = document.createElementNS(svg.namespaceURI, 'path'); path.setAttribute('d', 'M3 3l18 18M10.6 10.6a2 2 0 0 0 2.8 2.8M9.9 5.2A10.7 10.7 0 0 1 12 5c6.4 0 10 7 10 7a15.5 15.5 0 0 1-3.1 3.8M6.2 6.2C3.5 8 2 12 2 12s3.6 7 10 7c1 0 2-.2 2.8-.5'); svg.appendChild(path); }
    else { const path = document.createElementNS(svg.namespaceURI, 'path'); path.setAttribute('d', 'M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12Z'); const eye = document.createElementNS(svg.namespaceURI, 'circle'); eye.setAttribute('cx', '12'); eye.setAttribute('cy', '12'); eye.setAttribute('r', '3'); svg.append(path, eye); }
    event.currentTarget.replaceChildren(svg); event.currentTarget.setAttribute('aria-label', reveal ? 'Hide admin key' : 'Show admin key');
  });
  adminKeyInput?.addEventListener('input', () => {
    btn?.classList.toggle('has-key', Boolean(adminKeyInput.value.trim()));
  });

  const fetchAudits = async () => {
    if (!container) return;
    if (exportBtn) exportBtn.disabled = true;
    const adminKey = adminKeyInput ? adminKeyInput.value.trim() : '';
    clearElement(container);
    const loadingRow = document.createElement('tr');
    const loadingCell = document.createElement('td');
    loadingCell.colSpan = 6;
    loadingCell.className = 'audit-loading';
    loadingCell.textContent = 'Loading audit records';
    loadingRow.appendChild(loadingCell);
    container.appendChild(loadingRow);

    try {
      const headers = adminKey ? { 'X-Admin-Key': adminKey } : {};
      const res = await fetch('/api/audits/recent?limit=25', { headers });
      clearElement(container);

      if (res.status === 401 || res.status === 403) {
        const tr = document.createElement('tr');
        const td = document.createElement('td');
        td.colSpan = 6;
        td.className = 'audit-empty';
        const lock = document.createElement('span'); lock.className = 'audit-lock'; lock.setAttribute('aria-hidden', 'true');
        const lockSvg = document.createElementNS('http://www.w3.org/2000/svg', 'svg'); lockSvg.setAttribute('viewBox', '0 0 24 24');
        const shackle = document.createElementNS(lockSvg.namespaceURI, 'path'); shackle.setAttribute('d', 'M5 10h14v11H5zM8 10V7a4 4 0 0 1 8 0v3'); lockSvg.appendChild(shackle); lock.appendChild(lockSvg);
        const title = document.createElement('strong'); title.textContent = 'Enter your admin key to view the audit log';
        const note = document.createElement('span'); note.textContent = 'Keys are only sent with the request and never stored.';
        td.append(lock, title, note);
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
      if (exportBtn) exportBtn.disabled = false;

      audits.forEach(a => {
        const tr = document.createElement('tr');

        const tdTime = document.createElement('td');
        tdTime.textContent = new Date(a.created_at * 1000).toLocaleTimeString();
        tdTime.className = 'audit-mono';
        tr.appendChild(tdTime);

        const tdCorr = document.createElement('td');
        const copyId = document.createElement('button'); copyId.type = 'button'; copyId.className = 'copy-id'; copyId.textContent = (a.correlation_id || '').substring(0, 12); copyId.title = 'Copy correlation ID';
        copyId.addEventListener('click', async () => { try { await navigator.clipboard.writeText(a.correlation_id || ''); copyId.textContent = 'Copied'; setTimeout(() => { copyId.textContent = (a.correlation_id || '').substring(0, 12); }, 1200); } catch (_) {} });
        tdCorr.appendChild(copyId); tdCorr.className = 'audit-mono';
        tr.appendChild(tdCorr);

        const tdText = document.createElement('td');
        tdText.textContent = (a.input_text || '').substring(0, 60) + '...';
        tr.appendChild(tdText);

        const tdScore = document.createElement('td');
        tdScore.textContent = (a.avg_score !== null && a.avg_score !== undefined) ? `${a.avg_score}/100` : 'N/A';
        tdScore.className = 'score-chip';
        tr.appendChild(tdScore);

        const tdStale = document.createElement('td');
        tdStale.textContent = a.stale_claims;
        tdStale.className = 'stale-chip';
        tr.appendChild(tdStale);

        const tdLatency = document.createElement('td');
        tdLatency.textContent = `${a.latency_ms} ms`;
        tr.appendChild(tdLatency);

        container.appendChild(tr);
      });
    } catch (err) {
      clearElement(container);
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
