export type RunStatus = "queued" | "running" | "completed" | "failed" | "cancelled";
export type StageStatus = "pending" | "running" | "passed" | "degraded" | "failed" | "skipped" | "cancelled";
export type Severity = "critical" | "high" | "medium" | "low";
export type Category = "ui" | "api" | "a11y" | "perf";
export type Classification = "app_bug" | "flaky" | "test_error" | "environment";

export interface RunBrief {
  id: number;
  status: RunStatus;
  score: number | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  llm_calls: number;
  cache_hits: number;
  ui_score?: number | null;
  api_score?: number | null;
  a11y_score?: number | null;
  perf_score?: number | null;
}

export interface Project {
  id: number;
  name: string;
  base_url: string;
  openapi_url: string | null;
  has_credentials: boolean;
  username: string | null;
  login_url: string | null;
  settings: Record<string, unknown>;
  consent_acknowledged_at: string;
  created_at: string;
  last_run: RunBrief | null;
}

export interface Stage {
  stage: string;
  label: string;
  status: StageStatus;
  duration_ms?: number | null;
  llm_calls?: number;
  error?: string | null;
  details?: Record<string, unknown>;
}

export interface Comparison {
  previous_run_id: number | null;
  previous_score?: number | null;
  new?: number;
  fixed?: number;
  persisting?: number;
  fixed_titles?: string[];
}

export interface Run extends RunBrief {
  project_id: number;
  ui_score: number | null;
  api_score: number | null;
  a11y_score: number | null;
  perf_score: number | null;
  tokens_in: number;
  tokens_out: number;
  llm_budget: number;
  budget_remaining: number;
  summary: string | null;
  summary_ai: boolean;
  comparison: Comparison;
  cancel_requested: boolean;
  error: string | null;
  config: Record<string, unknown>;
  stages: Stage[];
  finding_counts: Record<"severity" | "category" | "classification", Record<string, number>>;
  open_findings: number;
}

export interface Finding {
  id: number;
  run_id: number;
  fingerprint: string;
  category: Category;
  severity: Severity;
  classification: Classification;
  title: string;
  description: string;
  impact: string;
  root_cause: string;
  repro_steps: string[];
  suggested_fix: string;
  evidence: Record<string, unknown>;
  status: "open" | "ignored" | "fixed";
  ai_enriched: boolean;
  ai_unverified: boolean;
}

export interface TestRow {
  id: number | null;
  name: string;
  script: string;
  source: string;
  valid: boolean;
  validation_error: string | null;
  attempts: Record<string, unknown>[];
  journey: { name: string; description: string; steps: string[]; expected: string[]; priority: number; source: string } | null;
  result: {
    status: string;
    error: string | null;
    error_kind: string | null;
    duration_ms: number;
    attempts: number;
    screenshot: string | null;
    trace: string | null;
    video: string | null;
  } | null;
}

export interface ApiCheck {
  id: number;
  method: string;
  url: string;
  path: string;
  check: string;
  status: "passed" | "failed" | "warning" | "skipped";
  status_code: number | null;
  latency_ms: number | null;
  message: string;
  details: Record<string, unknown>;
}

export interface A11yViolation {
  id: number;
  page_url: string;
  rule_id: string;
  impact: "critical" | "serious" | "moderate" | "minor" | null;
  wcag_tags: string[];
  description: string;
  help: string;
  help_url: string;
  node_count: number;
  nodes: { target: string[]; html: string; summary: string }[];
}

export interface PerfMetric {
  id: number;
  page_url: string;
  ttfb: number | null;
  fcp: number | null;
  lcp: number | null;
  cls_score: number | null;
  dcl: number | null;
  load: number | null;
  transfer_size: number | null;
  request_count: number | null;
  ratings: Record<string, string>;
  score: number | null;
}

export interface LLMCallRow {
  id: number;
  stage: string;
  model: string;
  prompt_name: string;
  prompt_version: string;
  prompt_hash: string;
  tokens_in: number;
  tokens_out: number;
  latency_ms: number;
  cache_hit: boolean;
  status: string;
  error: string | null;
  created_at: string;
}

export interface LLMUsage {
  calls: LLMCallRow[];
  by_stage: Record<string, { calls: number; cache_hits: number; tokens_in: number; tokens_out: number; fallbacks: number }>;
  totals: { calls: number; cache_hits: number; tokens_in: number; tokens_out: number; budget: number; budget_remaining: number };
}

export interface TrendPoint {
  run_id: number;
  finished_at: string | null;
  score: number | null;
  ui: number | null;
  api: number | null;
  a11y: number | null;
  perf: number | null;
  bugs: number;
  llm_calls: number;
  cache_hits: number;
}

export interface RunEventMsg {
  id: number;
  type: "run_status" | "stage_status" | "log" | "progress";
  stage: string | null;
  message: string;
  data: Record<string, unknown>;
  ts: string;
}

export interface PublicSettings {
  llm_configured: boolean;
  models: Record<string, string>;
  rps: number;
  tpm: number;
  max_llm_calls_per_run: number;
  max_llm_calls_per_day: number;
  llm_calls_today: number;
  vision_check_enabled: boolean;
  allow_local_targets: boolean;
  default_max_pages: number;
  default_max_depth: number;
}
