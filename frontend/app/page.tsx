"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { NewProjectForm } from "@/components/NewProjectForm";
import { ScorePill } from "@/components/ScoreCards";
import { Button, Card, EmptyState, ErrorState, Spinner, StatusBadge } from "@/components/ui";
import { api } from "@/lib/api";
import { fmtDate } from "@/lib/format";
import type { Project } from "@/lib/types";

export default function ProjectsPage() {
  const router = useRouter();
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);

  const [nonce, setNonce] = useState(0);
  const load = useCallback(() => setNonce((n) => n + 1), []);

  useEffect(() => {
    api<Project[]>("/projects")
      .then((p) => {
        setError(null);
        setProjects(p);
        if (p.length === 0) setShowForm(true);
      })
      .catch((e: Error) => setError(e.message));
  }, [nonce]);

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">Projects</h1>
          <p className="text-sm text-muted">Paste a URL, press one button, get a QA report.</p>
        </div>
        {!showForm && (
          <Button variant="primary" onClick={() => setShowForm(true)}>
            New project
          </Button>
        )}
      </div>

      {showForm && (
        <Card>
          <NewProjectForm
            onCreated={(p) => router.push(`/projects/${p.id}`)}
            onCancel={projects && projects.length > 0 ? () => setShowForm(false) : undefined}
          />
        </Card>
      )}

      {error && <ErrorState error={error} onRetry={load} />}
      {!projects && !error && <Spinner label="Loading projects" />}
      {projects && projects.length === 0 && !showForm && (
        <EmptyState title="No projects yet">Create your first project to run QA.</EmptyState>
      )}
      {projects && projects.length > 0 && (
        <ul className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3" aria-label="Projects">
          {projects.map((p) => (
            <li key={p.id}>
              <Link href={`/projects/${p.id}`} className="card block h-full p-4 transition hover:border-accent">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <h2 className="truncate font-semibold">{p.name}</h2>
                    <p className="truncate text-sm text-muted">{p.base_url}</p>
                  </div>
                  <ScorePill score={p.last_run?.score ?? null} />
                </div>
                <div className="mt-4 flex items-center justify-between text-xs text-muted">
                  {p.last_run ? (
                    <>
                      <StatusBadge status={p.last_run.status} />
                      <span>{fmtDate(p.last_run.finished_at || p.last_run.created_at)}</span>
                    </>
                  ) : (
                    <span>Not run yet</span>
                  )}
                </div>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
