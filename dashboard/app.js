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
  const status = summary.status || data.status || 'completed';
  const extractionStatus = summary.extraction_status || (data.extraction ? data.extraction.status : 'success');

  // Summary Card
  const summaryBox = document.createElement('div');
  summaryBox.className = 'claim-card';
  summaryBox.style.marginBottom = '16px';

  // Determine border color based on status
  let borderColor = '#10b981'; // Green
  if (status === 'extraction_failed' || status === 'verification_failed' || status === 'external_provider_unavailable') {
    borderColor = '#ef4444'; // Red
  } else if (status === 'no_claims_detected') {
    borderColor = '#64748b'; // Neutral Slate
  } else if (status === 'partially_verified' || status === 'completed_with_warnings') {
    borderColor = '#f59e0b'; // Amber
  } else if (summary.stale > 0) {
    borderColor = '#ef4444'; // Red for stale claims
  } else if (summary.avg_score !== null && summary.avg_score !== undefined && summary.avg_score < 70) {
    borderColor = '#f59e0b'; // Amber for degraded score
  }
  summaryBox.style.borderLeft = `4px solid ${borderColor}`;

  // Header row in summary box
  const headerRow = document.createElement('div');
  headerRow.style.display = 'flex';
  headerRow.style.justifyContent = 'space-between';
  headerRow.style.alignItems = 'center';
  headerRow.style.flexWrap = 'wrap';
  headerRow.style.gap = '8px';
  headerRow.style.marginBottom = '8px';

  const title = document.createElement('h3');
  title.style.fontSize = '1.05rem';
  title.style.margin = '0';

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
  statsP.style.color = '#9ca3af';
  statsP.style.fontSize = '0.9rem';
  statsP.textContent = `Total Claims: ${summary.total || 0} | Checked: ${summary.checked || 0} | Stale: ${summary.stale || 0} | Extraction: ${extractionStatus.replace(/_/g, ' ')}`;
  summaryBox.appendChild(statsP);

  // Informative / Alert Message Banner
  if (summary.message || data.error) {
    const banner = document.createElement('div');
    const msgText = summary.message || data.error;
    banner.className = 'alert-banner';
    if (status === 'extraction_failed' || status === 'verification_failed' || status === 'external_provider_unavailable') {
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
    emptyBox.className = 'claim-card';
    emptyBox.style.marginTop = '12px';

    const p = document.createElement('p');
    p.style.color = '#9ca3af';
    p.style.fontSize = '0.95rem';

    if (status === 'no_claims_detected') {
      p.textContent = 'No volatile factual claims found. Response passed through as stable (conversational, subjective, or general statements).';
    } else if (status === 'extraction_failed') {
      p.textContent = 'Claim extraction failed. Unable to verify response freshness.';
      p.style.color = '#f87171';
    } else if (status === 'verification_failed') {
      p.textContent = 'Fact verification failed due to engine or external service error.';
      p.style.color = '#f87171';
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
    card.style.marginBottom = '12px';

    const header = document.createElement('div');
    header.className = 'claim-header';

    const typeSpan = document.createElement('span');
    typeSpan.style.color = '#9ca3af';
    typeSpan.style.fontSize = '0.8rem';
    typeSpan.style.fontWeight = '600';
    typeSpan.textContent = `[${(c.category || c.type || 'unknown').toUpperCase()}]`;
    header.appendChild(typeSpan);

    const badge = document.createElement('span');
    const verdict = c.verdict || 'unverifiable';
    badge.className = `badge badge-${verdict}`;
    badge.textContent = verdict.replace(/_/g, ' ');
    header.appendChild(badge);

    card.appendChild(header);

    const claimText = document.createElement('p');
    claimText.style.fontWeight = '600';
    claimText.style.fontSize = '0.95rem';
    claimText.style.marginBottom = '8px';
    claimText.textContent = c.text;
    card.appendChild(claimText);

    if (c.verdict === 'unverifiable') {
      const unverifiableNote = document.createElement('p');
      unverifiableNote.style.color = '#94a3b8';
      unverifiableNote.style.fontSize = '0.85rem';
      unverifiableNote.style.fontStyle = 'italic';
      unverifiableNote.style.marginBottom = '8px';
      unverifiableNote.textContent = 'No conclusive web evidence was found to confirm or contradict this claim.';
      card.appendChild(unverifiableNote);
    }

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
      corrBox.style.border = '1px solid #ef4444';
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

        if (s.snippet) {
          const snip = document.createElement('p');
          snip.style.color = '#94a3b8';
          snip.style.marginTop = '4px';
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
    verifiedSectionHeader.style.color = '#34d399';
    verifiedSectionHeader.textContent = `Verified Claims (${verifiedClaims.length})`;
    container.appendChild(verifiedSectionHeader);

    verifiedClaims.forEach(c => renderClaimCard(c, container));
  }

  if (unverifiableClaims.length > 0) {
    const unverifiedSectionHeader = document.createElement('div');
    unverifiedSectionHeader.className = 'section-subheading';
    unverifiedSectionHeader.style.color = '#94a3b8';
    unverifiedSectionHeader.textContent = `Unverified / Inconclusive Claims (${unverifiableClaims.length})`;
    container.appendChild(unverifiedSectionHeader);

    unverifiableClaims.forEach(c => renderClaimCard(c, container));
  }
}

// --- Operational Metrics Polling ---
function setupMetricsPolling() {
  const fetchAndUpdate = async () => {
    try {
      const [depRes, readyRes, provRes] = await Promise.all([
        fetch('/api/deployment/status'),
        fetch('/health/ready'),
        fetch('/api/provider/status')
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
          const status = (llm.status || 'healthy').toUpperCase();
          llmStatusEl.textContent = status;
          if (status === 'HEALTHY') llmStatusEl.style.color = '#34d399';
          else if (status === 'RATE_LIMITED' || status === 'DEGRADED') llmStatusEl.style.color = '#fbbf24';
          else llmStatusEl.style.color = '#f87171';
        }
        if (llmModelEl) llmModelEl.textContent = `Model: ${llm.model || 'gpt-4o-mini'}`;
        if (llmTokensEl) llmTokensEl.textContent = Number(llm.total_tokens || 0).toLocaleString();
        if (llmTokensDetailEl) llmTokensDetailEl.textContent = `Prompt: ${Number(llm.prompt_tokens || 0).toLocaleString()} | Completion: ${Number(llm.completion_tokens || 0).toLocaleString()}`;
        if (llmBudgetEl) {
          if (llm.status === 'quota_exhausted') {
            llmBudgetEl.textContent = 'EXHAUSTED';
            llmBudgetEl.style.color = '#f87171';
          } else if (llm.status === 'rate_limited') {
            llmBudgetEl.textContent = 'RATE LIMITED';
            llmBudgetEl.style.color = '#fbbf24';
          } else {
            llmBudgetEl.textContent = 'AVAILABLE';
            llmBudgetEl.style.color = '#34d399';
          }
        }
        if (llmRetriesEl) llmRetriesEl.textContent = `Retries: ${llm.retries_count || 0} | 429s: ${llm.rate_limit_count || 0}`;

        // Tavily Status
        const tavilyStatusEl = document.getElementById('stat-tavily-status');
        const tavilyFailuresEl = document.getElementById('stat-tavily-failures');
        if (tavilyStatusEl) {
          const tavilyBreaker = cbs.tavily_search || {};
          const isTavilyOpen = tavilyBreaker.state === 'open';
          tavilyStatusEl.textContent = isTavilyOpen ? 'DEGRADED' : 'CONNECTED';
          tavilyStatusEl.style.color = isTavilyOpen ? '#f87171' : '#34d399';
        }
        if (tavilyFailuresEl) tavilyFailuresEl.textContent = `Search Failures: ${metrics.search_failures || 0}`;

        // Gateway Verification
        const claimsRatioEl = document.getElementById('stat-claims-ratio');
        const claimsStaleEl = document.getElementById('stat-claims-stale');
        const cacheHitRateEl = document.getElementById('stat-cache-hitrate');
        const cacheOpsEl = document.getElementById('stat-cache-ops');
        const avgLatencyEl = document.getElementById('stat-avg-latency');

        if (claimsRatioEl) claimsRatioEl.textContent = `${metrics.claims_extracted || 0} / ${metrics.claims_checked || 0}`;
        if (claimsStaleEl) claimsStaleEl.textContent = `Stale Detected: ${metrics.claims_stale || 0}`;
        if (cacheHitRateEl) cacheHitRateEl.textContent = `${metrics.cache_hit_rate_pct || 100}%`;
        if (cacheOpsEl) cacheOpsEl.textContent = `Hits: ${metrics.cache_hits || 0} | Misses: ${metrics.cache_misses || 0}`;
        if (avgLatencyEl) avgLatencyEl.textContent = `${metrics.avg_latency_ms || 0} ms`;

        // Concurrency & Queues
        const concurrencyEl = document.getElementById('stat-concurrency');
        const queuedEl = document.getElementById('stat-queued');
        if (concurrencyEl) concurrencyEl.textContent = `${llm.active_requests || 0} ACTIVE`;
        if (queuedEl) queuedEl.textContent = `Queue Depth: ${llm.queued_requests || 0}`;
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
        tdScore.textContent = (a.avg_score !== null && a.avg_score !== undefined) ? `${a.avg_score}/100` : 'N/A';
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
