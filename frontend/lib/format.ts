export function scoreTone(score: number | null | undefined): "good" | "warn" | "bad" | "none" {
  if (score === null || score === undefined) return "none";
  if (score >= 80) return "good";
  if (score >= 60) return "warn";
  return "bad";
}

export function grade(score: number | null | undefined): string {
  if (score === null || score === undefined) return "n/a";
  if (score >= 90) return "A";
  if (score >= 80) return "B";
  if (score >= 70) return "C";
  if (score >= 60) return "D";
  return "F";
}

export function fmtScore(score: number | null | undefined): string {
  return score === null || score === undefined ? "–" : Math.round(score).toString();
}

export function fmtMs(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return "–";
  if (ms >= 10_000) return `${(ms / 1000).toFixed(0)} s`;
  if (ms >= 1000) return `${(ms / 1000).toFixed(1)} s`;
  return `${Math.round(ms)} ms`;
}

export function fmtBytes(b: number | null | undefined): string {
  if (b === null || b === undefined) return "–";
  if (b >= 1_000_000) return `${(b / 1_000_000).toFixed(1)} MB`;
  if (b >= 1000) return `${(b / 1000).toFixed(1)} kB`;
  return `${b} B`;
}

export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return "–";
  const d = new Date(iso.endsWith("Z") || iso.includes("+") ? iso : iso + "Z");
  return d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

export function durationBetween(a: string | null, b: string | null): string {
  if (!a || !b) return "–";
  const ms = new Date(b.endsWith("Z") ? b : b + "Z").getTime() - new Date(a.endsWith("Z") ? a : a + "Z").getTime();
  return fmtMs(ms);
}

export function pathOf(url: string): string {
  try {
    const u = new URL(url);
    return u.pathname + u.search;
  } catch {
    return url;
  }
}

export const SEVERITY_ORDER: Record<string, number> = { critical: 0, high: 1, medium: 2, low: 3 };
