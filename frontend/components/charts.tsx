"use client";

import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { PerfMetric, TrendPoint } from "@/lib/types";
import { fmtMs, pathOf } from "@/lib/format";

const axis = { stroke: "var(--chart-axis)", tick: { fill: "var(--chart-ink-muted)", fontSize: 12 } };
const tooltipStyle = {
  contentStyle: { background: "var(--surface)", border: "1px solid var(--border)", borderRadius: 8, color: "var(--text)", fontSize: 12 },
  labelStyle: { color: "var(--text)", fontWeight: 600 },
  itemStyle: { color: "var(--text)" },
};

// Fixed categorical order (series identity never depends on rank).
const SERIES = [
  { key: "score", name: "Overall", color: "var(--series-1)", width: 3 },
  { key: "ui", name: "UI", color: "var(--series-2)", width: 2 },
  { key: "api", name: "API", color: "var(--series-3)", width: 2 },
  { key: "a11y", name: "Accessibility", color: "var(--series-4)", width: 2 },
  { key: "perf", name: "Performance", color: "var(--series-5)", width: 2 },
] as const;

export function TrendChart({ points }: { points: TrendPoint[] }) {
  const data = points.map((p) => ({ ...p, label: `#${p.run_id}` }));
  return (
    <div className="h-64 w-full" role="img" aria-label={`Quality score trend over ${points.length} runs`}>
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 8, right: 16, bottom: 0, left: -12 }}>
          <CartesianGrid stroke="var(--chart-grid)" vertical={false} />
          <XAxis dataKey="label" {...axis} tickLine={false} />
          <YAxis domain={[0, 100]} ticks={[0, 25, 50, 75, 100]} {...axis} tickLine={false} axisLine={false} />
          <Tooltip {...tooltipStyle} cursor={{ stroke: "var(--chart-axis)" }} />
          <Legend wrapperStyle={{ fontSize: 12, color: "var(--muted)" }} />
          {SERIES.map((s) => (
            <Line
              key={s.key}
              type="monotone"
              dataKey={s.key}
              name={s.name}
              stroke={s.color}
              strokeWidth={s.width}
              dot={{ r: 4, strokeWidth: 2, stroke: "var(--surface)", fill: s.color }}
              activeDot={{ r: 6 }}
              connectNulls
              isAnimationActive={false}
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

/** One single-series bar chart per metric (small multiples; never a dual axis). */
export function PerfBars({ metrics, metric, title, threshold }: { metrics: PerfMetric[]; metric: "lcp" | "ttfb" | "fcp"; title: string; threshold: number }) {
  const data = metrics.map((m) => ({ page: pathOf(m.page_url), value: m[metric] ?? 0 }));
  return (
    <figure className="card p-4">
      <figcaption className="mb-2 text-sm font-medium">
        {title} <span className="font-normal text-muted">· median per page · good ≤ {fmtMs(threshold)}</span>
      </figcaption>
      <div className="w-full" style={{ height: Math.max(160, data.length * 28 + 40) }}>
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data} layout="vertical" margin={{ top: 0, right: 24, bottom: 0, left: 8 }} barCategoryGap={2}>
            <CartesianGrid stroke="var(--chart-grid)" horizontal={false} />
            <XAxis type="number" {...axis} tickFormatter={(v: number) => fmtMs(v)} tickLine={false} />
            <YAxis type="category" dataKey="page" width={120} {...axis} tickLine={false} axisLine={false} />
            <Tooltip {...tooltipStyle} formatter={(v) => [fmtMs(Number(v)), title]} cursor={{ fill: "var(--surface-2)" }} />
            <Bar dataKey="value" name={title} fill="var(--series-1)" radius={[0, 4, 4, 0]} isAnimationActive={false} />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </figure>
  );
}
