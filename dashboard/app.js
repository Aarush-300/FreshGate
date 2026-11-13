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
  setupPolicyStudio();
  setupWatchtower();
  setupReplay();
});

function setStatusTone(element, tone) {
  if (!element) return;
  element.classList.remove('status-positive', 'status-warning', 'status-danger', 'status-accent', 'status-neutral');
  element.classList.add(`status-${tone}`);
}

// --- Tab Switching ---
function setupTabs() {
  const navTabs = document.querySelector('.nav-tabs');
  const tabBtns = document.querySelectorAll('.tab-btn');
  const tabContents = document.querySelectorAll('.tab-content');
  let switching = false;

  function updatePillPosition(activeBtn) {
    if (!navTabs || !activeBtn) return;
    const navRect = navTabs.getBoundingClientRect();
    const btnRect = activeBtn.getBoundingClientRect();
    const offset = btnRect.left - navRect.left + navTabs.scrollLeft - 4;
    navTabs.style.setProperty('--tab-offset', `${Math.max(0, Math.round(offset))}px`);
    navTabs.style.setProperty('--tab-width', `${Math.round(btnRect.width)}px`);
  }

  const initialActive = document.querySelector('.tab-btn.active') || tabBtns[0];
  if (initialActive) {
    window.requestAnimationFrame(() => updatePillPosition(initialActive));
  }

  window.addEventListener('resize', () => {
    const curActive = document.querySelector('.tab-btn.active');
    if (curActive) updatePillPosition(curActive);
  });

  tabBtns.forEach(btn => {
    btn.addEventListener('click', () => {
      if (switching || btn.classList.contains('active')) return;
      const targetId = btn.getAttribute('data-tab');
      switching = true;
      const current = document.querySelector('.tab-content.active');
      current?.classList.add('is-exiting');
      window.setTimeout(() => {
        tabBtns.forEach(b => b.classList.toggle('active', b === btn));
        updatePillPosition(btn);
        tabContents.forEach(c => c.classList.remove('active', 'is-exiting', 'view-enter'));
        const targetContent = document.getElementById(targetId);
        if (targetContent) {
          document.body.dataset.ambient = targetContent.dataset.ambient || 'verify';
          targetContent.classList.add('view-enter');
          targetContent.classList.add('active');
        }
        window.setTimeout(() => { targetContent?.classList.remove('view-enter'); switching = false; }, 500);
      }, 160);
    });
  });
}

// --- Verification Console ---
const PROVIDER_PRESETS = {
  openai: {
    baseUrl: 'https://api.openai.com/v1',
    model: 'gpt-4o-mini',
    keyLabel: 'OPENAI API KEY',
    placeholder: 'sk-… (or leave blank for server .env)',
    hint: 'Standard OpenAI developer platform. Requires prepaid credit balance at platform.openai.com.'
  },
  groq: {
    baseUrl: 'https://api.groq.com/openai/v1',
    model: 'openai/gpt-oss-20b',
    keyLabel: 'GROQ API KEY',
    placeholder: 'gsk_… (from console.groq.com)',
    hint: 'Groq GPT-OSS 20B. Requires a Groq API key; account access, limits, and pricing apply.'
  },
  gemini: {
    baseUrl: 'https://generativelanguage.googleapis.com/v1beta/openai',
    model: 'gemini-1.5-flash',
    keyLabel: 'GOOGLE GEMINI API KEY',
    placeholder: 'AIzaSy… (free at aistudio.google.com)',
    hint: 'Google AI Studio free tier (15 RPM, no billing required).'
  },
  ollama: {
    baseUrl: 'http://localhost:11434/v1',
    model: 'llama3.2',
    keyLabel: 'OLLAMA API KEY',
    placeholder: 'Optional (defaults to "ollama")',
    hint: 'Local offline execution. Run "ollama run llama3.2" on your machine.'
  },
  openrouter: {
    baseUrl: 'https://openrouter.ai/api/v1',
    model: 'meta-llama/llama-3.3-70b-instruct:free',
    keyLabel: 'OPENROUTER API KEY',
    placeholder: 'sk-or-v1-…',
    hint: 'Free-tier models available at openrouter.ai.'
  },
  custom: {
    baseUrl: '',
    model: '',
    keyLabel: 'API KEY',
    placeholder: 'Enter provider API key',
    hint: 'Custom OpenAI-compatible Chat Completions endpoint.'
  }
};

function setupVerificationForm() {
  const form = document.getElementById('verify-form');
  const input = document.getElementById('verify-input');
  const resultsContainer = document.getElementById('verify-results');
  const submitBtn = document.getElementById('verify-submit-btn');
  const submitLabel = document.getElementById('verify-button-label');
  const openaiKey = document.getElementById('openai-api-key');
  const tavilyKey = document.getElementById('tavily-api-key');
  const policySelect = document.getElementById('verify-policy');
  const providerSelect = document.getElementById('ai-provider-select');
  const modelInput = document.getElementById('ai-model-input');
  const baseUrlInput = document.getElementById('ai-base-url');
  const keyLabel = document.getElementById('ai-key-label');
  const hintText = document.getElementById('provider-hint-text');

  if (!form) return;

  function updateProviderFields() {
    if (!providerSelect) return;
    const provider = providerSelect.value;
    const preset = PROVIDER_PRESETS[provider] || PROVIDER_PRESETS.openai;
    if (modelInput && preset.model !== undefined) {
      modelInput.value = preset.model;
      modelInput.placeholder = preset.model || 'model-name';
    }
    if (baseUrlInput && preset.baseUrl !== undefined) {
      baseUrlInput.value = preset.baseUrl;
      baseUrlInput.placeholder = preset.baseUrl || 'https://...';
    }
    if (keyLabel) {
      keyLabel.textContent = preset.keyLabel;
    }
    if (openaiKey) {
      openaiKey.placeholder = preset.placeholder;
    }
    if (hintText) {
      hintText.textContent = preset.hint;
    }
  }

  if (providerSelect) {
    providerSelect.addEventListener('change', updateProviderFields);
  }

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const text = input.value.trim();
    if (!text) {
      input.focus();
      return;
    }

    submitBtn.disabled = true;
    submitLabel.textContent = 'Verifying…';
    resultsContainer.setAttribute('aria-busy', 'true');
    clearElement(resultsContainer);

    const loadingP = document.createElement('p');
    loadingP.className = 'result-loading';
    loadingP.textContent = 'Extracting claims and verifying with live web evidence...';
    resultsContainer.appendChild(loadingP);

    try {
      const headers = { 'Content-Type': 'application/json' };
      const customOpenAI = openaiKey.value.trim();
      const customTavily = tavilyKey.value.trim();
      if (customOpenAI) headers['X-OpenAI-API-Key'] = customOpenAI;
      if (customTavily) headers['X-Tavily-API-Key'] = customTavily;

      const providerValue = providerSelect?.value || 'openai';
      const customModel = modelInput?.value.trim() || '';
      const customBaseUrl = baseUrlInput?.value.trim() || '';

      if (providerValue) headers['X-LLM-Provider'] = providerValue;
      if (customModel) headers['X-LLM-Model'] = customModel;
      if (customBaseUrl) headers['X-LLM-Base-URL'] = customBaseUrl;

      const res = await fetch('/verify', {
        method: 'POST',
        headers,
        body: JSON.stringify({
          answer: text,
          provider: providerValue,
          model: customModel || undefined,
          base_url: customBaseUrl || undefined,
          ...(policySelect?.value ? { policy_id: policySelect.value } : {})
        })
      });

      if (!res.ok) {
        let detail = '';
        try {
          const errorData = await res.json();
          const candidate = errorData.detail || errorData.error || errorData.message || '';
          detail = typeof candidate === 'string' ? candidate : (candidate?.message || '');
        } catch (_) {}
        throw new Error(detail ? String(detail) : `Verification failed: HTTP ${res.status}`);
      }

      const data = await res.json();
      renderVerificationResults(data, resultsContainer, {
        provider: providerValue,
        model: customModel,
        onUseGroqModel: () => {
          if (providerSelect) providerSelect.value = 'groq';
          updateProviderFields();
          if (modelInput) {
            modelInput.value = PROVIDER_PRESETS.groq.model;
            modelInput.focus();
            modelInput.select();
          }
        }
      });
    } catch (err) {
      clearElement(resultsContainer);
      const errBox = document.createElement('div');
      errBox.className = 'result-error';
      errBox.setAttribute('role', 'alert');
      errBox.textContent = getProviderErrorMessage(String(err.message || err), providerSelect?.value || 'openai', modelInput?.value.trim() || '');
      resultsContainer.appendChild(errBox);
    } finally {
      openaiKey.value = '';
      tavilyKey.value = '';
      submitBtn.disabled = false;
      submitLabel.textContent = 'Verify live';
      resultsContainer.setAttribute('aria-busy', 'false');
    }
  });
}

function getProviderErrorMessage(message, provider, model = '') {
  const text = String(message || '');
  if (/model_not_found|model.{0,40}(does not exist|not found|unavailable)|does not exist or you do not have access/i.test(text)) {
    if (provider === 'groq') {
      if (/llama-3\.3-70b-versatile/i.test(model) || /llama-3\.3-70b-versatile/i.test(text)) {
        return 'Groq retired the former Llama 3.3 default for developer accounts. FreshGate now defaults to openai/gpt-oss-20b; confirm that model is available to your Groq project and retry.';
      }
      return `Groq could not access ${model || 'the selected model'}. Check the model ID and confirm it is available to your Groq project.`;
    }
    return `The selected ${provider} model is unavailable or your account cannot access it. Check the model ID and provider account permissions, then retry.`;
  }
  if (/invalid_api_key|incorrect api key|unauthorized|authentication failed/i.test(text)) {
    return `The ${provider} API key was rejected. Check the key for this provider and try again.`;
  }
  if (/insufficient[_ ]quota|quota exhausted|no credits remaining|credit balance|billing limit/i.test(text)) {
    return `The ${provider} account has reached its API quota or billing limit. Check its current plan, usage, and billing settings.`;
  }
  if (/rate_limit|rate limit|too many requests/i.test(text)) {
    return `The ${provider} account is being rate limited. Wait briefly, then retry.`;
  }
  if (/failed to fetch|networkerror|load failed|connection error/i.test(text)) {
    return 'FreshGate could not reach the verification service. Check your connection and try again.';
  }
  if (/timeout|timed out|aborterror/i.test(text)) {
    return 'The verification request took too long. Try again, or shorten the response you submitted.';
  }
  return text;
}

function renderVerificationResults(data, container, requestContext = {}) {
  clearElement(container);

  const summary = data.summary || {};
  const claims = data.claims || [];
  const status = summary.status || data.status || 'completed';
  const extractionStatus = summary.extraction_status || (data.extraction ? data.extraction.status : 'success');
  const rawMessage = summary.message || data.error || '';
  const provider = requestContext.provider || 'openai';
  const model = requestContext.model || '';
  const quotaExhausted = /insufficient[_ ]quota|quota exhausted|no credits remaining|credit balance|billing limit/i.test(rawMessage);
  const modelUnavailable = /model_not_found|model.{0,40}(does not exist|not found|unavailable)|does not exist or you do not have access/i.test(rawMessage);

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
    const msgText = getProviderErrorMessage(rawMessage, provider, model);
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

  if (data.policy_evaluation) {
    const policyBox = document.createElement('section');
    policyBox.className = 'claim-card policy-result-card';
    const policyTitle = document.createElement('h3');
    policyTitle.textContent = `Policy: ${data.policy_evaluation.policy_name || 'Selected policy'}`;
    const policyStatus = document.createElement('p');
    policyStatus.className = 'result-stats';
    policyStatus.textContent = `Mode: ${data.policy_evaluation.verification_mode} · Decision: ${data.policy_evaluation.decision}`;
    policyBox.append(policyTitle, policyStatus);
    for (const outcome of data.policy_evaluation.claims || []) {
      const row = document.createElement('p');
      row.className = 'policy-result-item';
      row.textContent = `${outcome.category}: ${outcome.decision} · sources ${outcome.eligible_source_count}/${outcome.required_source_count} · score ${outcome.freshness_score ?? 'N/A'}/${outcome.minimum_freshness_score ?? 0}`;
      policyBox.appendChild(row);
    }
    container.appendChild(policyBox);
  }

  // Handle empty claims cases based on status
  if (claims.length === 0) {
    const emptyBox = document.createElement('div');
    emptyBox.className = 'claim-card result-empty';

    const p = document.createElement('p');
    p.className = 'result-empty-copy';

    if (status === 'no_claims_detected') {
      p.textContent = 'No volatile factual claims found. Response passed through as stable (conversational, subjective, or general statements).';
    } else if (status === 'extraction_failed') {
      p.textContent = modelUnavailable
        ? 'No freshness verdict is available yet. Update the model above and run the check again.'
        : 'No freshness verdict is available. Review the error above, then check your provider settings and retry.';
      p.classList.add('is-error');
    } else if (status === 'verification_failed') {
      p.textContent = 'Fact verification failed due to engine or external service error.';
      p.classList.add('is-error');
    } else {
      p.textContent = 'No claims were processed.';
    }
    emptyBox.appendChild(p);
    if (status === 'extraction_failed' && modelUnavailable && provider === 'groq' && requestContext.onUseGroqModel) {
      const recoveryButton = document.createElement('button');
      recoveryButton.type = 'button';
      recoveryButton.className = 'secondary model-recovery-button';
      recoveryButton.textContent = 'Use GPT-OSS 20B';
      recoveryButton.addEventListener('click', requestContext.onUseGroqModel);
      emptyBox.appendChild(recoveryButton);
    }
    container.appendChild(emptyBox);
    return;
  }

  // Separate claims into Verified vs Unverified / Inconclusive
  const verifiedClaims = claims.filter(c => c.verdict && c.verdict !== 'unverifiable');
  const unverifiableClaims = claims.filter(c => !c.verdict || c.verdict === 'unverifiable');

  function renderClaimCard(c, targetContainer, claimIndex = 0) {
    const card = document.createElement('div');
    card.className = 'claim-card';
    card.classList.add('result-claim-card');
    card.style.setProperty('--claim-index', String(claimIndex));

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

    if (c.verdict && c.verdict !== 'unverifiable') {
      const monitorButton = document.createElement('button');
      monitorButton.type = 'button';
      monitorButton.className = 'secondary monitor-claim-btn';
      monitorButton.textContent = 'Monitor this claim';
      monitorButton.addEventListener('click', async () => {
        monitorButton.disabled = true;
        try {
          const response = await fetch('/api/watchtower/monitor', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              claim_text: c.text,
              category: c.type || c.category,
              entities: c.entities || [],
              ...(data.policy_evaluation?.policy_id ? { policy_id: data.policy_evaluation.policy_id } : {}),
              monitoring_interval: 86400,
              baseline: { verdict: c.verdict, score: c.score, sources: c.sources || [] }
            })
          });
          const body = await response.json();
          if (!response.ok) throw new Error(body.detail || `HTTP ${response.status}`);
          monitorButton.textContent = 'Claim is monitored';
          document.querySelector('[data-tab="tab-watchtower"]')?.click();
          document.dispatchEvent(new CustomEvent('freshgate:watchtower-refresh'));
        } catch (error) {
          monitorButton.textContent = `Could not monitor: ${error.message}`;
          monitorButton.disabled = false;
        }
      });
      card.appendChild(monitorButton);
    }
    if (c.claim_fingerprint) {
      const replayButton = document.createElement('button');
      replayButton.type = 'button';
      replayButton.className = 'secondary replay-claim-btn';
      replayButton.textContent = 'Open Replay history';
      replayButton.addEventListener('click', () => window.openFreshGateReplay?.(c.claim_fingerprint));
      card.appendChild(replayButton);
    }
    targetContainer.appendChild(card);
  }

  if (verifiedClaims.length > 0) {
    const verifiedSectionHeader = document.createElement('div');
    verifiedSectionHeader.className = 'section-subheading';
    verifiedSectionHeader.classList.add('verified-section');
    verifiedSectionHeader.textContent = `Verified Claims (${verifiedClaims.length})`;
    container.appendChild(verifiedSectionHeader);

    verifiedClaims.forEach((c, idx) => renderClaimCard(c, container, idx));
  }

  if (unverifiableClaims.length > 0) {
    const unverifiedSectionHeader = document.createElement('div');
    unverifiedSectionHeader.className = 'section-subheading';
    unverifiedSectionHeader.classList.add('unverified-section');
    unverifiedSectionHeader.textContent = `Unverified / Inconclusive Claims (${unverifiableClaims.length})`;
    container.appendChild(unverifiedSectionHeader);

    unverifiableClaims.forEach((c, idx) => renderClaimCard(c, container, verifiedClaims.length + idx));
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
  const polyline = document.createElementNS('http://www.w3.org/2000/svg','polyline'); polyline.setAttribute('fill','none'); polyline.setAttribute('stroke','rgba(0,212,146,.8)'); polyline.setAttribute('stroke-width','1.5'); polyline.setAttribute('points', values.map((value,index) => `${index * 160 / (values.length - 1)},${22 - (value - min) / span * 18}`).join(' ')); svg.appendChild(polyline); container.appendChild(svg);
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
  const reportTitleIndex = lines.findIndex(line => /^#\s+/.test(line.trim()));
  const reportLeadIndex = lines.findIndex((line, index) => index > reportTitleIndex && line.trim() && !line.trim().startsWith('#') && !line.trim().startsWith('|'));
  if (reportTitleIndex >= 0) {
    const title = document.createElement('h2');
    title.className = 'benchmark-report-title';
    title.textContent = clean(lines[reportTitleIndex].replace(/^#\s+/, ''));
    container.appendChild(title);
  }
  if (reportLeadIndex >= 0) {
    const lead = document.createElement('p');
    lead.className = 'benchmark-report-lead';
    lead.textContent = clean(lines[reportLeadIndex]);
    container.appendChild(lead);
  }
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
    const firstHeader = (tableData.rows[0]?.[0] || '').toLowerCase();
    const title = document.createElement('h3');
    if (firstHeader.includes('evaluation metric')) title.textContent = 'Core quality metrics';
    else if (firstHeader.includes('evaluation suite')) title.textContent = 'Multi-suite performance breakdown';
    else if (firstHeader.includes('category')) title.textContent = `Category breakdown${countMatch ? ` (${countMatch[1]} production items)` : ''}`;
    else title.textContent = `Benchmark table ${++tableIndex}`;
    section.appendChild(title);
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
  for (const [lineIndex, raw] of lines.entries()) {
    const line = raw.trim();
    if (lineIndex === reportTitleIndex || lineIndex === reportLeadIndex) continue;
    const normalized = clean(line);
    if (!line || line.startsWith('|') || /^\|?\s*:?-{3,}/.test(line)) continue;
    if (line === '---') { flushList(); flushKv(); defense = null; prose.appendChild(document.createElement('hr')); continue; }
    const hash = normalized.match(/^(#{1,3})\s+(.+)/);
    const numberedHeading = !hash && normalized.match(/^(\d+)\.\s+(.{4,})$/) && !/:\s*\S/.test(normalized) && !/:$/.test(normalized);
    if (hash || numberedHeading) {
      flushList(); flushKv(); defense = null;
      if (hash?.[1] === '#') continue;
      const headingText = numberedHeading ? numberedHeading[2] : hash[2].replace(/^\d+\.\s*/, '');
      if (/^(Executive Summary & Core Quality Metrics|Multi-Suite Performance Breakdown|Category Breakdown)/i.test(headingText)) continue;
      const section = document.createElement('section'); section.className = 'report-section';
      const title = document.createElement('h3'); const number = numberedHeading ? numberedHeading[1] : (hash[2].match(/^(\d+)\./)?.[1] || '');
      if (number) { const eyebrow = document.createElement('span'); eyebrow.className = 'report-number'; eyebrow.textContent = number.padStart(2, '0'); section.appendChild(eyebrow); }
      else section.classList.add('report-section-plain');
      title.textContent = clean(headingText); section.appendChild(title); prose.appendChild(section); continue;
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

function setupPolicyStudio() {
  const form = document.getElementById('policy-form');
  if (!form) return;

  const list = document.getElementById('policy-list');
  const listState = document.getElementById('policy-list-state');
  const categorySelect = document.getElementById('policy-categories');
  const categoryPillsContainer = document.getElementById('policy-category-pills');
  const verifySelect = document.getElementById('verify-policy');
  const previewCategory = document.getElementById('policy-preview-category');
  const formState = document.getElementById('policy-form-state');
  let policies = [];

  const updateCategoryPillsState = () => {
    if (!categoryPillsContainer) return;
    const selected = new Set([...categorySelect.selectedOptions].map(o => o.value));
    for (const pill of categoryPillsContainer.querySelectorAll('.category-pill')) {
      const cat = pill.dataset.category;
      const isSelected = selected.has(cat);
      pill.classList.toggle('active', isSelected);
      pill.setAttribute('aria-pressed', String(isSelected));
    }
  };

  categorySelect.addEventListener('change', updateCategoryPillsState);

  const request = async (url, options = {}) => {
    const response = await fetch(url, {
      ...options,
      headers: { 'Content-Type': 'application/json', ...(options.headers || {}) }
    });
    if (!response.ok) {
      let detail = `HTTP ${response.status}`;
      try {
        const body = await response.json();
        detail = body.detail || detail;
      } catch (_) {}
      throw new Error(detail);
    }
    return response.status === 204 ? null : response.json();
  };

  const splitDomains = (id) => document.getElementById(id).value
    .split(/[\n,]+/).map(value => value.trim()).filter(Boolean);
  const parseObject = (id) => {
    const value = document.getElementById(id).value.trim();
    if (!value) return {};
    const parsed = JSON.parse(value);
    if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object') {
      throw new Error(`${id.replaceAll('-', ' ')} must be a JSON object`);
    }
    return parsed;
  };

  const payloadFromForm = () => {
    const categories = [...categorySelect.selectedOptions].map(option => option.value);
    return {
      name: document.getElementById('policy-name').value.trim(),
      description: document.getElementById('policy-description').value.trim(),
      enabled: document.getElementById('policy-enabled').checked,
      categories,
      preferred_domains: splitDomains('policy-preferred-domains'),
      allowed_domains: splitDomains('policy-allowed-domains'),
      blocked_domains: splitDomains('policy-blocked-domains'),
      minimum_source_count: Number(document.getElementById('policy-min-sources').value),
      minimum_source_authority: Number(document.getElementById('policy-min-authority').value),
      minimum_freshness_score: Number(document.getElementById('policy-min-score').value),
      maximum_evidence_age_days: document.getElementById('policy-max-age').value
        ? Number(document.getElementById('policy-max-age').value) : null,
      category_maximum_evidence_age_days: parseObject('policy-category-age'),
      category_minimum_freshness_scores: parseObject('policy-category-scores'),
      cache_ttl_seconds: parseObject('policy-cache-ttls'),
      verification_mode: document.getElementById('policy-mode').value,
      cache_enabled: document.getElementById('policy-cache-enabled').checked,
      jurisdiction: document.getElementById('policy-jurisdiction').value.trim() || null
    };
  };

  const clearForm = () => {
    form.reset();
    document.getElementById('policy-id').value = '';
    document.getElementById('policy-editor-title').textContent = 'Create a policy';
    document.getElementById('save-policy-btn').textContent = 'Save policy';
    document.getElementById('policy-enabled').checked = true;
    document.getElementById('policy-cache-enabled').checked = true;
    for (const option of categorySelect.options) option.selected = option.dataset.volatile === 'true';
    updateCategoryPillsState();
    formState.textContent = '';
    formState.classList.remove('is-error');
  };

  const fillForm = (policy) => {
    document.getElementById('policy-id').value = policy.id;
    document.getElementById('policy-name').value = policy.name;
    document.getElementById('policy-description').value = policy.description || '';
    document.getElementById('policy-enabled').checked = policy.enabled;
    for (const option of categorySelect.options) option.selected = policy.categories.includes(option.value);
    updateCategoryPillsState();
    document.getElementById('policy-allowed-domains').value = (policy.allowed_domains || []).join('\n');
    document.getElementById('policy-blocked-domains').value = (policy.blocked_domains || []).join('\n');
    document.getElementById('policy-preferred-domains').value = (policy.preferred_domains || []).join('\n');
    document.getElementById('policy-jurisdiction').value = policy.jurisdiction || '';
    document.getElementById('policy-min-sources').value = policy.minimum_source_count;
    document.getElementById('policy-min-authority').value = policy.minimum_source_authority;
    document.getElementById('policy-min-score').value = policy.minimum_freshness_score;
    document.getElementById('policy-max-age').value = policy.maximum_evidence_age_days ?? '';
    document.getElementById('policy-category-age').value = JSON.stringify(policy.category_maximum_evidence_age_days || {});
    document.getElementById('policy-category-scores').value = JSON.stringify(policy.category_minimum_freshness_scores || {});
    document.getElementById('policy-cache-ttls').value = JSON.stringify(policy.cache_ttl_seconds || {});
    document.getElementById('policy-mode').value = policy.verification_mode;
    document.getElementById('policy-cache-enabled').checked = policy.cache_enabled;
    document.getElementById('policy-editor-title').textContent = 'Edit policy';
    document.getElementById('save-policy-btn').textContent = 'Update policy';
    formState.textContent = '';
    formState.classList.remove('is-error');
    document.getElementById('policy-editor-title').scrollIntoView({ behavior: 'smooth', block: 'start' });
  };

  const renderPolicyList = () => {
    clearElement(list);
    listState.textContent = policies.length ? `${policies.length} ${policies.length === 1 ? 'policy' : 'policies'}` : 'No policies yet. Create one to customize verification.';
    for (const policy of policies) {
      const card = document.createElement('article');
      card.className = 'policy-list-item';
      const heading = document.createElement('div');
      heading.className = 'policy-list-heading';
      const title = document.createElement('h3');
      title.textContent = policy.name;
      const state = document.createElement('span');
      state.className = policy.enabled ? 'policy-enabled' : 'policy-disabled';
      state.textContent = policy.enabled ? 'ENABLED' : 'DISABLED';
      heading.append(title, state);
      const description = document.createElement('p');
      description.className = 'muted-copy';
      description.textContent = policy.description || 'No description';
      const meta = document.createElement('p');
      meta.className = 'policy-list-meta';
      meta.textContent = `${policy.categories.length} categories · ${policy.verification_mode} · score ≥ ${policy.minimum_freshness_score}`;
      const actions = document.createElement('div');
      actions.className = 'policy-list-actions';
      for (const [action, label] of [['edit', 'Edit'], ['toggle', policy.enabled ? 'Disable' : 'Enable'], ['duplicate', 'Duplicate'], ['delete', 'Delete']]) {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = action === 'delete' ? 'secondary policy-delete' : 'secondary';
        button.dataset.action = action;
        button.dataset.policyId = policy.id;
        button.textContent = label;
        actions.appendChild(button);
      }
      card.append(heading, description, meta, actions);
      list.appendChild(card);
    }
  };

  const loadPolicies = async () => {
    try {
      const data = await request('/api/policies?limit=100');
      policies = data.policies || [];
      renderPolicyList();
      const selectedId = verifySelect.value;
      clearElement(verifySelect);
      const defaultOption = document.createElement('option');
      defaultOption.value = '';
      defaultOption.textContent = 'Default FreshGate behavior';
      verifySelect.appendChild(defaultOption);
      for (const policy of policies.filter(item => item.enabled)) {
        const option = document.createElement('option');
        option.value = policy.id;
        option.textContent = policy.name;
        verifySelect.appendChild(option);
      }
      if ([...verifySelect.options].some(option => option.value === selectedId)) verifySelect.value = selectedId;
    } catch (err) {
      listState.textContent = `Could not load policies: ${err.message}`;
    }
  };

  request('/api/policies/categories').then(data => {
    clearElement(categorySelect);
    clearElement(previewCategory);
    if (categoryPillsContainer) clearElement(categoryPillsContainer);
    for (const category of data.categories || []) {
      const option = document.createElement('option');
      option.value = category.name;
      option.textContent = `${category.name.replaceAll('_', ' ')}${category.volatile ? '' : ' (stable)'}`;
      option.dataset.volatile = String(category.volatile);
      categorySelect.appendChild(option);

      if (categoryPillsContainer) {
        const pill = document.createElement('button');
        pill.type = 'button';
        pill.className = 'category-pill';
        pill.dataset.category = category.name;
        pill.textContent = category.name.replaceAll('_', ' ');
        pill.addEventListener('click', () => {
          option.selected = !option.selected;
          updateCategoryPillsState();
          categorySelect.dispatchEvent(new Event('change'));
          if (categorySelect.selectedOptions.length) {
            formState.textContent = '';
            formState.classList.remove('is-error');
          }
        });
        categoryPillsContainer.appendChild(pill);
      }

      const previewOption = document.createElement('option');
      previewOption.value = category.name;
      previewOption.textContent = category.name.replaceAll('_', ' ');
      previewCategory.appendChild(previewOption);
    }
    clearForm();
  }).catch(err => { listState.textContent = `Could not load categories: ${err.message}`; });

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (!categorySelect.selectedOptions.length) {
      formState.textContent = 'Choose at least one claim category to save this policy.';
      formState.classList.add('is-error');
      categoryPillsContainer.querySelector('.category-pill')?.focus();
      return;
    }
    const saveButton = document.getElementById('save-policy-btn');
    saveButton.disabled = true;
    formState.textContent = 'Validating policy…';
    try {
      const payload = payloadFromForm();
      const policyId = document.getElementById('policy-id').value;
      const validation = await request(policyId ? `/api/policies/${encodeURIComponent(policyId)}/validate` : '/api/policies/validate', {
        method: 'POST', body: JSON.stringify(payload)
      });
      const saved = await request(policyId ? `/api/policies/${encodeURIComponent(policyId)}` : '/api/policies', {
        method: policyId ? 'PUT' : 'POST', body: JSON.stringify(payload)
      });
      formState.textContent = validation.warnings?.length
        ? `Saved with note: ${validation.warnings[0].message}` : 'Policy saved.';
      await loadPolicies();
      document.getElementById('policy-id').value = saved.id;
      document.getElementById('policy-editor-title').textContent = 'Edit policy';
      document.getElementById('save-policy-btn').textContent = 'Update policy';
    } catch (err) {
      formState.textContent = `Could not save policy: ${err.message}`;
      formState.classList.add('is-error');
    } finally {
      saveButton.disabled = false;
    }
  });

  list.addEventListener('click', async (event) => {
    const button = event.target.closest('button[data-action]');
    if (!button) return;
    const policy = policies.find(item => item.id === button.dataset.policyId);
    if (!policy) return;
    try {
      if (button.dataset.action === 'edit') fillForm(policy);
      if (button.dataset.action === 'delete') {
        if (!window.confirm(`Delete policy “${policy.name}”?`)) return;
        await request(`/api/policies/${encodeURIComponent(policy.id)}`, { method: 'DELETE' });
        if (document.getElementById('policy-id').value === policy.id) clearForm();
        await loadPolicies();
      }
      if (button.dataset.action === 'duplicate') {
        await request(`/api/policies/${encodeURIComponent(policy.id)}/duplicate`, { method: 'POST' });
        await loadPolicies();
      }
      if (button.dataset.action === 'toggle') {
        const payload = {
          name: policy.name, description: policy.description, enabled: !policy.enabled,
          categories: policy.categories, preferred_domains: policy.preferred_domains,
          allowed_domains: policy.allowed_domains, blocked_domains: policy.blocked_domains,
          minimum_source_count: policy.minimum_source_count,
          minimum_source_authority: policy.minimum_source_authority,
          minimum_freshness_score: policy.minimum_freshness_score,
          maximum_evidence_age_days: policy.maximum_evidence_age_days,
          category_maximum_evidence_age_days: policy.category_maximum_evidence_age_days,
          category_minimum_freshness_scores: policy.category_minimum_freshness_scores,
          verification_mode: policy.verification_mode, cache_enabled: policy.cache_enabled,
          cache_ttl_seconds: policy.cache_ttl_seconds, jurisdiction: policy.jurisdiction
        };
        await request(`/api/policies/${encodeURIComponent(policy.id)}`, { method: 'PUT', body: JSON.stringify(payload) });
        await loadPolicies();
      }
    } catch (err) {
      listState.textContent = `Policy action failed: ${err.message}`;
    }
  });

  document.getElementById('new-policy-btn').addEventListener('click', clearForm);
  document.getElementById('cancel-policy-btn').addEventListener('click', clearForm);
  document.getElementById('run-policy-preview-btn').addEventListener('click', async () => {
    const policyId = document.getElementById('policy-id').value || verifySelect.value;
    const output = document.getElementById('policy-preview-result');
    if (!policyId) {
      output.textContent = 'Save or select a policy before running its simulation.';
      return;
    }
    const domain = document.getElementById('policy-preview-domain').value.trim();
    const sample = {
      id: 'sample-claim',
      text: document.getElementById('policy-preview-text').value.trim(),
      category: previewCategory.value,
      freshness_score: Number(document.getElementById('policy-preview-score').value),
      sources: domain ? [{
        url: `https://${domain}/`, domain,
        snippet: 'Sample evidence supplied for policy simulation.',
        published: new Date().toISOString(), authority_score: 0.95,
        is_primary: true, relevance_score: 1
      }] : []
    };
    try {
      const result = await request(`/api/policies/${encodeURIComponent(policyId)}/preview`, {
        method: 'POST', body: JSON.stringify({ claims: [sample] })
      });
      output.textContent = JSON.stringify(result, null, 2);
    } catch (err) {
      output.textContent = `Preview failed: ${err.message}`;
    }
  });

  clearForm();
  loadPolicies();
}

function setupWatchtower() {
  const claims = document.getElementById('watchtower-claims');
  if (!claims) return;
  const claimsState = document.getElementById('watchtower-claims-state');
  const alerts = document.getElementById('watchtower-alerts');
  const alertsState = document.getElementById('watchtower-alerts-state');
  const overview = document.getElementById('watchtower-overview');
  const detailCard = document.getElementById('watchtower-detail-card');
  const detail = document.getElementById('watchtower-detail');
  const detailTitle = document.getElementById('watchtower-detail-title');

  const request = async (url, options = {}) => {
    const response = await fetch(url, {
      ...options,
      headers: { 'Content-Type': 'application/json', ...(options.headers || {}) }
    });
    if (!response.ok) {
      let message = `HTTP ${response.status}`;
      try { message = (await response.json()).detail || message; } catch (_) {}
      throw new Error(message);
    }
    return response.status === 204 ? null : response.json();
  };
  const dateLabel = value => value ? new Date(value * 1000).toLocaleString() : 'Not checked yet';
  const makeAction = (label, action, id, secondary = true) => {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = secondary ? 'secondary' : '';
    button.dataset.action = action;
    button.dataset.id = id;
    button.textContent = label;
    return button;
  };

  const load = async () => {
    try {
      const [claimData, alertData] = await Promise.all([
        request('/api/watchtower/claims?limit=100'),
        request('/api/watchtower/alerts?limit=100')
      ]);
      clearElement(claims);
      clearElement(alerts);
      clearElement(overview);
      const totals = claimData.overview || {};
      for (const [label, value] of [['Monitored', totals.total], ['Healthy', totals.healthy], ['Changed', totals.changed], ['Pending', totals.pending], ['Failed', totals.failed]]) {
        const stat = document.createElement('div');
        stat.className = 'watchtower-stat';
        const name = document.createElement('span'); name.textContent = label;
        const amount = document.createElement('strong'); amount.textContent = String(value ?? 0);
        stat.append(name, amount); overview.appendChild(stat);
      }
      claimsState.textContent = claimData.claims.length ? `${claimData.claims.length} monitored claim${claimData.claims.length === 1 ? '' : 's'}` : 'No monitored claims. Verify a claim, then choose “Monitor this claim.”';
      for (const item of claimData.claims) {
        const card = document.createElement('article'); card.className = 'watchtower-claim';
        const title = document.createElement('h3'); title.textContent = item.claim_text;
        const meta = document.createElement('p'); meta.className = 'watchtower-meta';
        meta.textContent = `${item.category} · ${item.last_verdict || 'baseline needed'} · score ${item.last_score ?? '—'} · last attempt ${dateLabel(item.last_checked_at)} · last success ${dateLabel(item.last_successful_at)} · next ${dateLabel(item.next_check_at)} · ${item.enabled ? 'enabled' : 'paused'}`;
        const actions = document.createElement('div'); actions.className = 'watchtower-actions';
        actions.append(
          makeAction('Check now', 'check', item.id, false),
          makeAction('Details', 'details', item.id),
          makeAction('Replay', 'replay', item.claim_fingerprint),
          makeAction(item.enabled ? 'Pause' : 'Resume', 'toggle', item.id),
          makeAction('Remove', 'delete', item.id)
        );
        card.append(title, meta, actions); claims.appendChild(card);
      }
      alertsState.textContent = alertData.alerts.length ? `${alertData.alerts.length} alert${alertData.alerts.length === 1 ? '' : 's'}` : 'No detected changes.';
      for (const item of alertData.alerts) {
        const card = document.createElement('article'); card.className = 'watchtower-alert';
        const title = document.createElement('h3'); title.textContent = item.reason;
        const transition = document.createElement('p'); transition.textContent = `${item.previous_verdict || '—'} → ${item.current_verdict || '—'} · score ${item.previous_score ?? '—'} → ${item.current_score ?? '—'} · ${dateLabel(item.created_at)}`;
        const actions = document.createElement('div'); actions.className = 'watchtower-actions';
        const fingerprint = item.metadata?.claim_fingerprint;
        if (fingerprint) actions.appendChild(makeAction('Open in Replay', 'replay', fingerprint));
        if (!item.acknowledged_at) actions.appendChild(makeAction('Acknowledge', 'acknowledge', item.id));
        const sources = document.createElement('div'); sources.className = 'watchtower-evidence';
        for (const [label, evidence] of [['Previous evidence', item.old_evidence], ['Current evidence', item.new_evidence]]) {
          if (!evidence?.length) continue;
          const group = document.createElement('div'); group.className = 'watchtower-evidence-group';
          const heading = document.createElement('span'); heading.textContent = `${label}: `; group.appendChild(heading);
          for (const source of evidence) {
            try {
              const url = new URL(source.url);
              if (!['http:', 'https:'].includes(url.protocol)) continue;
              const link = document.createElement('a'); link.href = url.href; link.target = '_blank'; link.rel = 'noopener noreferrer'; link.textContent = source.domain || url.hostname; group.appendChild(link);
            } catch (_) {}
          }
          sources.appendChild(group);
        }
        card.append(title, transition, sources, actions); alerts.appendChild(card);
      }
    } catch (error) {
      claimsState.textContent = `Could not load Watchtower: ${error.message}`;
      alertsState.textContent = `Could not load Watchtower: ${error.message}`;
    }
  };

  claims.addEventListener('click', async event => {
    const button = event.target.closest('button[data-action]'); if (!button) return;
    const { action, id } = button.dataset;
    try {
      if (action === 'replay') {
        window.openFreshGateReplay?.(id);
        return;
      }
      if (action === 'check') {
        button.disabled = true; button.textContent = 'Checking…';
        const outcome = await request(`/api/watchtower/claims/${encodeURIComponent(id)}/check`, { method: 'POST' });
        claimsState.textContent = outcome.status === 'completed' ? `Check finished: ${outcome.verdict}` : `Check ${outcome.status}: ${outcome.error || ''}`;
      }
      if (action === 'toggle') {
        const item = await request(`/api/watchtower/claims/${encodeURIComponent(id)}`);
        await request(`/api/watchtower/claims/${encodeURIComponent(id)}`, { method: 'PATCH', body: JSON.stringify({ enabled: !item.enabled }) });
      }
      if (action === 'delete') {
        if (!window.confirm('Remove this monitored claim? Existing alerts remain in history.')) return;
        await request(`/api/watchtower/claims/${encodeURIComponent(id)}`, { method: 'DELETE' });
      }
      if (action === 'details') {
        const item = await request(`/api/watchtower/claims/${encodeURIComponent(id)}`);
        detailTitle.textContent = item.claim_text;
        detail.textContent = JSON.stringify(item, null, 2);
        detailCard.hidden = false;
      }
      await load();
    } catch (error) { claimsState.textContent = `Watchtower action failed: ${error.message}`; }
  });

  alerts.addEventListener('click', async event => {
    const button = event.target.closest('button[data-action]'); if (!button) return;
    if (button.dataset.action === 'replay') {
      window.openFreshGateReplay?.(button.dataset.id);
      return;
    }
    if (button.dataset.action !== 'acknowledge') return;
    try { await request(`/api/watchtower/alerts/${encodeURIComponent(button.dataset.id)}/acknowledge`, { method: 'POST' }); await load(); }
    catch (error) { alertsState.textContent = `Could not acknowledge alert: ${error.message}`; }
  });
  document.getElementById('watchtower-refresh').addEventListener('click', load);
  document.addEventListener('freshgate:watchtower-refresh', load);
  document.querySelector('[data-tab="tab-watchtower"]')?.addEventListener('click', load);
  load();
}

function setupReplay() {
  const fingerprintInput = document.getElementById('replay-fingerprint');
  if (!fingerprintInput) return;
  const state = document.getElementById('replay-state');
  const timeline = document.getElementById('replay-timeline');
  const comparison = document.getElementById('replay-comparison');
  const fromSelect = document.getElementById('replay-from');
  const toSelect = document.getElementById('replay-to');
  let currentFingerprint = '';

  const request = async url => {
    const response = await fetch(url);
    if (!response.ok) {
      let message = `HTTP ${response.status}`;
      try { message = (await response.json()).detail || message; } catch (_) {}
      throw new Error(message);
    }
    return response.json();
  };
  const dateLabel = value => value ? new Date(value * 1000).toLocaleString() : 'Unknown time';
  const appendSourceLinks = (container, sources, label) => {
    if (!sources?.length) return;
    const group = document.createElement('div'); group.className = 'replay-diff';
    const heading = document.createElement('strong'); heading.textContent = label; group.appendChild(heading);
    for (const source of sources) {
      try {
        const url = new URL(source.url);
        if (!['http:', 'https:'].includes(url.protocol)) continue;
        const link = document.createElement('a'); link.href = url.href; link.target = '_blank'; link.rel = 'noopener noreferrer';
        link.textContent = `${source.domain || url.hostname}${source.published ? ` · ${source.published}` : ''}`;
        group.appendChild(link);
      } catch (_) {}
    }
    container.appendChild(group);
  };
  const renderComparison = result => {
    clearElement(comparison);
    const before = result.previous;
    const after = result.current;
    const panes = document.createElement('div'); panes.className = 'replay-panes';
    for (const [label, snapshot] of [['Previous', before], ['Current', after]]) {
      const pane = document.createElement('article'); pane.className = 'replay-pane';
      const title = document.createElement('h3'); title.textContent = `${label} · ${dateLabel(snapshot.checked_at)}`;
      const verdict = document.createElement('p'); verdict.textContent = `Verdict: ${snapshot.verdict || 'unknown'} · score: ${snapshot.freshness_score ?? '—'}`;
      const policy = document.createElement('p'); policy.textContent = `Policy: ${snapshot.metadata?.policy_name || snapshot.policy_id || 'Default'}`;
      const correction = document.createElement('p'); correction.textContent = snapshot.metadata?.correction ? `Correction: ${snapshot.metadata.correction}` : 'No correction recorded';
      const evidence = document.createElement('p'); evidence.textContent = `${(snapshot.sources || []).length} evidence source(s)`;
      pane.append(title, verdict, policy, correction, evidence);
      appendSourceLinks(pane, snapshot.sources, `${label} sources`);
      panes.appendChild(pane);
    }
    comparison.appendChild(panes);
    const diff = document.createElement('div'); diff.className = 'replay-diff';
    const title = document.createElement('strong'); title.textContent = 'Changes'; diff.appendChild(title);
    const summary = document.createElement('p');
    summary.textContent = `Verdict: ${result.verdict.previous || '—'} → ${result.verdict.current || '—'} · score change: ${result.freshness_score.delta ?? '—'} · policy changed: ${result.policy.changed ? 'yes' : 'no'} · evidence changed: ${result.evidence_changed ? 'yes' : 'no'}`;
    if (result.verdict.changed) summary.classList.add('replay-status-changed');
    diff.appendChild(summary);
    const factors = document.createElement('p');
    factors.textContent = `Source authority ${result.sources.authority.previous ?? '—'} → ${result.sources.authority.current ?? '—'} · recency ${result.sources.recency.previous ?? '—'} → ${result.sources.recency.current ?? '—'} · agreement ${result.sources.agreement.previous ?? '—'} → ${result.sources.agreement.current ?? '—'}`;
    diff.appendChild(factors);
    appendSourceLinks(diff, result.sources.added, 'Added sources');
    appendSourceLinks(diff, result.sources.removed, 'Removed sources');
    for (const source of result.sources.changed || []) {
      const changed = document.createElement('p'); changed.textContent = `Evidence changed at ${source.url}`; diff.appendChild(changed);
    }
    comparison.appendChild(diff);
  };

  const loadReplay = async (fingerprint, compareImmediately = true) => {
    currentFingerprint = fingerprint.trim();
    fingerprintInput.value = currentFingerprint;
    if (!/^[a-f0-9]{64}$/.test(currentFingerprint)) {
      state.textContent = 'Enter a valid 64-character claim fingerprint.';
      return;
    }
    state.textContent = 'Loading immutable history…';
    clearElement(timeline); clearElement(comparison); clearElement(fromSelect); clearElement(toSelect);
    try {
      const [replay, timelineData] = await Promise.all([
        request(`/api/replay/${encodeURIComponent(currentFingerprint)}?limit=100`),
        request(`/api/replay/${encodeURIComponent(currentFingerprint)}/timeline?limit=100`)
      ]);
      const snapshots = replay.snapshots || [];
      for (const snapshot of snapshots) {
        const id = snapshot.verification_id;
        const label = `${dateLabel(snapshot.checked_at)} · ${snapshot.verdict || 'unknown'} · ${snapshot.freshness_score ?? '—'}`;
        for (const select of [fromSelect, toSelect]) {
          const option = document.createElement('option'); option.value = id; option.textContent = label; select.appendChild(option);
        }
      }
      if (snapshots.length >= 2) {
        fromSelect.value = snapshots[snapshots.length - 2].verification_id;
        toSelect.value = snapshots[snapshots.length - 1].verification_id;
      }
      for (const event of timelineData.events || []) {
        const card = document.createElement('article'); card.className = `replay-event${event.event_type === 'alert' ? ' is-alert' : ''}`;
        const heading = document.createElement('h3');
        const detail = document.createElement('p');
        if (event.event_type === 'verification') {
          const snapshot = event.verification;
          heading.textContent = `${snapshot.verdict || 'Verification'} · score ${snapshot.freshness_score ?? '—'}`;
          detail.textContent = `${dateLabel(snapshot.checked_at)} · ${snapshot.metadata?.policy_name || snapshot.policy_id || 'Default policy'} · ${snapshot.cached ? 'cached' : 'live'} · ${snapshot.verification_id}`;
        } else {
          const alert = event.alert;
          heading.textContent = `Alert · ${alert.reason}`;
          detail.textContent = `${dateLabel(alert.created_at)} · ${alert.previous_verdict || '—'} → ${alert.current_verdict || '—'} · score ${alert.previous_score ?? '—'} → ${alert.current_score ?? '—'}`;
        }
        card.append(heading, detail); timeline.appendChild(card);
      }
      if (replay.latest_comparison) renderComparison(replay.latest_comparison);
      state.textContent = `${replay.total} historical event(s) for ${currentFingerprint}`;
      if (compareImmediately && snapshots.length < 2) comparison.textContent = 'At least two verification snapshots are needed for a comparison.';
    } catch (error) {
      if (error.message === 'No history found for this claim') {
        state.textContent = 'No verification history exists for this claim yet. New live verifications will appear here.';
        comparison.textContent = 'A comparison will be available after at least two verification snapshots.';
      } else {
        state.textContent = `Could not load Replay: ${error.message}`;
      }
    }
  };

  document.getElementById('load-replay-btn').addEventListener('click', () => loadReplay(fingerprintInput.value));
  fingerprintInput.addEventListener('keydown', event => {
    if (event.key === 'Enter') { event.preventDefault(); loadReplay(fingerprintInput.value); }
  });
  document.getElementById('compare-replay-btn').addEventListener('click', async () => {
    if (!currentFingerprint || !fromSelect.value || !toSelect.value) return;
    const query = new URLSearchParams({ from: fromSelect.value, to: toSelect.value });
    try { renderComparison(await request(`/api/replay/${encodeURIComponent(currentFingerprint)}/compare?${query}`)); }
    catch (error) { state.textContent = `Comparison failed: ${error.message}`; }
  });
  document.querySelector('[data-tab="tab-replay"]')?.addEventListener('click', () => {
    if (fingerprintInput.value.trim() && fingerprintInput.value.trim() !== currentFingerprint) loadReplay(fingerprintInput.value);
  });
  window.openFreshGateReplay = fingerprint => {
    document.querySelector('[data-tab="tab-replay"]')?.click();
    loadReplay(fingerprint);
  };
}

// Utility: Safe DOM Clear
function clearElement(el) {
  while (el && el.firstChild) {
    el.removeChild(el.firstChild);
  }
}
