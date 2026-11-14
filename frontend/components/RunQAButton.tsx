"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { api } from "@/lib/api";
import type { Run } from "@/lib/types";
import { cx } from "./ui";

export function RunQAButton({ projectId, className, label = "Run QA" }: { projectId: number; className?: string; label?: string }) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function start() {
    setBusy(true);
    setError(null);
    try {
      const run = await api<Run>(`/projects/${projectId}/runs`, { method: "POST", body: JSON.stringify({}) });
      router.push(`/runs/${run.id}`);
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col items-end gap-1">
      <button
        type="button"
        onClick={start}
        disabled={busy}
        className={cx(
          "inline-flex items-center gap-2 rounded-xl bg-accent px-6 py-3 text-base font-semibold text-accent-contrast shadow-sm transition hover:opacity-90 disabled:opacity-60",
          className,
        )}
      >
        <span aria-hidden="true">▶</span> {busy ? "Starting…" : label}
      </button>
      {error && (
        <p role="alert" className="max-w-xs text-right text-xs text-bad">
          {error}
        </p>
      )}
    </div>
  );
}
