"use client";

import { useId, useRef, type KeyboardEvent, type ReactNode } from "react";
import { cx } from "./ui";

export interface TabDef {
  key: string;
  label: string;
  count?: number;
}

/** WAI-ARIA tabs with roving focus (arrow keys, Home/End). */
export function Tabs({ tabs, active, onChange, children }: { tabs: TabDef[]; active: string; onChange: (k: string) => void; children: ReactNode }) {
  const id = useId();
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  const onKey = (e: KeyboardEvent, i: number) => {
    let n = i;
    if (e.key === "ArrowRight") n = (i + 1) % tabs.length;
    else if (e.key === "ArrowLeft") n = (i - 1 + tabs.length) % tabs.length;
    else if (e.key === "Home") n = 0;
    else if (e.key === "End") n = tabs.length - 1;
    else return;
    e.preventDefault();
    onChange(tabs[n].key);
    refs.current[n]?.focus();
  };
  return (
    <div>
      <div role="tablist" aria-label="Run details" className="flex gap-1 overflow-x-auto border-b border-border">
        {tabs.map((t, i) => {
          const sel = t.key === active;
          return (
            <button
              key={t.key}
              ref={(el) => {
                refs.current[i] = el;
              }}
              role="tab"
              type="button"
              id={`${id}-tab-${t.key}`}
              aria-selected={sel}
              aria-controls={`${id}-panel`}
              tabIndex={sel ? 0 : -1}
              onClick={() => onChange(t.key)}
              onKeyDown={(e) => onKey(e, i)}
              className={cx(
                "-mb-px whitespace-nowrap border-b-2 px-3 py-2 text-sm font-medium",
                sel ? "border-accent text-text" : "border-transparent text-muted hover:text-text",
              )}
            >
              {t.label}
              {t.count !== undefined && <span className="ml-1.5 rounded-full bg-surface-2 px-1.5 text-xs text-muted">{t.count}</span>}
            </button>
          );
        })}
      </div>
      <div role="tabpanel" id={`${id}-panel`} aria-labelledby={`${id}-tab-${active}`} tabIndex={0} className="pt-4">
        {children}
      </div>
    </div>
  );
}
