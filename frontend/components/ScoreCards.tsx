import { cx } from "./ui";
import { fmtScore, grade, scoreTone } from "@/lib/format";

const TONE_TEXT = { good: "text-good", warn: "text-warn", bad: "text-bad", none: "text-muted" } as const;
const TONE_LABEL = { good: "Good", warn: "Needs work", bad: "Poor", none: "No data" } as const;

export function ScorePill({ score }: { score: number | null }) {
  const tone = scoreTone(score);
  return (
    <span className={cx("rounded-lg border border-border bg-surface-2 px-2 py-1 text-lg font-semibold tabular", TONE_TEXT[tone])}>
      <span className="sr-only">Quality score </span>
      {fmtScore(score)}
    </span>
  );
}

export function ScoreRing({ score }: { score: number | null }) {
  const tone = scoreTone(score);
  const pct = score === null ? 0 : Math.max(0, Math.min(100, score));
  const r = 52;
  const c = 2 * Math.PI * r;
  return (
    <div className="flex items-center gap-4">
      <svg viewBox="0 0 120 120" className="h-28 w-28 shrink-0" role="img" aria-label={`Quality score ${fmtScore(score)} out of 100`}>
        <circle cx="60" cy="60" r={r} fill="none" stroke="var(--surface-2)" strokeWidth="10" />
        <circle
          cx="60"
          cy="60"
          r={r}
          fill="none"
          stroke={tone === "none" ? "var(--border)" : `var(--${tone === "warn" ? "warn" : tone})`}
          strokeWidth="10"
          strokeLinecap="round"
          strokeDasharray={`${(pct / 100) * c} ${c}`}
          transform="rotate(-90 60 60)"
        />
        <text x="60" y="66" textAnchor="middle" className="fill-[var(--text)] text-[30px] font-bold">
          {fmtScore(score)}
        </text>
      </svg>
      <div>
        <p className="text-sm text-muted">Quality score</p>
        <p className="text-2xl font-semibold">Grade {grade(score)}</p>
        <p className={cx("text-sm font-medium", TONE_TEXT[tone])}>{TONE_LABEL[tone]}</p>
      </div>
    </div>
  );
}

export function CategoryCard({ label, weight, score, detail }: { label: string; weight: number; score: number | null; detail?: string }) {
  const tone = scoreTone(score);
  return (
    <div className="card p-4">
      <div className="flex items-baseline justify-between">
        <p className="text-sm font-medium text-muted">{label}</p>
        <p className="text-xs text-muted">weight {weight}</p>
      </div>
      <p className={cx("mt-1 text-3xl font-semibold tabular", TONE_TEXT[tone])}>{fmtScore(score)}</p>
      <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-surface-2" aria-hidden="true">
        <div
          className={cx("h-full rounded-full", tone === "good" ? "bg-good" : tone === "warn" ? "bg-warn" : tone === "bad" ? "bg-bad" : "bg-border")}
          style={{ width: `${score ?? 0}%` }}
        />
      </div>
      <p className="mt-2 text-xs text-muted">{detail || TONE_LABEL[tone]}</p>
    </div>
  );
}
