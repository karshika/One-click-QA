"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { RunQAButton } from "@/components/RunQAButton";
import { A11yTab, ApiTab, BugsTab, LlmTab, PerfTab, SummaryTab, UiTab } from "@/components/RunTabs";
import { CategoryCard, ScoreRing } from "@/components/ScoreCards";
import { Stepper } from "@/components/Stepper";
import { Tabs } from "@/components/Tabs";
import { Button, Card, ErrorState, PrivacyNotice, Spinner, StatusBadge } from "@/components/ui";
import { api, reportUrl } from "@/lib/api";
import { durationBetween, fmtDate } from "@/lib/format";
import { useRunEvents } from "@/lib/useRunEvents";
import type { Finding, Project, Run } from "@/lib/types";

const ACTIVE = new Set(["queued", "running"]);

export default function RunPage() {
  const { id } = useParams<{ id: string }>();
  const runId = Number(id);
  const [run, setRun] = useState<Run | null>(null);
  const [project, setProject] = useState<Project | null>(null);
  const [findings, setFindings] = useState<Finding[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState("summary");
  const [refreshKey, setRefreshKey] = useState(0);
  const load = useCallback(() => setRefreshKey((k) => k + 1), []);

  useEffect(() => {
    let alive = true;
    api<Run>(`/runs/${runId}`)
      .then(async (r) => {
        if (!alive) return;
        setRun(r);
        setError(null);
        const analyzed = r.stages.some((s) => s.stage === "analyze" && !["pending", "running"].includes(s.status));
        if (!ACTIVE.has(r.status) || analyzed) {
          const f = await api<Finding[]>(`/runs/${runId}/findings`);
          if (alive) setFindings(f);
        }
      })
      .catch((e: Error) => {
        if (alive) setError(e.message);
      });
    return () => {
      alive = false;
    };
  }, [runId, refreshKey]);

  const projectId = run?.project_id;
  useEffect(() => {
    if (projectId === undefined) return;
    api<Project>(`/projects/${projectId}`)
      .then(setProject)
      .catch(() => undefined);
  }, [projectId]);

  const active = !!run && ACTIVE.has(run.status);
  const { log, connected } = useRunEvents(run ? runId : null, active, load);

  // Fallback polling when SSE is unavailable (proxies, network hiccups).
  useEffect(() => {
    if (!active || connected) return;
    const t = setInterval(load, 3000);
    return () => clearInterval(t);
  }, [active, connected, load]);

  async function cancel() {
    try {
      await api(`/runs/${runId}/cancel`, { method: "POST" });
      load();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  if (error && !run) return <ErrorState error={error} onRetry={load} />;
  if (!run) return <Spinner label="Loading run" />;

  const counts = run.finding_counts?.category || {};
  const tabs = [
    { key: "summary", label: "Summary" },
    { key: "ui", label: "UI flows" },
    { key: "api", label: "API" },
    { key: "a11y", label: "Accessibility", count: counts.a11y },
    { key: "perf", label: "Performance" },
    { key: "bugs", label: "Bugs", count: run.open_findings },
    { key: "llm", label: "LLM usage" },
  ];
  const lastLog = [...log].reverse().find((l) => l.type === "log" || l.type === "stage_status");

  return (
    <div className="space-y-6">
      <nav aria-label="Breadcrumb" className="text-sm text-muted">
        <Link href="/" className="hover:text-text">
          Projects
        </Link>{" "}
        /{" "}
        {project ? (
          <Link href={`/projects/${project.id}`} className="hover:text-text">
            {project.name}
          </Link>
        ) : (
          "…"
        )}{" "}
        / <span className="text-text">Run #{run.id}</span>
      </nav>

      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="flex items-center gap-3 text-2xl font-semibold">
            Run #{run.id} <StatusBadge status={run.status} />
          </h1>
          <p className="text-sm text-muted">
            {project?.base_url} · started {fmtDate(run.started_at || run.created_at)} · duration {active ? "running…" : durationBetween(run.started_at, run.finished_at)}
            {" · "}
            {run.config?.llm_configured ? "AI on (Mistral)" : "AI off (deterministic mode)"}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {active ? (
            <Button variant="danger" onClick={cancel} disabled={run.cancel_requested}>
              {run.cancel_requested ? "Cancelling…" : "Cancel run"}
            </Button>
          ) : (
            <>
              <div className="flex gap-1" role="group" aria-label="Export report">
                <a className="rounded-lg border border-border bg-surface px-3 py-2 text-sm hover:bg-surface-2" href={reportUrl(run.id, "md")}>
                  Markdown
                </a>
                <a className="rounded-lg border border-border bg-surface px-3 py-2 text-sm hover:bg-surface-2" href={reportUrl(run.id, "html")} target="_blank" rel="noreferrer">
                  HTML
                </a>
                <a className="rounded-lg border border-border bg-surface px-3 py-2 text-sm hover:bg-surface-2" href={reportUrl(run.id, "pdf")}>
                  PDF
                </a>
              </div>
              <RunQAButton projectId={run.project_id} label="Run QA again" />
            </>
          )}
        </div>
      </div>

      <Card>
        <Stepper stages={run.stages} />
        {active && (
          <p className="mt-4 text-sm text-muted" aria-live="polite">
            {lastLog?.message || "Waiting for the worker…"}
          </p>
        )}
        {run.stages.some((s) => s.status === "failed" || s.status === "degraded") && !active && (
          <ul className="mt-4 space-y-1 text-sm">
            {run.stages
              .filter((s) => (s.status === "failed" || s.status === "degraded") && s.error)
              .map((s) => (
                <li key={s.stage} className={s.status === "failed" ? "text-bad" : "text-warn"}>
                  <strong>{s.label}</strong> {s.status === "degraded" ? "used its deterministic fallback" : "failed"}: {s.error}
                </li>
              ))}
          </ul>
        )}
        {run.error && <ErrorState error={run.error} />}
      </Card>

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-6">
        <div className="card flex items-center p-4 md:col-span-2">
          <ScoreRing score={run.score} />
        </div>
        <CategoryCard label="UI flows" weight={40} score={run.ui_score} detail={`${counts.ui || 0} open issue(s)`} />
        <CategoryCard label="API" weight={25} score={run.api_score} detail={`${counts.api || 0} open issue(s)`} />
        <CategoryCard label="Accessibility" weight={20} score={run.a11y_score} detail={`${counts.a11y || 0} open issue(s)`} />
        <CategoryCard label="Performance" weight={15} score={run.perf_score} detail={`${counts.perf || 0} open issue(s)`} />
      </div>

      <Tabs tabs={tabs} active={tab} onChange={setTab}>
        {tab === "summary" && <SummaryTab run={run} findings={findings} />}
        {tab === "ui" && <UiTab runId={run.id} refreshKey={refreshKey} />}
        {tab === "api" && <ApiTab runId={run.id} refreshKey={refreshKey} />}
        {tab === "a11y" && <A11yTab runId={run.id} refreshKey={refreshKey} />}
        {tab === "perf" && <PerfTab runId={run.id} refreshKey={refreshKey} />}
        {tab === "bugs" && <BugsTab findings={findings} onChange={(f) => setFindings((all) => (all || []).map((x) => (x.id === f.id ? f : x)))} />}
        {tab === "llm" && <LlmTab runId={run.id} refreshKey={refreshKey} />}
      </Tabs>

      <PrivacyNotice compact />
    </div>
  );
}
