"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { TrendChart } from "@/components/charts";
import { RunQAButton } from "@/components/RunQAButton";
import { ScorePill } from "@/components/ScoreCards";
import { Button, Card, EmptyState, ErrorState, PrivacyNotice, Spinner, StatusBadge } from "@/components/ui";
import { api } from "@/lib/api";
import { durationBetween, fmtDate, fmtScore } from "@/lib/format";
import type { Comparison, Project, RunBrief, TrendPoint } from "@/lib/types";

export default function ProjectPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const [project, setProject] = useState<Project | null>(null);
  const [runs, setRuns] = useState<RunBrief[] | null>(null);
  const [trends, setTrends] = useState<{ points: TrendPoint[]; latest_comparison: Comparison | null } | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [nonce, setNonce] = useState(0);
  const load = useCallback(() => setNonce((n) => n + 1), []);

  useEffect(() => {
    Promise.all([
      api<Project>(`/projects/${id}`),
      api<RunBrief[]>(`/projects/${id}/runs`),
      api<{ points: TrendPoint[]; latest_comparison: Comparison | null }>(`/projects/${id}/trends`),
    ])
      .then(([p, r, t]) => {
        setError(null);
        setProject(p);
        setRuns(r);
        setTrends(t);
      })
      .catch((e: Error) => setError(e.message));
  }, [id, nonce]);

  async function remove() {
    if (!project || !confirm(`Delete project "${project.name}" and all its runs? This cannot be undone.`)) return;
    try {
      await api(`/projects/${id}`, { method: "DELETE" });
      router.push("/");
    } catch (e) {
      setError((e as Error).message);
    }
  }

  if (error && !project) return <ErrorState error={error} onRetry={load} />;
  if (!project || !runs || !trends) return <Spinner label="Loading project" />;

  const active = runs.find((r) => r.status === "queued" || r.status === "running");
  const cmp = trends.latest_comparison;

  return (
    <div className="space-y-6">
      <nav aria-label="Breadcrumb" className="text-sm text-muted">
        <Link href="/" className="hover:text-text">
          Projects
        </Link>{" "}
        / <span className="text-text">{project.name}</span>
      </nav>
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <h1 className="truncate text-2xl font-semibold">{project.name}</h1>
          <p className="truncate text-sm text-muted">
            <a href={project.base_url} target="_blank" rel="noreferrer" className="underline">
              {project.base_url}
            </a>
            {project.openapi_url && <> · OpenAPI: {project.openapi_url}</>}
            {project.has_credentials && <> · login as {project.username}</>}
          </p>
        </div>
        <div className="flex items-center gap-2">
          {active ? (
            <Link href={`/runs/${active.id}`} className="rounded-lg bg-accent px-5 py-3 font-semibold text-accent-contrast">
              View run in progress
            </Link>
          ) : (
            <RunQAButton projectId={project.id} />
          )}
        </div>
      </div>
      {error && <ErrorState error={error} />}
      <PrivacyNotice compact />

      <div className="grid gap-4 lg:grid-cols-3">
        <Card title="Score over time" className="lg:col-span-2">
          {trends.points.length > 0 ? (
            <TrendChart points={trends.points} />
          ) : (
            <EmptyState title="No completed runs yet">Press Run QA to get your first quality score.</EmptyState>
          )}
        </Card>
        <Card title="Latest vs previous run">
          {cmp && cmp.previous_run_id ? (
            <dl className="grid grid-cols-3 gap-2 text-center">
              <div className="rounded-lg bg-surface-2 p-3">
                <dt className="text-xs text-muted">New issues</dt>
                <dd className="text-2xl font-semibold text-bad">{cmp.new}</dd>
              </div>
              <div className="rounded-lg bg-surface-2 p-3">
                <dt className="text-xs text-muted">Fixed</dt>
                <dd className="text-2xl font-semibold text-good">{cmp.fixed}</dd>
              </div>
              <div className="rounded-lg bg-surface-2 p-3">
                <dt className="text-xs text-muted">Still open</dt>
                <dd className="text-2xl font-semibold">{cmp.persisting}</dd>
              </div>
              <p className="col-span-3 mt-1 text-sm text-muted">
                {cmp.new} new issue{cmp.new === 1 ? "" : "s"}, {cmp.fixed} fixed compared with run #{cmp.previous_run_id}
                {cmp.previous_score != null && <> (score was {fmtScore(cmp.previous_score)})</>}.
              </p>
            </dl>
          ) : (
            <EmptyState title="Nothing to compare yet">Run QA twice to see new and fixed issues.</EmptyState>
          )}
        </Card>
      </div>

      <Card title="Run history">
        {runs.length === 0 ? (
          <EmptyState title="No runs yet" />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-left text-muted">
                <tr className="border-b border-border">
                  <th scope="col" className="py-2 pr-3 font-medium">Run</th>
                  <th scope="col" className="py-2 pr-3 font-medium">Status</th>
                  <th scope="col" className="py-2 pr-3 font-medium">Score</th>
                  <th scope="col" className="py-2 pr-3 font-medium">UI / API / A11y / Perf</th>
                  <th scope="col" className="py-2 pr-3 font-medium">AI calls (cache hits)</th>
                  <th scope="col" className="py-2 pr-3 font-medium">Duration</th>
                  <th scope="col" className="py-2 font-medium">Started</th>
                </tr>
              </thead>
              <tbody>
                {runs.map((r) => (
                  <tr key={r.id} className="border-b border-border last:border-0">
                    <td className="py-2 pr-3">
                      <Link href={`/runs/${r.id}`} className="font-medium text-accent underline">
                        #{r.id}
                      </Link>
                    </td>
                    <td className="py-2 pr-3">
                      <StatusBadge status={r.status} />
                    </td>
                    <td className="py-2 pr-3">
                      <ScorePill score={r.score} />
                    </td>
                    <td className="py-2 pr-3 tabular text-muted">
                      {fmtScore(r.ui_score)} / {fmtScore(r.api_score)} / {fmtScore(r.a11y_score)} / {fmtScore(r.perf_score)}
                    </td>
                    <td className="py-2 pr-3 tabular">
                      {r.llm_calls} ({r.cache_hits})
                    </td>
                    <td className="py-2 pr-3 tabular">{durationBetween(r.started_at, r.finished_at)}</td>
                    <td className="py-2">{fmtDate(r.started_at || r.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <div className="flex justify-end">
        <Button variant="danger" onClick={remove} disabled={!!active}>
          Delete project
        </Button>
      </div>
    </div>
  );
}
