export type ApiStatus = { status: string; database?: string; cache?: string; upstream?: string };
export type Health = { status: string; service?: string; upstream?: string; providers?: string[] };
export type ProviderOption = {
  id: string;
  name: string;
  base_url: string;
  default_model: string;
  models: string[];
  credential_hint: string;
};
export type Category = { name: string; volatile: boolean };
export type Source = {
  url: string; domain?: string; published?: string | null; snippet?: string;
  authority_score?: number; is_primary?: boolean; relevance_score?: number;
};
export type Claim = {
  id: string; text: string; type: string; entities?: string[]; temporal_anchor?: string | null;
  verdict?: string | null; explanation?: string | null; score?: number | null;
  confidence?: number; sources?: Source[]; supporting_sources?: Source[];
  contradicting_sources?: Source[]; verification_id?: string | null;
  claim_fingerprint?: string | null; verification_timestamp?: string | null;
};
export type VerificationResult = {
  claims: Claim[];
  summary: {
    total: number; checked: number; stale: number; avg_score?: number | null;
    status: string; message?: string | null; extraction_status?: string | null;
  };
  status: string; error?: string | null;
  policy_evaluation?: Record<string, unknown> | null;
};
export type Policy = {
  id: string; name: string; description: string; enabled: boolean; categories: string[];
  preferred_domains: string[]; allowed_domains: string[]; blocked_domains: string[];
  minimum_source_count: number; minimum_source_authority: number; minimum_freshness_score: number;
  maximum_evidence_age_days?: number | null; category_maximum_evidence_age_days: Record<string, number>;
  category_minimum_freshness_scores: Record<string, number>; verification_mode: "shadow" | "warn" | "enforce";
  cache_enabled: boolean; cache_ttl_seconds: Record<string, number>; jurisdiction?: string | null;
  created_at: number; updated_at: number;
};
export type WatchClaim = {
  id: string; claim_text: string; category: string; enabled: boolean; last_verdict?: string | null;
  last_score?: number | null; last_checked_at?: number | null; last_error?: string | null;
  next_check_at?: number | null; policy_id?: string | null; claim_fingerprint: string;
};
export type WatchAlert = {
  id: string; monitored_claim_id: string; previous_verdict?: string | null;
  current_verdict: string; acknowledged_at?: number | null; created_at: number;
  claim_text?: string; metadata?: Record<string, unknown>;
};
export type Snapshot = {
  id: string; verification_id?: string; claim_fingerprint: string; verdict: string;
  freshness_score?: number | null; sources?: Source[]; checked_at: number;
  policy_id?: string | null; metadata?: Record<string, unknown>;
};
export type Replay = { claim_fingerprint: string; snapshots: Snapshot[]; total: number };
export type Benchmark = {
  results_md: string; total_items: number; stale_items: number;
  control_items: number; partial_or_unverifiable_items: number;
};
export type MetricsData = Record<string, number | string | boolean | null>;
export type RecordValue = Record<string, unknown>;
