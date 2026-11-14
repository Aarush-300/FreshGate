import { useCallback, useEffect, useLayoutEffect, useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  Activity, AlertTriangle, ArrowDownRight, ArrowUpRight, BookOpen, Check, ChevronDown,
  ChevronRight, CircleHelp, Clock3, FileClock, FlaskConical, Gauge, History, Leaf,
  LoaderCircle, LockKeyhole, Menu, Plus, RefreshCw, Search, Settings2, ShieldCheck,
  Sprout, Sun, Moon, Eye, EyeOff, X,
} from "lucide-react";
import { api, jsonBody, patchBody } from "./api";
import type {
  ApiStatus, Benchmark, Category, Claim, Health, Policy, RecordValue, Replay,
  ProviderOption, Source, VerificationResult, WatchAlert, WatchClaim,
} from "./types";

type Section = "verify" | "system" | "benchmarks" | "audits" | "policies" | "watchtower" | "replay";
type LoadState<T> = { value: T | null; error: string | null };
function initialTheme(): "light" | "dark" {
  try {
    const saved = window.localStorage.getItem("freshgate-theme");
    if (saved === "light" || saved === "dark") return saved;
  } catch {
    // Storage can be disabled by a browser privacy setting; theme switching still works in memory.
  }
  return "light";
}

const navigation: { id: Section; label: string; icon: typeof Activity; group?: string }[] = [
  { id: "verify", label: "Verify", icon: ShieldCheck },
  { id: "system", label: "System", icon: Activity, group: "Operations" },
  { id: "benchmarks", label: "Benchmarks", icon: FlaskConical },
  { id: "audits", label: "Audit log", icon: FileClock },
  { id: "policies", label: "Policy Studio", icon: Settings2, group: "Evidence" },
  { id: "watchtower", label: "Watchtower", icon: Search },
  { id: "replay", label: "Replay", icon: History },
];

const titleFor: Record<Section, string> = {
  verify: "Verify a response", system: "System health", benchmarks: "Benchmarks",
  audits: "Audit log", policies: "Policy Studio", watchtower: "Watchtower", replay: "Replay",
};

function asSection(value: string): Section {
  return navigation.some((item) => item.id === value) ? value as Section : "verify";
}

function dateTime(value?: string | number | null): string {
  if (value === null || value === undefined || value === "") return "Not recorded";
  const date = typeof value === "number" ? new Date(value * 1000) : new Date(value);
  return Number.isNaN(date.valueOf()) ? String(value) : new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(date);
}

function display(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (typeof value === "number") return new Intl.NumberFormat().format(value);
  if (typeof value === "string") return value.replaceAll("_", " ");
  return JSON.stringify(value);
}

function tone(value?: string | null): string {
  const normalized = value?.toLowerCase().replaceAll("_", "-") ?? "unknown";
  if (["confirmed", "ready", "connected", "healthy", "closed", "ok", "completed"].some((word) => normalized.includes(word))) return "good";
  if (["contradicted", "failed", "unhealthy", "unavailable", "error", "not-ready"].some((word) => normalized.includes(word))) return "bad";
  if (["partial", "warning", "stale", "degraded", "open"].some((word) => normalized.includes(word))) return "caution";
  return "neutral";
}

function StatusMark({ value, label }: { value?: string | null; label?: string }) {
  return <span className={`status-mark ${tone(value)}`}><i aria-hidden="true" />{label ?? display(value)}</span>;
}

function InlineNotice({ children, variant = "error", onDismiss }: { children: string; variant?: "error" | "success" | "info"; onDismiss?: () => void }) {
  return <div className={`notice ${variant}`} role={variant === "error" ? "alert" : "status"}>
    {variant === "error" ? <AlertTriangle size={17} /> : variant === "success" ? <Check size={17} /> : <CircleHelp size={17} />}
    <span>{children}</span>{onDismiss && <button className="icon-button" onClick={onDismiss} aria-label="Dismiss message"><X size={16} /></button>}
  </div>;
}

function EmptyState({ title, detail, action }: { title: string; detail: string; action?: React.ReactNode }) {
  return <div className="empty-state"><span className="empty-mark"><BookOpen size={20} /></span><h3>{title}</h3><p>{detail}</p>{action}</div>;
}

function SectionHeading({ title, detail, action }: { title: string; detail?: string; action?: React.ReactNode }) {
  return <div className="section-heading"><div><h1>{title}</h1>{detail && <p>{detail}</p>}</div>{action}</div>;
}

function DataTable({ rows, columns, empty, onSelect }: {
  rows: RecordValue[]; columns: { key: string; label: string; format?: (value: unknown, row: RecordValue) => React.ReactNode }[];
  empty: string; onSelect?: (row: RecordValue) => void;
}) {
  if (!rows.length) return <EmptyState title="Nothing to show yet" detail={empty} />;
  return <div className="table-wrap"><table><thead><tr>{columns.map((col) => <th key={col.key}>{col.label}</th>)}</tr></thead>
    <tbody>{rows.map((row, index) => <tr key={String(row.id ?? row.verification_id ?? index)} onClick={() => onSelect?.(row)} onKeyDown={(event) => {
      if (onSelect && (event.key === "Enter" || event.key === " ")) { event.preventDefault(); onSelect(row); }
    }} tabIndex={onSelect ? 0 : undefined} className={onSelect ? "selectable-row" : undefined}>
      {columns.map((col) => <td key={col.key}>{col.format ? col.format(row[col.key], row) : display(row[col.key])}</td>)}
    </tr>)}</tbody></table></div>;
}

export function App() {
  const [section, setSection] = useState<Section>(() => asSection(window.location.hash.slice(1)));
  const [theme, setTheme] = useState<"light" | "dark">(initialTheme);
  const [menuOpen, setMenuOpen] = useState(false);
  const [health, setHealth] = useState<LoadState<Health>>({ value: null, error: null });
  const [readiness, setReadiness] = useState<LoadState<ApiStatus>>({ value: null, error: null });
  const [provider, setProvider] = useState<LoadState<RecordValue>>({ value: null, error: null });
  const [deployment, setDeployment] = useState<LoadState<RecordValue>>({ value: null, error: null });
  const [policies, setPolicies] = useState<Policy[]>([]);
  const [categories, setCategories] = useState<Category[]>([]);
  const [claims, setClaims] = useState<WatchClaim[]>([]);
  const [alerts, setAlerts] = useState<WatchAlert[]>([]);
  const [benchmark, setBenchmark] = useState<LoadState<Benchmark>>({ value: null, error: null });
  const [refreshing, setRefreshing] = useState(false);
  const [globalError, setGlobalError] = useState<string | null>(null);

  const refreshWorkspace = useCallback(async () => {
    setRefreshing(true);
    const results = await Promise.allSettled([
      api<Health>("/health"), api<ApiStatus>("/health/ready"),
      api<RecordValue>("/api/provider/status"), api<RecordValue>("/api/deployment/status"),
      api<{ policies: Policy[] }>("/api/policies"), api<{ categories: Category[] }>("/api/policies/categories"),
      api<{ claims: WatchClaim[] }>("/api/watchtower/claims"), api<{ alerts: WatchAlert[] }>("/api/watchtower/alerts?unacknowledged_only=true"),
    ]);
    const [h, r, p, d, pol, cat, c, a] = results;
    if (h.status === "fulfilled") setHealth({ value: h.value, error: null }); else setHealth({ value: null, error: h.reason instanceof Error ? h.reason.message : "Could not reach FreshGate." });
    if (r.status === "fulfilled") setReadiness({ value: r.value, error: null }); else setReadiness({ value: null, error: r.reason instanceof Error ? r.reason.message : "Readiness could not be loaded." });
    if (p.status === "fulfilled") setProvider({ value: p.value, error: null }); else setProvider({ value: null, error: p.reason instanceof Error ? p.reason.message : "Provider status could not be loaded." });
    if (d.status === "fulfilled") setDeployment({ value: d.value, error: null }); else setDeployment({ value: null, error: d.reason instanceof Error ? d.reason.message : "Deployment settings could not be loaded." });
    if (pol.status === "fulfilled") setPolicies(pol.value.policies); else setGlobalError(pol.reason instanceof Error ? pol.reason.message : "Policies could not be loaded.");
    if (cat.status === "fulfilled") setCategories(cat.value.categories); else setGlobalError(cat.reason instanceof Error ? cat.reason.message : "Categories could not be loaded.");
    if (c.status === "fulfilled") setClaims(c.value.claims); else setGlobalError(c.reason instanceof Error ? c.reason.message : "Watchtower claims could not be loaded.");
    if (a.status === "fulfilled") setAlerts(a.value.alerts); else setGlobalError(a.reason instanceof Error ? a.reason.message : "Watchtower alerts could not be loaded.");
    setRefreshing(false);
  }, []);

  useEffect(() => {
    const sync = () => setSection(asSection(window.location.hash.slice(1)));
    window.addEventListener("hashchange", sync);
    void refreshWorkspace();
    const timer = window.setInterval(() => void refreshWorkspace(), 45_000);
    return () => { window.removeEventListener("hashchange", sync); window.clearInterval(timer); };
  }, [refreshWorkspace]);

  useEffect(() => { document.title = `FreshGate · ${titleFor[section]}`; }, [section]);

  useLayoutEffect(() => {
    document.documentElement.dataset.theme = theme;
    document.documentElement.style.colorScheme = theme;
    try { window.localStorage.setItem("freshgate-theme", theme); } catch {
      // The active theme remains set for this session when browser storage is unavailable.
    }
  }, [theme]);

  const navigate = (next: Section) => {
    window.location.hash = next;
    setSection(next);
    setMenuOpen(false);
    setGlobalError(null);
  };

  const openAlerts = alerts.length;

  return <div className="app-shell">
    <button className={`mobile-scrim ${menuOpen ? "visible" : ""}`} aria-label="Close navigation" onClick={() => setMenuOpen(false)} />
    <aside className={`sidebar ${menuOpen ? "open" : ""}`}>
      <a className="brand" href="#verify" onClick={() => navigate("verify")} aria-label="FreshGate home">
        <span className="brand-mark"><Sprout size={20} strokeWidth={1.8} /></span><span>FreshGate</span>
      </a>
      <div className="workspace-label">VERIFICATION WORKSPACE</div>
      <nav aria-label="Main navigation" className="primary-nav">
        {navigation.map((item, index) => {
          const Icon = item.icon;
          return <div key={item.id}>
            {item.group && <div className="nav-group">{item.group}</div>}
            <a href={`#${item.id}`} onClick={(event) => { event.preventDefault(); navigate(item.id); }} className={`nav-link ${section === item.id ? "active" : ""}`} aria-current={section === item.id ? "page" : undefined}>
              <Icon size={17} strokeWidth={1.8} /><span>{item.label}</span>
              {item.id === "watchtower" && openAlerts > 0 && <span className="nav-count" aria-label={`${openAlerts} unacknowledged alerts`}>{openAlerts > 99 ? "99+" : openAlerts}</span>}
              {section === item.id && <ChevronRight size={15} className="nav-chevron" />}
            </a>
            {index === 0 && <div className="nav-divider" />}
          </div>;
        })}
      </nav>
      <div className="sidebar-bottom">
        <div className="sidebar-foot"><Leaf size={14} /> Current evidence, clearly noted.</div>
      </div>
    </aside>

    <div className="main-shell">
      <header className="topbar">
        <button className="icon-button mobile-menu" aria-label="Open navigation" onClick={() => setMenuOpen(true)}><Menu size={19} /></button>
        <div className="breadcrumb"><span>FreshGate</span><ChevronRight size={14} /><strong>{titleFor[section]}</strong></div>
        <div className="topbar-actions">
          <button className="theme-switch" type="button" role="switch" aria-checked={theme === "dark"} aria-label="Dark mode" title={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"} onClick={() => setTheme((current) => current === "dark" ? "light" : "dark")}>
            <span className="theme-switch-icon" aria-hidden="true">{theme === "dark" ? <Moon size={14} /> : <Sun size={14} />}</span>
            <span className="theme-switch-label">Dark mode</span>
            <span className="theme-switch-track" aria-hidden="true"><i /></span>
          </button>
          <button className="icon-button" title="Refresh workspace" aria-label="Refresh workspace" onClick={() => void refreshWorkspace()}><RefreshCw size={16} className={refreshing ? "spin" : ""} /></button>
        </div>
      </header>
      <main className="main-content" key={section}>
        {globalError && <InlineNotice onDismiss={() => setGlobalError(null)}>{globalError}</InlineNotice>}
        {section === "verify" && <VerifyView policies={policies} onMonitor={async (claim, result) => {
          await api("/api/watchtower/monitor", jsonBody({
            claim_text: claim.text,
            category: claim.type,
            ...(result ? { baseline: { verdict: claim.verdict ?? "unverifiable", score: claim.score ?? undefined, sources: claim.sources ?? [] } } : {}),
          }));
          await refreshWorkspace();
        }} />}
        {section === "system" && <SystemView health={health} readiness={readiness} provider={provider} deployment={deployment} refreshing={refreshing} onRefresh={() => void refreshWorkspace()} />}
        {section === "benchmarks" && <BenchmarkView data={benchmark} setData={setBenchmark} />}
        {section === "audits" && <AuditView />}
        {section === "policies" && <PolicyView policies={policies} categories={categories} onChanged={refreshWorkspace} />}
        {section === "watchtower" && <WatchtowerView claims={claims} alerts={alerts} onRefresh={refreshWorkspace} onError={setGlobalError} />}
        {section === "replay" && <ReplayView claims={claims} />}
      </main>
      <footer className="app-footer"><span>FreshGate</span><span>Verification decisions stay attached to their evidence.</span></footer>
    </div>
  </div>;
}

function VerifyView({ policies, onMonitor }: { policies: Policy[]; onMonitor: (claim: Claim, result: VerificationResult) => Promise<void> }) {
  const [answer, setAnswer] = useState("");
  const [policyId, setPolicyId] = useState("");
  const [providers, setProviders] = useState<ProviderOption[]>([]);
  const [providerId, setProviderId] = useState("");
  const [model, setModel] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [llmKey, setLlmKey] = useState("");
  const [showLlmKey, setShowLlmKey] = useState(false);
  const [searchKey, setSearchKey] = useState("");
  const [result, setResult] = useState<VerificationResult | null>(null);
  const [selectedClaimId, setSelectedClaimId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const activeProvider = providers.find((providerOption) => providerOption.id === providerId) ?? null;
  const selectedClaim = result?.claims.find((claim) => claim.id === selectedClaimId) ?? result?.claims[0] ?? null;

  useEffect(() => {
    let active = true;
    api<{ providers: ProviderOption[] }>("/api/providers")
      .then(({ providers: items }) => {
        if (!active) return;
        setProviders(items);
        const defaultProvider = items[0];
        if (defaultProvider) {
          setProviderId(defaultProvider.id);
          setBaseUrl(defaultProvider.base_url);
          setModel(defaultProvider.default_model);
        }
      })
      .catch((cause) => {
        if (active) setError(cause instanceof Error ? cause.message : "Provider choices could not be loaded. Refresh and try again.");
      });
    return () => { active = false; };
  }, []);

  const chooseProvider = (nextProviderId: string) => {
    const nextProvider = providers.find((item) => item.id === nextProviderId);
    setProviderId(nextProviderId);
    setBaseUrl(nextProvider?.base_url ?? "");
    setModel(nextProvider?.default_model ?? "");
    setLlmKey("");
    setShowLlmKey(false);
  };

  const verify = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!answer.trim()) { setError("Paste a response before starting a check."); return; }
    if (!providerId || !llmKey.trim() || !baseUrl.trim() || !model.trim()) {
      setError("Choose a provider and enter its API key, base URL, and model before verifying.");
      return;
    }
    try {
      const parsedBaseUrl = new URL(baseUrl.trim());
      if (!["http:", "https:"].includes(parsedBaseUrl.protocol) || parsedBaseUrl.username || parsedBaseUrl.password) {
        setError("Enter a complete HTTP or HTTPS base URL without embedded credentials.");
        return;
      }
    } catch {
      setError("Enter a complete provider base URL, including https://.");
      return;
    }
    setBusy(true); setError(null); setMessage(null); setResult(null); setSelectedClaimId(null);
    try {
      const headers: Record<string, string> = { "X-LLM-API-Key": llmKey.trim() };
      if (searchKey.trim()) headers["X-Tavily-API-Key"] = searchKey.trim();
      const body = {
        answer,
        provider: providerId,
        base_url: baseUrl.trim(),
        model: model.trim(),
        ...(policyId ? { policy_id: policyId } : {}),
      };
      const data = await api<VerificationResult>("/verify", { ...jsonBody(body), headers });
      setResult(data);
      setSelectedClaimId(data.claims[0]?.id ?? null);
      setMessage(data.summary.message ?? "Verification finished.");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Verification did not complete. Check your connection and try again.");
    } finally {
      setBusy(false);
      setLlmKey(""); setSearchKey("");
    }
  };

  const monitor = async (claim: Claim) => {
    setError(null); setMessage(null);
    try { await onMonitor(claim, result!); setMessage("Claim added to Watchtower. Scheduled checks use server-configured provider credentials."); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "Could not add this claim. Check that it belongs to a supported volatile category."); }
  };

  return <>
    <SectionHeading title="Verify a response" detail="Find time-sensitive claims and inspect the evidence behind each one." />
    <div className="verify-layout">
      <form className="folio-panel verify-form" onSubmit={(event) => void verify(event)}>
        <div className="panel-heading"><h2>Response to verify</h2><span className="panel-index">INPUT</span></div>
        <p className="panel-copy">Paste an AI response. FreshGate extracts factual claims, checks current sources, and reports what it found.</p>
        <label className="field-label" htmlFor="answer">AI response</label>
        <textarea id="answer" className="response-input" value={answer} onChange={(event) => setAnswer(event.target.value)} maxLength={50000} placeholder="Paste a response to begin verification…" aria-describedby="response-help" />
        <div id="response-help" className="field-hint"><span>The provider key is sent with this request only and cleared when it finishes.</span><span className="tabular">{new Intl.NumberFormat().format(answer.length)} / 50,000</span></div>
        <fieldset className="provider-config" disabled={busy}>
          <legend>Provider connection</legend>
          <div className="provider-grid">
            <label className="field-group"><span className="field-label">API provider</span>
              <select value={providerId} onChange={(event) => chooseProvider(event.target.value)} required>
                <option value="" disabled>Select a provider</option>
                {providers.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
              </select>
            </label>
            <label className="field-group"><span className="field-label">API key</span>
              <span className="secret-field">
                <input autoComplete="new-password" type={showLlmKey ? "text" : "password"} value={llmKey} onChange={(event) => setLlmKey(event.target.value)} placeholder={providerId === "ollama" ? "Required by this form" : "Paste the selected provider key"} required />
                <button type="button" className="icon-button" onClick={() => setShowLlmKey((shown) => !shown)} aria-label={showLlmKey ? "Hide API key" : "Show API key"} title={showLlmKey ? "Hide API key" : "Show API key"}>{showLlmKey ? <EyeOff size={15} /> : <Eye size={15} />}</button>
              </span>
            </label>
            <label className="field-group"><span className="field-label">Base URL</span>
              <input type="url" value={baseUrl} onChange={(event) => { setBaseUrl(event.target.value); if (llmKey) setLlmKey(""); }} placeholder="https://api.provider.com/v1" required />
            </label>
            <label className="field-group"><span className="field-label">Model</span>
              <input list="provider-model-options" value={model} onChange={(event) => setModel(event.target.value)} placeholder="Choose or enter a model ID" autoComplete="off" required />
              <datalist id="provider-model-options">{activeProvider?.models.map((modelId) => <option key={modelId} value={modelId} />)}</datalist>
            </label>
          </div>
          <p className="provider-hint">{activeProvider?.credential_hint ?? "Provider defaults include a base URL and model; you can edit both. Only use an endpoint you trust."}</p>
        </fieldset>
        <div className="form-row">
          <label className="field-group"><span className="field-label">Policy <span>OPTIONAL</span></span>
            <select value={policyId} onChange={(event) => setPolicyId(event.target.value)}>
              <option value="">No policy</option>{policies.filter((policy) => policy.enabled).map((policy) => <option key={policy.id} value={policy.id}>{policy.name}</option>)}
            </select>
          </label>
          <button className="primary-button" type="submit" disabled={busy || !answer.trim()}>{busy ? <><LoaderCircle size={16} className="spin" />Checking</> : <>Check response <ArrowUpRight size={16} /></>}</button>
        </div>
        <details className="request-options"><summary><Settings2 size={15} /> Search provider settings <ChevronDown size={15} /></summary>
          <p>Use this only when the server has no search provider key. The key is sent with this request and cleared afterward.</p>
          <label className="field-group"><span className="field-label">Tavily search API key <span>OPTIONAL IF CONFIGURED ON SERVER</span></span><input autoComplete="new-password" type="password" value={searchKey} onChange={(event) => setSearchKey(event.target.value)} placeholder="Use the server's Tavily configuration" /></label>
        </details>
        {busy && <div className="checking-track" aria-live="polite"><span className="track-dot" /><span>Extracting claims and checking current evidence</span><span className="track-line" /></div>}
        {error && <InlineNotice>{error}</InlineNotice>}{message && <InlineNotice variant="success">{message}</InlineNotice>}
      </form>
      <section className="route-panel" aria-label="Verification steps">
        <div className="panel-heading"><h2>Evidence path</h2><BookOpen size={17} className="panel-symbol" /></div>
        <p className="panel-copy">A steady route from response to decision.</p>
        <ol className={`evidence-route ${busy ? "is-running" : ""}`}>
          <li><span className="route-marker">A</span><div><strong>Extract claims</strong><span>Find factual statements</span></div><span className="route-note">TEXT</span></li>
          <li><span className="route-marker">B</span><div><strong>Assess freshness</strong><span>Identify facts that can change</span></div><span className="route-note">TIME</span></li>
          <li><span className="route-marker">C</span><div><strong>Check evidence</strong><span>Compare current sources</span></div><span className="route-note">SOURCES</span></li>
          <li><span className="route-marker">D</span><div><strong>Apply policy</strong><span>Use your selected rules, if any</span></div><span className="route-note">RULES</span></li>
        </ol>
        <div className="route-foot"><span className="route-legend"><i /> Verification path</span><span>Results include claim-level sources</span></div>
      </section>
    </div>
    {result && <VerificationEvidence result={result} selectedClaim={selectedClaim} onSelect={setSelectedClaimId} onMonitor={(claim) => void monitor(claim)} />}
  </>;
}

function VerificationEvidence({ result, selectedClaim, onSelect, onMonitor }: {
  result: VerificationResult; selectedClaim: Claim | null; onSelect: (id: string) => void; onMonitor: (claim: Claim) => void;
}) {
  const summary = result.summary;
  return <section className="results-section">
    <div className="results-top"><h2>Findings</h2><StatusMark value={result.status} /></div>
    <div className="result-summary">
      <div><span className="summary-label">Claims found</span><strong className="serif-number">{summary.total}</strong></div>
      <div><span className="summary-label">Checked</span><strong className="serif-number">{summary.checked}</strong></div>
      <div><span className="summary-label">Stale</span><strong className="serif-number">{summary.stale}</strong></div>
      <div><span className="summary-label">Mean score</span><strong className="serif-number">{summary.avg_score ?? "—"}</strong></div>
      <p>{summary.message ?? "Claim findings are shown below."}</p>
    </div>
    {result.error && <InlineNotice>{result.error}</InlineNotice>}
    {!result.claims.length ? <EmptyState title="No changing claims found" detail={summary.message ?? "This response did not contain volatile factual claims."} /> :
      <div className="evidence-results">
        <div className="claims-table">
          <div className="table-title"><div><h3>Claims</h3><span>Choose a row to inspect its source notes.</span></div><span className="table-count">{result.claims.length} {result.claims.length === 1 ? "claim" : "claims"}</span></div>
          <div className="table-wrap"><table><thead><tr><th>Statement</th><th>Category</th><th>Verdict</th><th>Score</th></tr></thead>
            <tbody>{result.claims.map((claim) => <tr key={claim.id} tabIndex={0} aria-selected={selectedClaim?.id === claim.id} onKeyDown={(event) => {
              if (event.key === "Enter" || event.key === " ") { event.preventDefault(); onSelect(claim.id); }
            }} onClick={() => onSelect(claim.id)} className={`selectable-row ${selectedClaim?.id === claim.id ? "chosen" : ""}`}>
              <td className="claim-text">{claim.text}</td><td>{display(claim.type)}</td><td><StatusMark value={claim.verdict} /></td><td className="tabular">{claim.score ?? "—"}</td>
            </tr>)}</tbody></table></div>
        </div>
        {selectedClaim && <ClaimFolio claim={selectedClaim} onMonitor={() => onMonitor(selectedClaim)} />}
      </div>}
  </section>;
}

function ClaimFolio({ claim, onMonitor }: { claim: Claim; onMonitor: () => void }) {
  const sources = claim.sources ?? [];
  return <section className="claim-folio">
    <div className="folio-margin"><span className="folio-caption">SELECTED CLAIM</span><span className="folio-id">{claim.id.slice(0, 8)}</span></div>
    <div className="folio-main"><div className="claim-folio-head"><h3>{claim.text}</h3><button className="text-button" onClick={onMonitor}><Clock3 size={15} /> Monitor claim</button></div>
      <div className="claim-meta"><span>{display(claim.type)}</span><StatusMark value={claim.verdict} /><span>Score {claim.score ?? "—"}</span><span>Checked {dateTime(claim.verification_timestamp)}</span></div>
      {claim.explanation && <p className="claim-explanation">{claim.explanation}</p>}
      <div className="source-notes"><div className="notes-heading"><h4>Source notes</h4><span>{sources.length ? `${sources.length} ${sources.length === 1 ? "source" : "sources"}` : "No sources returned"}</span></div>
        {sources.length ? sources.map((source, index) => <SourceNote key={`${source.url}-${index}`} source={source} index={index + 1} />) : <p className="muted-line">The verification returned no source citations for this claim.</p>}
      </div>
    </div>
  </section>;
}

function SourceNote({ source, index }: { source: Source; index: number }) {
  return <article className="source-note" style={{ animationDelay: `${Math.min(index - 1, 5) * 70}ms` }}>
    <span className="source-number">{new Intl.NumberFormat(undefined, { minimumIntegerDigits: 2, useGrouping: false }).format(index)}</span>
    <div className="source-body"><a href={source.url} target="_blank" rel="noreferrer">{source.domain || source.url}<ArrowUpRight size={13} /></a>
      <p>{source.snippet || "No source excerpt was returned."}</p>
      <div className="source-meta"><span>{source.is_primary ? "Primary source" : "Evidence source"}</span><span>Published {dateTime(source.published)}</span>{source.authority_score !== undefined && <span>Authority {new Intl.NumberFormat(undefined, { style: "percent", maximumFractionDigits: 0 }).format(source.authority_score)}</span>}</div>
    </div>
  </article>;
}

function SystemView({ health, readiness, provider, deployment, refreshing, onRefresh }: {
  health: LoadState<Health>; readiness: LoadState<ApiStatus>; provider: LoadState<RecordValue>; deployment: LoadState<RecordValue>; refreshing: boolean; onRefresh: () => void;
}) {
  const providerValue = provider.value ?? {};
  const breakers = (providerValue.circuit_breakers ?? {}) as RecordValue;
  const cache = (providerValue.cache ?? {}) as RecordValue;
  const config = (providerValue.config ?? {}) as RecordValue;
  const summary = (providerValue.metrics ?? {}) as RecordValue;
  const entries = [
    { label: "Service", value: health.value?.status, detail: health.value?.service ?? "FreshGate API", error: health.error },
    { label: "Database", value: readiness.value?.database, detail: readiness.value?.status ?? "Waiting for response", error: readiness.error },
    { label: "Cache", value: readiness.value?.cache ?? display(cache.status), detail: "Readiness probe", error: readiness.error },
    { label: "Circuit breaker", value: display((deployment.value?.circuit_breaker as RecordValue | undefined)?.state ?? (deployment.value?.circuit_breaker as RecordValue | undefined)?.status), detail: `Mode ${display(deployment.value?.mode)}`, error: deployment.error },
  ];
  return <>
    <SectionHeading title="System health" detail="Service readiness, provider connections, and runtime state reported by FreshGate." action={<button className="secondary-button" onClick={onRefresh} disabled={refreshing}><RefreshCw size={15} className={refreshing ? "spin" : ""} /> Refresh</button>} />
    <div className="health-ledger">
      {entries.map((entry) => <div className="health-row" key={entry.label}><span className="health-label">{entry.label}</span><div>{entry.error ? <StatusMark value="unavailable" label="Unavailable" /> : <StatusMark value={entry.value} />}</div><span className="health-detail">{entry.error ?? entry.detail}</span></div>)}
    </div>
    <div className="system-columns">
      <section className="folio-panel data-panel"><div className="panel-heading"><h2>Provider status</h2><Activity size={18} className="panel-symbol" /></div>
        {provider.error ? <InlineNotice>{provider.error}</InlineNotice> : provider.value ? <DataExplorer value={{ openai: providerValue.openai ?? providerValue.llm, search: providerValue.tavily, circuit_breakers: breakers }} /> : <LoadingLine />}</section>
      <section className="folio-panel data-panel"><div className="panel-heading"><h2>Cache & configuration</h2><Settings2 size={18} className="panel-symbol" /></div>
        {provider.error ? <InlineNotice>{provider.error}</InlineNotice> : provider.value ? <DataExplorer value={{ cache, deployment: deployment.value, configuration: config, summary }} /> : <LoadingLine />}</section>
    </div>
    <section className="folio-panel metrics-panel"><div className="panel-heading"><h2>Service metrics</h2><Gauge size={18} className="panel-symbol" /></div>
      <MetricsExplorer /></section>
  </>;
}

function LoadingLine() { return <div className="loading-line"><LoaderCircle size={16} className="spin" />Waiting for service data…</div>; }

function DataExplorer({ value }: { value: Record<string, unknown> }) {
  const entries = Object.entries(value).filter(([, item]) => item !== undefined && item !== null);
  if (!entries.length) return <p className="muted-line">No runtime details were returned.</p>;
  return <dl className="data-explorer">{entries.map(([key, item]) => <div className="data-row" key={key}><dt>{key.replaceAll("_", " ")}</dt><dd>{item && typeof item === "object" ? <details><summary>Inspect values <ChevronDown size={13} /></summary><pre>{JSON.stringify(item, null, 2)}</pre></details> : display(item)}</dd></div>)}</dl>;
}

function MetricsExplorer() {
  const [text, setText] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  useEffect(() => { void api<string>("/metrics").then(setText).catch((cause) => setError(cause instanceof Error ? cause.message : "Metrics could not be loaded.")); }, []);
  const rows = useMemo(() => (text ?? "").split("\n").filter((line) => line.trim() && !line.startsWith("#")).map((line) => {
    const splitAt = line.lastIndexOf(" ");
    return { name: line.slice(0, splitAt).replace(/\{.*\}$/, ""), labels: line.slice(0, splitAt).match(/\{.*\}/)?.[0] ?? "", value: line.slice(splitAt + 1) };
  }).filter((row) => row.name.toLowerCase().includes(query.toLowerCase()) || row.labels.toLowerCase().includes(query.toLowerCase())), [text, query]);
  return <>{error ? <InlineNotice>{error}</InlineNotice> : <>
    <label className="search-field"><Search size={16} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Filter returned metrics" aria-label="Filter metrics" /></label>
    <DataTable rows={rows} columns={[{ key: "name", label: "Metric" }, { key: "labels", label: "Labels" }, { key: "value", label: "Value" }]} empty={text === null ? "Loading metrics from /metrics…" : "No metrics match this filter."} />
  </>}</>;
}

function BenchmarkView({ data, setData }: { data: LoadState<Benchmark>; setData: (value: LoadState<Benchmark>) => void }) {
  const [loading, setLoading] = useState(false);
  const load = useCallback(async () => {
    setLoading(true);
    try { setData({ value: await api<Benchmark>("/api/benchmark"), error: null }); }
    catch (cause) { setData({ value: null, error: cause instanceof Error ? cause.message : "Benchmark data could not be loaded." }); }
    finally { setLoading(false); }
  }, [setData]);
  useEffect(() => { if (!data.value && !data.error) void load(); }, [data.value, data.error, load]);
  return <>
    <SectionHeading title="Benchmarks" detail="Evaluation results computed from the dataset currently served by FreshGate." action={<button className="secondary-button" onClick={() => void load()} disabled={loading}><RefreshCw size={15} className={loading ? "spin" : ""} /> Refresh</button>} />
    {data.error && <InlineNotice onDismiss={() => setData({ value: null, error: null })}>{data.error}</InlineNotice>}
    {data.value ? <><p className="source-caption">Dataset totals returned by <code>/api/benchmark</code>. These describe the evaluation dataset, not live traffic or a production guarantee.</p>
      <div className="result-summary benchmark-summary">
        <div><span className="summary-label">Dataset items</span><strong className="serif-number">{data.value.total_items}</strong></div>
        <div><span className="summary-label">Contradicted</span><strong className="serif-number">{data.value.stale_items}</strong></div>
        <div><span className="summary-label">Confirmed</span><strong className="serif-number">{data.value.control_items}</strong></div>
        <div><span className="summary-label">Partial or unverifiable</span><strong className="serif-number">{data.value.partial_or_unverifiable_items}</strong></div>
      </div>
      <section className="folio-panel markdown-panel"><div className="panel-heading"><h2>Benchmark notes</h2><FlaskConical size={18} className="panel-symbol" /></div>
        <div className="markdown-body"><ReactMarkdown remarkPlugins={[remarkGfm]}>{data.value.results_md}</ReactMarkdown></div>
      </section>
    </> : !data.error ? <LoadingLine /> : null}
  </>;
}

function AuditView() {
  const [key, setKey] = useState("");
  const [audits, setAudits] = useState<RecordValue[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [query, setQuery] = useState("");
  const load = async (event?: React.FormEvent) => {
    event?.preventDefault(); setBusy(true); setError(null); setAudits(null);
    try { const data = await api<{ audits: RecordValue[] }>("/api/audits/recent", { headers: key ? { "X-Admin-Key": key } : {} }); setAudits(data.audits); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "Audit records could not be loaded."); }
    finally { setBusy(false); setKey(""); }
  };
  const filtered = (audits ?? []).filter((row) => JSON.stringify(row).toLowerCase().includes(query.toLowerCase()));
  return <>
    <SectionHeading title="Audit log" detail="Recent immutable verification records. Access may require the configured admin key." />
    <form className="access-strip" onSubmit={(event) => void load(event)}><div className="access-icon"><LockKeyhole size={17} /></div><div className="access-copy"><strong>Protected audit access</strong><span>Enter the admin key for this request. It is held in memory only and cleared after the request.</span></div><input type="password" autoComplete="off" value={key} onChange={(event) => setKey(event.target.value)} aria-label="Admin key" placeholder="Admin key" /><button className="secondary-button" disabled={busy}>{busy ? <LoaderCircle size={15} className="spin" /> : <Search size={15} />} Load records</button></form>
    {error && <InlineNotice>{error}</InlineNotice>}
    {audits && <section className="folio-panel data-panel"><div className="panel-heading"><h2>Recent verification activity</h2><span className="table-count">{filtered.length} records</span></div>
      <label className="search-field"><Search size={16} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Filter audit records" aria-label="Filter audit records" /></label>
      <DataTable rows={filtered} columns={[
        { key: "created_at", label: "Recorded", format: (value) => dateTime(value as string | number | null) },
        { key: "status", label: "Status", format: (value) => <StatusMark value={String(value ?? "unknown")} /> },
        { key: "request_id", label: "Request" }, { key: "summary", label: "Summary" },
        { key: "details", label: "Record", format: (value) => value && typeof value === "object" ? <details><summary>Inspect record <ChevronDown size={13} /></summary><pre>{JSON.stringify(value, null, 2)}</pre></details> : display(value) },
      ]} empty="No recent audit records were returned." />
    </section>}
  </>;
}

function PolicyView({ policies, categories, onChanged }: { policies: Policy[]; categories: Category[]; onChanged: () => Promise<void> }) {
  const [editing, setEditing] = useState<Policy | null | "new">(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [previewPolicy, setPreviewPolicy] = useState<Policy | null>(null);
  const save = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault(); const form = event.currentTarget; const values = new FormData(form);
    const read = (key: string) => String(values.get(key) ?? "").trim();
    const splitDomains = (key: string) => read(key).split(/[\s,]+/).filter(Boolean);
    const activeCategories = Array.from(form.querySelectorAll<HTMLInputElement>('input[name="categories"]:checked')).map((input) => input.value);
    const cacheTtl = read("cache_ttl_seconds");
    let cacheTtlSeconds: Record<string, number> | undefined;
    try { if (cacheTtl) cacheTtlSeconds = JSON.parse(cacheTtl) as Record<string, number>; }
    catch { setError("Cache TTL values must be valid JSON by category."); return; }
    const body: Record<string, unknown> = {
      name: read("name"), description: read("description"), enabled: values.get("enabled") === "on",
      ...(activeCategories.length ? { categories: activeCategories } : {}),
      preferred_domains: splitDomains("preferred_domains"), allowed_domains: splitDomains("allowed_domains"), blocked_domains: splitDomains("blocked_domains"),
      verification_mode: read("verification_mode"), cache_enabled: values.get("cache_enabled") === "on",
    };
    for (const field of ["minimum_source_count", "minimum_source_authority", "minimum_freshness_score", "maximum_evidence_age_days"] as const) {
      const raw = read(field); if (raw) body[field] = Number(raw);
    }
    if (cacheTtlSeconds) body.cache_ttl_seconds = cacheTtlSeconds;
    if (read("jurisdiction")) body.jurisdiction = read("jurisdiction");
    const policy = editing !== "new" ? editing : null;
    setBusyId(policy?.id ?? "new"); setError(null); setNotice(null);
    try {
      if (policy) await api(`/api/policies/${encodeURIComponent(policy.id)}`, { ...jsonBody(body), method: "PUT" });
      else await api("/api/policies", jsonBody(body));
      await onChanged(); setEditing(null); setNotice(policy ? "Policy updated." : "Policy created.");
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Policy could not be saved."); }
    finally { setBusyId(null); }
  };
  const remove = async (policy: Policy) => {
    if (!window.confirm(`Delete “${policy.name}”? Monitored claims that use it may need another policy.`)) return;
    setBusyId(policy.id); setError(null); setNotice(null);
    try { await api(`/api/policies/${encodeURIComponent(policy.id)}`, { method: "DELETE" }); await onChanged(); setNotice(`“${policy.name}” was removed.`); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "Policy could not be removed."); }
    finally { setBusyId(null); }
  };
  const duplicate = async (policy: Policy) => {
    setBusyId(policy.id); setError(null); setNotice(null);
    try { await api(`/api/policies/${encodeURIComponent(policy.id)}/duplicate`, { method: "POST" }); await onChanged(); setNotice(`“${policy.name}” was duplicated.`); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "Policy could not be duplicated."); }
    finally { setBusyId(null); }
  };
  const toggle = async (policy: Policy) => {
    setBusyId(policy.id); setError(null);
    const { id: _id, created_at: _created, updated_at: _updated, ...settings } = policy;
    try { await api(`/api/policies/${encodeURIComponent(policy.id)}`, { ...jsonBody({ ...settings, enabled: !policy.enabled }), method: "PUT" }); await onChanged(); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "Policy state could not be changed."); }
    finally { setBusyId(null); }
  };

  return <>
    <SectionHeading title="Policy Studio" detail="Set evidence rules that apply when a response or monitored claim is checked." action={<button className="primary-button" onClick={() => { setEditing("new"); setError(null); }}><Plus size={16} /> New policy</button>} />
    {error && <InlineNotice>{error}</InlineNotice>}{notice && <InlineNotice variant="success" onDismiss={() => setNotice(null)}>{notice}</InlineNotice>}
    {editing !== null && <PolicyEditor policy={editing === "new" ? null : editing} categories={categories} busy={busyId !== null} onCancel={() => setEditing(null)} onSubmit={(event) => void save(event)} />}
    {!policies.length ? <EmptyState title="No policies saved" detail="Create a policy to set source, freshness, verification mode, and cache rules." action={<button className="secondary-button" onClick={() => setEditing("new")}><Plus size={15} /> Create policy</button>} /> :
      <section className="policy-list"><div className="list-heading"><h2>Policies</h2><span className="table-count">{policies.length} {policies.length === 1 ? "policy" : "policies"}</span></div>
        {policies.map((policy) => <article className="policy-row" key={policy.id}>
          <div className="policy-name"><span className={`policy-state ${policy.enabled ? "enabled" : "disabled"}`} /><div><h3>{policy.name}</h3><p>{policy.description || "No description provided."}</p><span className="policy-categories">{policy.categories.map(display).join(" · ")}</span></div></div>
          <div className="policy-rules"><span>{policy.minimum_source_count} {policy.minimum_source_count === 1 ? "source" : "sources"} minimum</span><span>Freshness ≥ {policy.minimum_freshness_score}</span><span>{display(policy.verification_mode)} mode</span></div>
          <div className="policy-actions"><button className="text-button" onClick={() => void toggle(policy)} disabled={busyId === policy.id} aria-label={`${policy.enabled ? "Disable" : "Enable"} ${policy.name}`}><StatusMark value={policy.enabled ? "enabled" : "disabled"} /></button><button className="text-button" onClick={() => setPreviewPolicy(previewPolicy?.id === policy.id ? null : policy)}><FlaskConical size={14} /> Preview</button><button className="text-button" onClick={() => setEditing(policy)}><Settings2 size={15} /> Edit</button><button className="icon-button" onClick={() => void duplicate(policy)} disabled={busyId === policy.id} title="Duplicate policy" aria-label={`Duplicate ${policy.name}`}><Plus size={15} /></button><button className="icon-button danger-icon" onClick={() => void remove(policy)} disabled={busyId === policy.id} title="Delete policy" aria-label={`Delete ${policy.name}`}><X size={15} /></button></div>
          {previewPolicy?.id === policy.id && <PolicyPreview policy={policy} onClose={() => setPreviewPolicy(null)} />}
        </article>)}
      </section>}
  </>;
}

function PolicyPreview({ policy, onClose }: { policy: Policy; onClose: () => void }) {
  const [claim, setClaim] = useState("");
  const [category, setCategory] = useState(policy.categories[0] ?? "");
  const [score, setScore] = useState("");
  const [result, setResult] = useState<RecordValue | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const submit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault(); setBusy(true); setResult(null); setError(null);
    try {
      const sample = { text: claim, category, sources: [], ...(score ? { freshness_score: Number(score) } : {}) };
      setResult(await api<RecordValue>(`/api/policies/${encodeURIComponent(policy.id)}/preview`, jsonBody({ claims: [sample] })));
    } catch (cause) { setError(cause instanceof Error ? cause.message : "The preview could not be evaluated."); }
    finally { setBusy(false); }
  };
  return <section className="policy-preview"><div className="preview-heading"><div><h3>Preview “{policy.name}”</h3><p>This uses the policy preview API. It does not call live providers or create a verification record.</p></div><button className="icon-button" onClick={onClose} aria-label="Close policy preview"><X size={16} /></button></div>
    <form className="preview-form" onSubmit={(event) => void submit(event)}><label className="field-group"><span className="field-label">Sample claim</span><input required maxLength={5000} value={claim} onChange={(event) => setClaim(event.target.value)} placeholder="Enter a claim to evaluate against this policy" /></label>
      <label className="field-group"><span className="field-label">Category</span><select required value={category} onChange={(event) => setCategory(event.target.value)}>{policy.categories.map((item) => <option key={item} value={item}>{display(item)}</option>)}</select></label>
      <label className="field-group"><span className="field-label">Freshness score <span>OPTIONAL</span></span><input type="number" min="0" max="100" value={score} onChange={(event) => setScore(event.target.value)} placeholder="No score supplied" /></label>
      <button className="secondary-button" disabled={busy || !claim.trim()}>{busy ? <LoaderCircle size={15} className="spin" /> : <FlaskConical size={15} />} Evaluate preview</button></form>
    {error && <InlineNotice>{error}</InlineNotice>}{result && <pre className="preview-result">{JSON.stringify(result, null, 2)}</pre>}
  </section>;
}

function PolicyEditor({ policy, categories, busy, onCancel, onSubmit }: {
  policy: Policy | null; categories: Category[]; busy: boolean; onCancel: () => void; onSubmit: (event: React.FormEvent<HTMLFormElement>) => void;
}) {
  const selected = policy?.categories ?? categories.filter((item) => item.volatile).map((item) => item.name);
  return <form className="folio-panel policy-editor" onSubmit={onSubmit}>
    <div className="panel-heading"><h2>{policy ? `Edit ${policy.name}` : "Create a policy"}</h2><button type="button" className="icon-button" onClick={onCancel} aria-label="Close policy editor"><X size={17} /></button></div>
    <div className="editor-grid"><label className="field-group"><span className="field-label">Name</span><input name="name" required maxLength={120} defaultValue={policy?.name ?? ""} /></label>
      <label className="field-group"><span className="field-label">Jurisdiction <span>OPTIONAL</span></span><input name="jurisdiction" defaultValue={policy?.jurisdiction ?? ""} /></label>
      <label className="field-group span-two"><span className="field-label">Description</span><input name="description" maxLength={1000} defaultValue={policy?.description ?? ""} /></label>
      <div className="field-group span-two"><span className="field-label">Claim categories</span><div className="category-options">{categories.map((category) => <label key={category.name} className="check-line"><input type="checkbox" name="categories" value={category.name} defaultChecked={selected.includes(category.name)} /><span>{display(category.name)}</span>{category.volatile && <small>volatile</small>}</label>)}</div></div>
      <label className="field-group"><span className="field-label">Verification mode</span><select name="verification_mode" defaultValue={policy?.verification_mode ?? "warn"}><option value="shadow">Shadow</option><option value="warn">Warn</option><option value="enforce">Enforce</option></select></label>
      <label className="field-group"><span className="field-label">Minimum source count</span><input name="minimum_source_count" type="number" min="0" max="10" defaultValue={policy?.minimum_source_count ?? ""} placeholder="Service default" /></label>
      <label className="field-group"><span className="field-label">Minimum source authority</span><input name="minimum_source_authority" type="number" min="0" max="1" step="0.01" defaultValue={policy?.minimum_source_authority ?? ""} placeholder="Service default" /></label>
      <label className="field-group"><span className="field-label">Minimum freshness score</span><input name="minimum_freshness_score" type="number" min="0" max="100" defaultValue={policy?.minimum_freshness_score ?? ""} placeholder="Service default" /></label>
      <label className="field-group"><span className="field-label">Maximum evidence age <span>DAYS · OPTIONAL</span></span><input name="maximum_evidence_age_days" type="number" min="1" max="3650" defaultValue={policy?.maximum_evidence_age_days ?? ""} /></label>
      <label className="field-group span-two"><span className="field-label">Preferred source domains</span><input name="preferred_domains" defaultValue={policy?.preferred_domains.join(", ") ?? ""} placeholder="Comma-separated domains" /></label>
      <label className="field-group span-two"><span className="field-label">Allowed source domains</span><input name="allowed_domains" defaultValue={policy?.allowed_domains.join(", ") ?? ""} placeholder="Leave blank to allow any eligible domain" /></label>
      <label className="field-group span-two"><span className="field-label">Blocked source domains</span><input name="blocked_domains" defaultValue={policy?.blocked_domains.join(", ") ?? ""} placeholder="domain-to-exclude.example" /></label>
      <label className="field-group span-two"><span className="field-label">Cache TTL by category <span>JSON · OPTIONAL</span></span><textarea name="cache_ttl_seconds" rows={2} defaultValue={policy?.cache_ttl_seconds && Object.keys(policy.cache_ttl_seconds).length ? JSON.stringify(policy.cache_ttl_seconds, null, 2) : ""} placeholder="Service defaults" /></label>
      <label className="check-line"><input type="checkbox" name="enabled" defaultChecked={policy?.enabled ?? true} /><span>Policy enabled</span></label>
      <label className="check-line"><input type="checkbox" name="cache_enabled" defaultChecked={policy?.cache_enabled ?? true} /><span>Cache eligible results</span></label>
    </div>
    <div className="editor-actions"><button type="button" className="secondary-button" onClick={onCancel}>Cancel</button><button className="primary-button" disabled={busy}>{busy ? <LoaderCircle size={15} className="spin" /> : <Check size={15} />}{policy ? "Save changes" : "Create policy"}</button></div>
  </form>;
}

function WatchtowerView({ claims, alerts, onRefresh, onError }: { claims: WatchClaim[]; alerts: WatchAlert[]; onRefresh: () => Promise<void>; onError: (message: string | null) => void }) {
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const check = async (claim: WatchClaim) => {
    setBusy(claim.id); onError(null); setNotice(null);
    try { await api(`/api/watchtower/claims/${encodeURIComponent(claim.id)}/check`, { method: "POST" }); await onRefresh(); setNotice("Claim checked. The latest result is now shown."); }
    catch (cause) { onError(cause instanceof Error ? cause.message : "The claim could not be checked. Try again when providers are available."); }
    finally { setBusy(null); }
  };
  const toggle = async (claim: WatchClaim) => {
    setBusy(claim.id); onError(null);
    try { await api(`/api/watchtower/claims/${encodeURIComponent(claim.id)}`, patchBody({ enabled: !claim.enabled })); await onRefresh(); }
    catch (cause) { onError(cause instanceof Error ? cause.message : "The monitoring state could not be changed."); }
    finally { setBusy(null); }
  };
  const acknowledge = async (alert: WatchAlert) => {
    setBusy(alert.id); onError(null);
    try { await api(`/api/watchtower/alerts/${encodeURIComponent(alert.id)}/acknowledge`, { method: "POST" }); await onRefresh(); }
    catch (cause) { onError(cause instanceof Error ? cause.message : "This alert could not be acknowledged."); }
    finally { setBusy(null); }
  };
  return <>
    <SectionHeading title="Watchtower" detail="Scheduled rechecks surface changes in claims you choose to monitor." action={<button className="secondary-button" disabled={busy !== null} onClick={() => void onRefresh()}><RefreshCw size={15} /> Refresh</button>} />
    {notice && <InlineNotice variant="success" onDismiss={() => setNotice(null)}>{notice}</InlineNotice>}
    <section className="folio-panel watch-panel"><div className="list-heading"><h2>Claims under watch</h2><span className="table-count">{claims.length} saved</span></div>
      {!claims.length ? <EmptyState title="No claims monitored" detail="After verifying a response, choose Monitor claim to schedule repeat checks." /> : <div className="watch-list">{claims.map((claim) => <article className="watch-row" key={claim.id}>
        <div className="watch-claim"><span className={`watch-pulse ${claim.enabled ? "enabled" : "disabled"}`} /><div><h3>{claim.claim_text}</h3><span>{display(claim.category)} · Next check {dateTime(claim.next_check_at)}</span></div></div>
        <div className="watch-state"><StatusMark value={claim.last_error ? "failed" : claim.last_verdict ?? (claim.enabled ? "pending" : "paused")} /><span>Score {claim.last_score ?? "—"}</span></div>
        {claim.last_error && <p className="watch-error">{claim.last_error}</p>}
        <div className="watch-actions"><button className="text-button" onClick={() => void check(claim)} disabled={busy !== null}><RefreshCw size={14} className={busy === claim.id ? "spin" : ""} /> Check now</button><button className="text-button" onClick={() => void toggle(claim)} disabled={busy !== null}>{claim.enabled ? "Pause" : "Resume"}</button></div>
      </article>)}</div>}
    </section>
    <section className="folio-panel watch-panel alerts-panel"><div className="list-heading"><h2>Unacknowledged alerts</h2><span className="table-count">{alerts.length} open</span></div>
      {!alerts.length ? <EmptyState title="No detected changes" detail="When a monitored claim changes verdict, its alert will appear here." /> : <div className="alert-list">{alerts.map((alert) => {
        const watchedClaim = claims.find((claim) => claim.id === alert.monitored_claim_id);
        return <article className="alert-row" key={alert.id}>
        <div className="alert-sign"><AlertTriangle size={17} /></div><div className="alert-copy"><strong>{alert.claim_text ?? watchedClaim?.claim_text ?? `Claim ${alert.monitored_claim_id}`}</strong><span>{display(alert.previous_verdict)} <ArrowDownRight size={13} /> {display(alert.current_verdict)} · {dateTime(alert.created_at)}</span></div>
        <button className="text-button" onClick={() => void acknowledge(alert)} disabled={busy !== null}>{busy === alert.id ? <LoaderCircle size={14} className="spin" /> : <Check size={14} />} Acknowledge</button>
      </article>})}</div>}
    </section>
  </>;
}

function ReplayView({ claims }: { claims: WatchClaim[] }) {
  const [fingerprint, setFingerprint] = useState("");
  const [replay, setReplay] = useState<Replay | null>(null);
  const [comparison, setComparison] = useState<RecordValue | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fromId, setFromId] = useState("");
  const [toId, setToId] = useState("");
  const load = async (event?: React.FormEvent) => {
    event?.preventDefault(); setError(null); setReplay(null); setComparison(null); setLoading(true);
    try {
      const data = await api<Replay>(`/api/replay/${encodeURIComponent(fingerprint.trim())}`);
      setReplay(data); setFromId(""); setToId("");
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Replay history could not be loaded."); }
    finally { setLoading(false); }
  };
  const compare = async () => {
    setError(null); setComparison(null); setLoading(true);
    try { setComparison(await api<RecordValue>(`/api/replay/${encodeURIComponent(fingerprint.trim())}/compare?from=${encodeURIComponent(fromId)}&to=${encodeURIComponent(toId)}`)); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "These snapshots could not be compared."); }
    finally { setLoading(false); }
  };
  const snapshots = replay?.snapshots ?? [];
  return <>
    <SectionHeading title="Replay" detail="Inspect immutable verification snapshots and compare how evidence changed over time." />
    <form className="access-strip replay-search" onSubmit={(event) => void load(event)}><div className="access-icon"><History size={17} /></div><div className="access-copy"><strong>Find a claim history</strong><span>Choose a monitored claim or paste a 64-character claim fingerprint.</span></div>
      <select value={fingerprint} onChange={(event) => setFingerprint(event.target.value)} aria-label="Select monitored claim"><option value="">Select monitored claim</option>{claims.map((claim) => <option key={claim.id} value={claim.claim_fingerprint}>{claim.claim_text.slice(0, 90)}</option>)}</select>
      <input value={fingerprint} onChange={(event) => setFingerprint(event.target.value)} aria-label="Claim fingerprint" placeholder="Claim fingerprint" />
      <button className="secondary-button" disabled={loading || !fingerprint.trim()}>{loading ? <LoaderCircle size={15} className="spin" /> : <Search size={15} />} Load history</button>
    </form>
    {error && <InlineNotice>{error}</InlineNotice>}
    {replay && <><section className="folio-panel replay-history"><div className="list-heading"><h2>Verification history</h2><span className="table-count">{replay.total} records</span></div>
      {!snapshots.length ? <EmptyState title="No verification history exists for this claim yet" detail="Snapshots will appear after this claim has been checked and recorded." /> : <>
        <div className="table-wrap"><table><thead><tr><th>Checked</th><th>Verdict</th><th>Freshness</th><th>Policy</th><th>Snapshot</th></tr></thead><tbody>{snapshots.map((snapshot) => <tr key={snapshot.id}>
          <td>{dateTime(snapshot.checked_at)}</td><td><StatusMark value={snapshot.verdict} /></td><td>{snapshot.freshness_score ?? "—"}</td><td>{snapshot.policy_id ?? "—"}</td><td className="tabular">{snapshot.id}</td>
        </tr>)}</tbody></table></div>
        {snapshots.length > 1 && <div className="compare-controls"><div><p>Compare two immutable snapshots without changing either record.</p></div><select value={fromId} onChange={(event) => setFromId(event.target.value)} aria-label="Earlier snapshot"><option value="">Earlier snapshot</option>{snapshots.map((item) => <option key={item.id} value={item.id}>{dateTime(item.checked_at)} · {item.verdict}</option>)}</select><select value={toId} onChange={(event) => setToId(event.target.value)} aria-label="Later snapshot"><option value="">Later snapshot</option>{snapshots.map((item) => <option key={item.id} value={item.id}>{dateTime(item.checked_at)} · {item.verdict}</option>)}</select><button className="secondary-button" onClick={() => void compare()} disabled={loading || !fromId || !toId || fromId === toId}>{loading ? <LoaderCircle size={15} className="spin" /> : <ArrowUpRight size={15} />} Compare</button></div>}
      </>}</section>
      {comparison && <ComparisonView value={comparison} />}
    </>}
  </>;
}

function ComparisonView({ value }: { value: RecordValue }) {
  const verdict = value.verdict as RecordValue | undefined;
  const score = value.freshness_score as RecordValue | undefined;
  const sources = value.sources as RecordValue | undefined;
  return <section className="folio-panel comparison-panel"><div className="panel-heading"><h2>What changed</h2><History size={18} className="panel-symbol" /></div>
    <div className="comparison-grid"><div><span>Verdict</span><p><StatusMark value={String(verdict?.previous ?? "unknown")} /> <ArrowDownRight size={13} /> <StatusMark value={String(verdict?.current ?? "unknown")} /></p></div>
      <div><span>Freshness score</span><p>{display(score?.previous)} <ArrowDownRight size={13} /> {display(score?.current)}{score?.delta !== null && score?.delta !== undefined && <small className={Number(score.delta) >= 0 ? "good-text" : "bad-text"}> ({Number(score.delta) > 0 ? "+" : ""}{display(score.delta)})</small>}</p></div>
      <div><span>Evidence changed</span><p><StatusMark value={String(value.evidence_changed)} /></p></div>
      <div><span>Policy changed</span><p><StatusMark value={String((value.policy as RecordValue | undefined)?.changed)} /></p></div></div>
    {sources && <div className="change-list">{(["added", "removed", "changed"] as const).map((key) => {
      const records = Array.isArray(sources[key]) ? sources[key] as RecordValue[] : [];
      return <div key={key}><h3>{key} sources <span>{records.length}</span></h3>{records.length ? records.map((row, index) => <pre key={index}>{JSON.stringify(row, null, 2)}</pre>) : <p>No {key} sources.</p>}</div>;
    })}</div>}
  </section>;
}
