"use client";

import { Card, ErrorState, PrivacyNotice, Spinner, StatusBadge } from "@/components/ui";
import { API_URL } from "@/lib/api";
import { useFetch } from "@/lib/useFetch";
import type { PublicSettings } from "@/lib/types";

export default function SettingsPage() {
  const { data, error, reload } = useFetch<PublicSettings>("/settings");
  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold">Settings</h1>
      <PrivacyNotice />
      {error && <ErrorState error={error} onRetry={reload} />}
      {!data && !error && <Spinner />}
      {data && (
        <div className="grid gap-4 md:grid-cols-2">
          <Card title="Mistral AI">
            <dl className="grid grid-cols-2 gap-y-2 text-sm">
              <dt className="text-muted">Status</dt>
              <dd>
                <StatusBadge status={data.llm_configured ? "passed" : "skipped"} />{" "}
                {data.llm_configured ? "API key configured" : "No key: deterministic mode"}
              </dd>
              {Object.entries(data.models).map(([role, model]) => (
                <div key={role} className="contents">
                  <dt className="capitalize text-muted">{role} model</dt>
                  <dd className="font-mono text-xs">{model}</dd>
                </div>
              ))}
              <dt className="text-muted">Rate limit</dt>
              <dd>
                {data.rps} req/s · {data.tpm.toLocaleString()} tokens/min
              </dd>
              <dt className="text-muted">Budget</dt>
              <dd>
                {data.max_llm_calls_per_run} calls/run · {data.llm_calls_today} of {data.max_llm_calls_per_day} used today
              </dd>
              <dt className="text-muted">Vision check</dt>
              <dd>{data.vision_check_enabled ? "enabled (experimental)" : "off"}</dd>
            </dl>
            {!data.llm_configured && (
              <p className="mt-3 text-sm text-muted">
                Get a free key at{" "}
                <a className="text-accent underline" href="https://console.mistral.ai" target="_blank" rel="noreferrer">
                  console.mistral.ai
                </a>{" "}
                and set <code>MISTRAL_API_KEY</code> in <code>.env</code>, then restart the backend.
              </p>
            )}
          </Card>
          <Card title="Crawling & security">
            <dl className="grid grid-cols-2 gap-y-2 text-sm">
              <dt className="text-muted">Default max pages</dt>
              <dd>{data.default_max_pages}</dd>
              <dt className="text-muted">Default max depth</dt>
              <dd>{data.default_max_depth}</dd>
              <dt className="text-muted">Local/private targets</dt>
              <dd>{data.allow_local_targets ? "allowed (ALLOW_LOCAL_TARGETS=true)" : "blocked (SSRF guard)"}</dd>
              <dt className="text-muted">Backend</dt>
              <dd className="font-mono text-xs">{API_URL}</dd>
            </dl>
            <p className="mt-3 text-sm text-muted">Settings are configured with environment variables; see <code>.env.example</code>.</p>
          </Card>
        </div>
      )}
    </div>
  );
}
