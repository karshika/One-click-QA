"use client";

import { useMemo, useState } from "react";
import { PerfBars } from "./charts";
import { Badge, Button, EmptyState, ErrorState, SeverityBadge, Spinner, StatusBadge, cx } from "./ui";
import { api, artifactUrl } from "@/lib/api";
import { fmtBytes, fmtMs, pathOf, SEVERITY_ORDER } from "@/lib/format";
import { useFetch } from "@/lib/useFetch";
import type { A11yViolation, ApiCheck, Finding, LLMUsage, PerfMetric, Run, TestRow } from "@/lib/types";

function Loadable<T>({ state, children, empty }: { state: { data: T | null; error: string | null; reload: () => void }; children: (d: T) => React.ReactNode; empty?: (d: T) => boolean }) {
  if (state.error) return <ErrorState error={state.error} onRetry={state.reload} />;
  if (state.data === null) return <Spinner />;
  if (empty && empty(state.data)) return <EmptyState title="Nothing here yet">This stage has no results for this run.</EmptyState>;
  return <>{children(state.data)}</>;
}

const th = "py-2 pr-3 text-left font-medium text-muted";
const td = "py-2 pr-3 align-top";

// ----------------------------------------------------------------------------- Summary
export function SummaryTab({ run, findings }: { run: Run; findings: Finding[] | null }) {
  const top = (findings || []).filter((f) => f.status === "open" && f.classification === "app_bug").slice(0, 5);
  return (
    <div className="grid gap-4 lg:grid-cols-5">
      <section className="card p-4 lg:col-span-3" aria-labelledby="sum-h">
        <div className="mb-2 flex items-center gap-2">
          <h3 id="sum-h" className="font-semibold">
            Summary
          </h3>
          {run.summary_ai ? <Badge className="border-accent/40 text-accent">AI-written</Badge> : run.summary ? <Badge className="border-border text-muted">template (no AI)</Badge> : null}
        </div>
        {run.summary ? (
          <p className="whitespace-pre-line leading-relaxed">{run.summary}</p>
        ) : (
          <EmptyState title={run.status === "running" || run.status === "queued" ? "The summary appears when the run finishes" : "No summary"} />
        )}
        {run.comparison?.previous_run_id && (
          <p className="mt-4 rounded-lg bg-surface-2 p-3 text-sm">
            Compared with run #{run.comparison.previous_run_id}: <strong>{run.comparison.new} new</strong> issue(s),{" "}
            <strong>{run.comparison.fixed} fixed</strong>, {run.comparison.persisting} still open.
          </p>
        )}
      </section>
      <section className="card p-4 lg:col-span-2" aria-labelledby="top-h">
        <h3 id="top-h" className="mb-2 font-semibold">
          Top issues
        </h3>
        {findings === null ? (
          <Spinner />
        ) : top.length === 0 ? (
          <EmptyState title="No open app bugs" />
        ) : (
          <ol className="space-y-2">
            {top.map((f) => (
              <li key={f.id} className="flex items-start gap-2">
                <SeverityBadge severity={f.severity} />
                <span className="text-sm">
                  {f.title} <span className="text-muted">· {f.category}</span>
                </span>
              </li>
            ))}
          </ol>
        )}
      </section>
    </div>
  );
}

// ----------------------------------------------------------------------------- UI flows
export function UiTab({ runId, refreshKey }: { runId: number; refreshKey: unknown }) {
  const state = useFetch<TestRow[]>(`/runs/${runId}/tests`, refreshKey);
  const [open, setOpen] = useState<number | null>(null);
  return (
    <Loadable state={state} empty={(d) => d.length === 0}>
      {(rows) => (
        <ul className="space-y-3">
          {rows.map((t, i) => {
            const st = t.result?.status || (t.valid ? "pending" : "skipped");
            return (
              <li key={i} className="card p-4">
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div className="min-w-0">
                    <h3 className="font-semibold">{t.journey?.name || t.name}</h3>
                    {t.journey?.description && <p className="text-sm text-muted">{t.journey.description}</p>}
                  </div>
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge className="border-border text-muted">script: {t.source}</Badge>
                    {t.result && <Badge className="border-border text-muted">{t.result.attempts} attempt(s) · {fmtMs(t.result.duration_ms)}</Badge>}
                    <StatusBadge status={st} />
                  </div>
                </div>
                {t.journey && (
                  <div className="mt-3 grid gap-3 text-sm sm:grid-cols-2">
                    <div>
                      <p className="font-medium">Steps</p>
                      <ol className="ml-5 list-decimal text-muted">
                        {t.journey.steps.map((s, j) => (
                          <li key={j}>{s}</li>
                        ))}
                      </ol>
                    </div>
                    <div>
                      <p className="font-medium">Expected</p>
                      <ul className="ml-5 list-disc text-muted">
                        {t.journey.expected.map((s, j) => (
                          <li key={j}>{s}</li>
                        ))}
                      </ul>
                    </div>
                  </div>
                )}
                {(t.result?.error || t.validation_error) && (
                  <pre tabIndex={0} className="mt-3 max-h-40 overflow-auto whitespace-pre-wrap rounded-lg bg-surface-2 p-3 text-xs">{t.result?.error || t.validation_error}</pre>
                )}
                <div className="mt-3 flex flex-wrap gap-2 text-sm">
                  {t.script && (
                    <Button variant="secondary" aria-expanded={open === i} onClick={() => setOpen(open === i ? null : i)}>
                      {open === i ? "Hide script" : "View generated script"}
                    </Button>
                  )}
                  {t.result?.screenshot && (
                    <a className="rounded-lg border border-border px-3 py-2 hover:bg-surface-2" href={artifactUrl(t.result.screenshot)!} target="_blank" rel="noreferrer">
                      Screenshot
                    </a>
                  )}
                  {t.result?.trace && (
                    <a className="rounded-lg border border-border px-3 py-2 hover:bg-surface-2" href={artifactUrl(t.result.trace)!} download>
                      Trace (.zip)
                    </a>
                  )}
                  {t.result?.video && (
                    <a className="rounded-lg border border-border px-3 py-2 hover:bg-surface-2" href={artifactUrl(t.result.video)!} target="_blank" rel="noreferrer">
                      Video
                    </a>
                  )}
                </div>
                {t.result?.trace && (
                  <p className="mt-2 text-xs text-muted">
                    Open the trace with <code>npx playwright show-trace trace.zip</code> or at trace.playwright.dev.
                  </p>
                )}
                {open === i && (
                  <pre tabIndex={0} className="mt-3 max-h-96 overflow-auto rounded-lg bg-surface-2 p-3 font-mono text-xs leading-relaxed">
                    <code>{t.script}</code>
                  </pre>
                )}
                {t.result?.screenshot && t.result.status !== "passed" && (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img src={artifactUrl(t.result.screenshot)!} alt={`Page at the moment "${t.name}" failed`} className="mt-3 max-h-80 rounded-lg border border-border" />
                )}
              </li>
            );
          })}
        </ul>
      )}
    </Loadable>
  );
}

// ----------------------------------------------------------------------------- API
export function ApiTab({ runId, refreshKey }: { runId: number; refreshKey: unknown }) {
  const state = useFetch<ApiCheck[]>(`/runs/${runId}/api-checks`, refreshKey);
  return (
    <Loadable state={state} empty={(d) => d.length === 0}>
      {(rows) => {
        const failed = rows.filter((r) => r.status === "failed").length;
        return (
          <div className="card overflow-x-auto p-4">
            <p className="mb-3 text-sm text-muted">
              {rows.length} checks · <span className="text-bad">{failed} failed</span> · {rows.filter((r) => r.status === "warning").length} warnings
            </p>
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border">
                  <th scope="col" className={th}>Endpoint</th>
                  <th scope="col" className={th}>Check</th>
                  <th scope="col" className={th}>Result</th>
                  <th scope="col" className={th}>HTTP</th>
                  <th scope="col" className={th}>Latency</th>
                  <th scope="col" className={th}>Details</th>
                </tr>
              </thead>
              <tbody>
                {[...rows]
                  .sort((a, b) => (a.status === "failed" ? -1 : 0) - (b.status === "failed" ? -1 : 0))
                  .map((r) => (
                    <tr key={r.id} className="border-b border-border last:border-0">
                      <td className={cx(td, "font-mono text-xs")}>
                        {r.method} {r.path}
                        <div className="text-muted">{pathOf(r.url)}</div>
                      </td>
                      <td className={td}>{r.check}</td>
                      <td className={td}>
                        <StatusBadge status={r.status} />
                      </td>
                      <td className={cx(td, "tabular")}>{r.status_code ?? "–"}</td>
                      <td className={cx(td, "tabular")}>{fmtMs(r.latency_ms)}</td>
                      <td className={cx(td, "max-w-md text-muted")}>{r.message}</td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
        );
      }}
    </Loadable>
  );
}

// ----------------------------------------------------------------------------- Accessibility
const IMPACTS = ["critical", "serious", "moderate", "minor"] as const;

export function A11yTab({ runId, refreshKey }: { runId: number; refreshKey: unknown }) {
  const state = useFetch<A11yViolation[]>(`/runs/${runId}/a11y`, refreshKey);
  return (
    <Loadable state={state}>
      {(rows) =>
        rows.length === 0 ? (
          <EmptyState title="No WCAG A/AA violations found">axe-core found no violations on the crawled pages.</EmptyState>
        ) : (
          <div className="space-y-5">
            {IMPACTS.map((imp) => {
              const group = rows.filter((r) => (r.impact || "minor") === imp);
              if (!group.length) return null;
              return (
                <section key={imp} aria-labelledby={`imp-${imp}`}>
                  <h3 id={`imp-${imp}`} className="mb-2 font-semibold capitalize">
                    {imp} <span className="text-sm font-normal text-muted">({group.length})</span>
                  </h3>
                  <ul className="space-y-2">
                    {group.map((v) => (
                      <li key={v.id} className="card p-4 text-sm">
                        <div className="flex flex-wrap items-center justify-between gap-2">
                          <p className="font-medium">{v.help}</p>
                          <a href={v.help_url} target="_blank" rel="noreferrer" className="text-accent underline">
                            {v.rule_id} docs
                          </a>
                        </div>
                        <p className="text-muted">
                          {pathOf(v.page_url)} · {v.node_count} element(s) · {v.wcag_tags.join(", ")}
                        </p>
                        <ul className="mt-2 space-y-1">
                          {v.nodes.slice(0, 3).map((n, i) => (
                            <li key={i}>
                              <code tabIndex={0} className="block overflow-x-auto rounded bg-surface-2 p-2 text-xs">{n.html}</code>
                            </li>
                          ))}
                        </ul>
                      </li>
                    ))}
                  </ul>
                </section>
              );
            })}
          </div>
        )
      }
    </Loadable>
  );
}

// ----------------------------------------------------------------------------- Performance
const RATING_CLS: Record<string, string> = { good: "text-good", "needs-improvement": "text-warn", poor: "text-bad" };

function Metric({ value, rating, cls }: { value: number | null; rating?: string; cls?: boolean }) {
  return (
    <span className={cx("tabular", rating ? RATING_CLS[rating] : "")}>
      {cls ? (value ?? "–") : fmtMs(value)}
      {rating && rating !== "good" && <span className="ml-1 text-xs">({rating === "poor" ? "poor" : "needs work"})</span>}
    </span>
  );
}

export function PerfTab({ runId, refreshKey }: { runId: number; refreshKey: unknown }) {
  const state = useFetch<PerfMetric[]>(`/runs/${runId}/perf`, refreshKey);
  return (
    <Loadable state={state} empty={(d) => d.length === 0}>
      {(rows) => (
        <div className="space-y-4">
          <div className="grid gap-4 lg:grid-cols-2">
            <PerfBars metrics={rows} metric="lcp" title="Largest Contentful Paint" threshold={2500} />
            <PerfBars metrics={rows} metric="ttfb" title="Time to First Byte" threshold={800} />
          </div>
          <div className="card overflow-x-auto p-4">
            <table className="w-full text-sm">
              <caption className="mb-2 text-left text-sm text-muted">Median of repeated cold loads per page</caption>
              <thead>
                <tr className="border-b border-border">
                  <th scope="col" className={th}>Page</th>
                  <th scope="col" className={th}>TTFB</th>
                  <th scope="col" className={th}>FCP</th>
                  <th scope="col" className={th}>LCP</th>
                  <th scope="col" className={th}>CLS</th>
                  <th scope="col" className={th}>DOM loaded</th>
                  <th scope="col" className={th}>Load</th>
                  <th scope="col" className={th}>Transfer</th>
                  <th scope="col" className={th}>Requests</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((m) => (
                  <tr key={m.id} className="border-b border-border last:border-0">
                    <td className={td}>{pathOf(m.page_url)}</td>
                    <td className={td}><Metric value={m.ttfb} rating={m.ratings.ttfb} /></td>
                    <td className={td}><Metric value={m.fcp} rating={m.ratings.fcp} /></td>
                    <td className={td}><Metric value={m.lcp} rating={m.ratings.lcp} /></td>
                    <td className={td}><Metric value={m.cls_score} rating={m.ratings.cls} cls /></td>
                    <td className={cx(td, "tabular")}>{fmtMs(m.dcl)}</td>
                    <td className={cx(td, "tabular")}>{fmtMs(m.load)}</td>
                    <td className={cx(td, "tabular")}>{fmtBytes(m.transfer_size)}</td>
                    <td className={cx(td, "tabular")}>{m.request_count ?? "–"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </Loadable>
  );
}

// ----------------------------------------------------------------------------- Bugs
export function BugsTab({ findings, onChange }: { findings: Finding[] | null; onChange: (f: Finding) => void }) {
  const [sev, setSev] = useState("");
  const [cat, setCat] = useState("");
  const [cls, setCls] = useState("");
  const [status, setStatus] = useState("open");
  const [err, setErr] = useState<string | null>(null);
  const list = useMemo(
    () =>
      (findings || [])
        .filter((f) => (!sev || f.severity === sev) && (!cat || f.category === cat) && (!cls || f.classification === cls) && (!status || f.status === status))
        .sort((a, b) => SEVERITY_ORDER[a.severity] - SEVERITY_ORDER[b.severity]),
    [findings, sev, cat, cls, status],
  );

  async function mark(f: Finding, s: Finding["status"]) {
    setErr(null);
    try {
      onChange(await api<Finding>(`/findings/${f.id}`, { method: "PATCH", body: JSON.stringify({ status: s }) }));
    } catch (e) {
      setErr((e as Error).message);
    }
  }

  if (findings === null) return <Spinner />;
  const select = "rounded-lg border border-border bg-surface px-2 py-1.5 text-sm";
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-2" role="group" aria-label="Filter findings">
        <label className="text-sm">
          <span className="sr-only">Severity</span>
          <select className={select} value={sev} onChange={(e) => setSev(e.target.value)}>
            <option value="">All severities</option>
            {["critical", "high", "medium", "low"].map((s) => (
              <option key={s}>{s}</option>
            ))}
          </select>
        </label>
        <label className="text-sm">
          <span className="sr-only">Category</span>
          <select className={select} value={cat} onChange={(e) => setCat(e.target.value)}>
            <option value="">All categories</option>
            <option value="ui">UI</option>
            <option value="api">API</option>
            <option value="a11y">Accessibility</option>
            <option value="perf">Performance</option>
          </select>
        </label>
        <label className="text-sm">
          <span className="sr-only">Classification</span>
          <select className={select} value={cls} onChange={(e) => setCls(e.target.value)}>
            <option value="">All classifications</option>
            <option value="app_bug">App bug</option>
            <option value="flaky">Flaky</option>
            <option value="test_error">Test error</option>
            <option value="environment">Environment</option>
          </select>
        </label>
        <label className="text-sm">
          <span className="sr-only">Status</span>
          <select className={select} value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">Any status</option>
            <option value="open">Open</option>
            <option value="ignored">Ignored</option>
            <option value="fixed">Fixed</option>
          </select>
        </label>
        <span className="self-center text-sm text-muted">{list.length} shown</span>
      </div>
      {err && <ErrorState error={err} />}
      {list.length === 0 ? (
        <EmptyState title="No findings match these filters" />
      ) : (
        <ul className="space-y-3">
          {list.map((f) => (
            <li key={f.id} className="card p-4">
              <div className="flex flex-wrap items-start justify-between gap-2">
                <h3 className="font-semibold">{f.title}</h3>
                <div className="flex flex-wrap gap-1.5">
                  <SeverityBadge severity={f.severity} />
                  <Badge className="border-border text-muted">{f.category}</Badge>
                  <Badge className="border-border text-muted">{f.classification.replace("_", " ")}</Badge>
                  {f.ai_enriched && <Badge className="border-accent/40 text-accent">AI explained</Badge>}
                  {f.ai_unverified && <Badge className="border-warn/40 text-warn">AI-suggested, unverified</Badge>}
                </div>
              </div>
              <p className="mt-2 text-sm">{f.description}</p>
              <dl className="mt-2 grid gap-2 text-sm sm:grid-cols-2">
                {f.impact && (
                  <div>
                    <dt className="font-medium">Impact</dt>
                    <dd className="text-muted">{f.impact}</dd>
                  </div>
                )}
                {f.root_cause && (
                  <div>
                    <dt className="font-medium">Likely cause</dt>
                    <dd className="text-muted">{f.root_cause}</dd>
                  </div>
                )}
                {f.repro_steps.length > 0 && (
                  <div>
                    <dt className="font-medium">Steps to reproduce</dt>
                    <dd>
                      <ol className="ml-5 list-decimal text-muted">
                        {f.repro_steps.map((s, i) => (
                          <li key={i}>{s}</li>
                        ))}
                      </ol>
                    </dd>
                  </div>
                )}
                {f.suggested_fix && (
                  <div>
                    <dt className="font-medium">Suggested fix</dt>
                    <dd className="text-muted">{f.suggested_fix}</dd>
                  </div>
                )}
              </dl>
              <div className="mt-3 flex flex-wrap gap-2">
                {typeof f.evidence.screenshot === "string" && (
                  <a className="rounded-lg border border-border px-3 py-1.5 text-sm hover:bg-surface-2" href={artifactUrl(`/artifacts/${String(f.evidence.screenshot).replace(/\\/g, "/")}`)!} target="_blank" rel="noreferrer">
                    Screenshot
                  </a>
                )}
                {typeof f.evidence.trace === "string" && (
                  <a className="rounded-lg border border-border px-3 py-1.5 text-sm hover:bg-surface-2" href={artifactUrl(`/artifacts/${String(f.evidence.trace).replace(/\\/g, "/")}`)!} download>
                    Trace
                  </a>
                )}
                {f.status !== "open" && <Button onClick={() => mark(f, "open")}>Reopen</Button>}
                {f.status !== "ignored" && <Button onClick={() => mark(f, "ignored")}>Ignore</Button>}
                {f.status !== "fixed" && <Button onClick={() => mark(f, "fixed")}>Mark fixed</Button>}
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// ----------------------------------------------------------------------------- LLM usage
export function LlmTab({ runId, refreshKey }: { runId: number; refreshKey: unknown }) {
  const state = useFetch<LLMUsage>(`/runs/${runId}/llm-calls`, refreshKey);
  return (
    <Loadable state={state}>
      {(u) => (
        <div className="space-y-4">
          <dl className="grid grid-cols-2 gap-3 sm:grid-cols-5">
            {[
              ["API calls", u.totals.calls],
              ["Cache hits", u.totals.cache_hits],
              ["Budget left", `${u.totals.budget_remaining} / ${u.totals.budget}`],
              ["Tokens in", u.totals.tokens_in.toLocaleString()],
              ["Tokens out", u.totals.tokens_out.toLocaleString()],
            ].map(([k, v]) => (
              <div key={k} className="card p-3">
                <dt className="text-xs text-muted">{k}</dt>
                <dd className="text-xl font-semibold tabular">{v}</dd>
              </div>
            ))}
          </dl>
          {u.calls.length === 0 ? (
            <EmptyState title="No AI calls in this run">
              Either no MISTRAL_API_KEY is configured (deterministic mode) or everything was served from cache.
            </EmptyState>
          ) : (
            <div className="card overflow-x-auto p-4">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-border">
                    <th scope="col" className={th}>Stage</th>
                    <th scope="col" className={th}>Prompt</th>
                    <th scope="col" className={th}>Model</th>
                    <th scope="col" className={th}>Result</th>
                    <th scope="col" className={th}>Tokens in/out</th>
                    <th scope="col" className={th}>Latency</th>
                    <th scope="col" className={th}>Hash</th>
                  </tr>
                </thead>
                <tbody>
                  {u.calls.map((c) => (
                    <tr key={c.id} className="border-b border-border last:border-0">
                      <td className={td}>{c.stage}</td>
                      <td className={td}>
                        {c.prompt_name} v{c.prompt_version}
                      </td>
                      <td className={cx(td, "font-mono text-xs")}>{c.model}</td>
                      <td className={td}>
                        {c.cache_hit ? <Badge className="border-good/40 text-good">cache hit</Badge> : <StatusBadge status={c.status === "ok" ? "passed" : c.status === "error" ? "failed" : "skipped"} />}
                        {!c.cache_hit && c.status !== "ok" && <span className="ml-1 text-xs text-muted">{c.status}</span>}
                      </td>
                      <td className={cx(td, "tabular")}>
                        {c.tokens_in} / {c.tokens_out}
                      </td>
                      <td className={cx(td, "tabular")}>{c.cache_hit ? "–" : fmtMs(c.latency_ms)}</td>
                      <td className={cx(td, "font-mono text-xs text-muted")}>{c.prompt_hash}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </Loadable>
  );
}
