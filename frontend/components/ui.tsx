import type { ButtonHTMLAttributes, ReactNode } from "react";

export function cx(...c: (string | false | null | undefined)[]) {
  return c.filter(Boolean).join(" ");
}

export function Button({
  variant = "secondary",
  className,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "secondary" | "danger" | "ghost" }) {
  const styles = {
    primary: "bg-accent text-accent-contrast hover:opacity-90 border-transparent",
    secondary: "bg-surface text-text border-border hover:bg-surface-2",
    danger: "bg-surface text-bad border-border hover:bg-surface-2",
    ghost: "bg-transparent text-muted border-transparent hover:bg-surface-2 hover:text-text",
  }[variant];
  return (
    <button
      type="button"
      {...props}
      className={cx(
        "inline-flex items-center justify-center gap-2 rounded-lg border px-3 py-2 text-sm font-medium transition disabled:cursor-not-allowed disabled:opacity-50",
        styles,
        className,
      )}
    />
  );
}

export function Card({ children, className, title, actions }: { children: ReactNode; className?: string; title?: string; actions?: ReactNode }) {
  return (
    <section className={cx("card p-4 sm:p-5", className)}>
      {(title || actions) && (
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          {title && <h2 className="text-base font-semibold">{title}</h2>}
          {actions}
        </div>
      )}
      {children}
    </section>
  );
}

export function Spinner({ label = "Loading" }: { label?: string }) {
  return (
    <span role="status" className="inline-flex items-center gap-2 text-sm text-muted">
      <span aria-hidden="true" className="h-4 w-4 animate-spin rounded-full border-2 border-border border-t-accent" />
      {label}…
    </span>
  );
}

export function EmptyState({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="rounded-lg border border-dashed border-border p-8 text-center">
      <p className="font-medium">{title}</p>
      {children && <div className="mt-1 text-sm text-muted">{children}</div>}
    </div>
  );
}

export function ErrorState({ error, onRetry }: { error: string; onRetry?: () => void }) {
  return (
    <div role="alert" className="rounded-lg border border-bad/40 bg-bad/5 p-4 text-sm">
      <p className="font-medium text-bad">Something went wrong</p>
      <p className="mt-1 text-text">{error}</p>
      {onRetry && (
        <Button className="mt-3" onClick={onRetry}>
          Try again
        </Button>
      )}
    </div>
  );
}

const SEV: Record<string, string> = {
  critical: "bg-bad text-bg border-bad",
  high: "bg-bad/10 text-bad border-bad/40",
  medium: "bg-warn/10 text-warn border-warn/40",
  low: "bg-surface-2 text-muted border-border",
};

export function SeverityBadge({ severity }: { severity: string }) {
  return <Badge className={SEV[severity] || SEV.low}>{severity}</Badge>;
}

const STATUS: Record<string, { cls: string; icon: string }> = {
  passed: { cls: "bg-good/10 text-good border-good/40", icon: "✓" },
  completed: { cls: "bg-good/10 text-good border-good/40", icon: "✓" },
  failed: { cls: "bg-bad/10 text-bad border-bad/40", icon: "✕" },
  error: { cls: "bg-warn/10 text-warn border-warn/40", icon: "!" },
  flaky: { cls: "bg-warn/10 text-warn border-warn/40", icon: "~" },
  warning: { cls: "bg-warn/10 text-warn border-warn/40", icon: "!" },
  degraded: { cls: "bg-warn/10 text-warn border-warn/40", icon: "◐" },
  running: { cls: "bg-accent/10 text-accent border-accent/40", icon: "●" },
  queued: { cls: "bg-surface-2 text-muted border-border", icon: "…" },
  pending: { cls: "bg-surface-2 text-muted border-border", icon: "○" },
  skipped: { cls: "bg-surface-2 text-muted border-border", icon: "–" },
  cancelled: { cls: "bg-surface-2 text-muted border-border", icon: "■" },
};

export function StatusBadge({ status }: { status: string }) {
  const s = STATUS[status] || STATUS.pending;
  return (
    <Badge className={s.cls}>
      <span aria-hidden="true">{s.icon}</span> {status}
    </Badge>
  );
}

export function Badge({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <span className={cx("inline-flex items-center gap-1 whitespace-nowrap rounded-full border px-2 py-0.5 text-xs font-medium", className)}>
      {children}
    </span>
  );
}

export function PrivacyNotice({ compact = false }: { compact?: boolean }) {
  return (
    <div className="rounded-lg border border-warn/40 bg-warn/5 p-3 text-sm" role="note">
      <p className="font-medium text-text">Only test sites you own or have permission to test.</p>
      {!compact && (
        <p className="mt-1 text-muted">
          OneClickQA uses the free Mistral AI tier, which may use submitted data for training. Only test staging or demo
          apps without real customer data. Passwords and tokens are never sent to the AI.
        </p>
      )}
    </div>
  );
}
