import type { Stage } from "@/lib/types";
import { fmtMs } from "@/lib/format";
import { cx } from "./ui";

const DOT: Record<string, string> = {
  passed: "bg-good border-good text-white",
  degraded: "bg-warn border-warn text-white",
  failed: "bg-bad border-bad text-white",
  running: "bg-accent border-accent text-accent-contrast animate-pulse",
  skipped: "bg-surface-2 border-border text-muted",
  cancelled: "bg-surface-2 border-border text-muted",
  pending: "bg-surface border-border text-muted",
};
const ICON: Record<string, string> = {
  passed: "✓",
  degraded: "◐",
  failed: "✕",
  running: "●",
  skipped: "–",
  cancelled: "■",
  pending: "",
};

export function Stepper({ stages }: { stages: Stage[] }) {
  return (
    <ol className="grid grid-cols-3 gap-3 sm:grid-cols-5 lg:grid-cols-9" aria-label="Pipeline progress">
      {stages.map((s, i) => (
        <li key={s.stage} className="flex flex-col items-center text-center" title={s.error || undefined}>
          <span
            className={cx(
              "flex h-8 w-8 items-center justify-center rounded-full border-2 text-xs font-bold",
              DOT[s.status] || DOT.pending,
            )}
            aria-hidden="true"
          >
            {ICON[s.status] || i + 1}
          </span>
          <span className="mt-1 text-xs font-medium">{s.label}</span>
          <span className="text-[11px] text-muted">
            <span className="sr-only">{s.label}: </span>
            {s.status === "pending" ? "waiting" : s.status}
            {s.duration_ms != null && s.status !== "running" ? ` · ${fmtMs(s.duration_ms)}` : ""}
          </span>
          {!!s.llm_calls && <span className="text-[11px] text-muted">{s.llm_calls} AI call{s.llm_calls === 1 ? "" : "s"}</span>}
        </li>
      ))}
    </ol>
  );
}
