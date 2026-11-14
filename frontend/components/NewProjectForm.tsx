"use client";

import { useId, useState, type FormEvent } from "react";
import { api } from "@/lib/api";
import type { Project } from "@/lib/types";
import { Button, PrivacyNotice } from "./ui";

const input =
  "mt-1 block w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm text-text placeholder:text-muted focus:border-accent";

export function NewProjectForm({ onCreated, onCancel }: { onCreated: (p: Project) => void; onCancel?: () => void }) {
  const id = useId();
  const [form, setForm] = useState({
    name: "",
    base_url: "",
    openapi_url: "",
    username: "",
    password: "",
    login_url: "",
    max_pages: 15,
  });
  const [consent, setConsent] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const set = (k: keyof typeof form) => (e: { target: { value: string } }) =>
    setForm((f) => ({ ...f, [k]: k === "max_pages" ? Number(e.target.value) : e.target.value }));

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    if (!consent) {
      setError("Please confirm the consent box to continue.");
      return;
    }
    setBusy(true);
    try {
      const p = await api<Project>("/projects", {
        method: "POST",
        body: JSON.stringify({
          name: form.name || new URL(form.base_url).host,
          base_url: form.base_url,
          openapi_url: form.openapi_url || null,
          username: form.username || null,
          password: form.password || null,
          login_url: form.login_url || null,
          max_pages: form.max_pages,
          consent: true,
        }),
      });
      onCreated(p);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="space-y-4" aria-labelledby={`${id}-title`}>
      <h2 id={`${id}-title`} className="text-lg font-semibold">
        New project
      </h2>
      <PrivacyNotice />
      <div className="grid gap-4 sm:grid-cols-2">
        <label className="block text-sm font-medium sm:col-span-2">
          App URL <span className="text-bad">*</span>
          <input className={input} type="url" required placeholder="https://staging.example.com" value={form.base_url} onChange={set("base_url")} />
        </label>
        <label className="block text-sm font-medium">
          Project name
          <input className={input} placeholder="My app (staging)" value={form.name} onChange={set("name")} />
        </label>
        <label className="block text-sm font-medium">
          OpenAPI spec URL <span className="font-normal text-muted">(optional)</span>
          <input className={input} placeholder="/openapi.json" value={form.openapi_url} onChange={set("openapi_url")} />
        </label>
        <fieldset className="rounded-lg border border-border p-3 sm:col-span-2">
          <legend className="px-1 text-sm font-medium">
            Test login <span className="font-normal text-muted">(optional, stored encrypted, never sent to the AI)</span>
          </legend>
          <div className="grid gap-3 sm:grid-cols-3">
            <label className="block text-sm">
              Username / email
              <input className={input} autoComplete="off" value={form.username} onChange={set("username")} />
            </label>
            <label className="block text-sm">
              Password
              <input className={input} type="password" autoComplete="new-password" value={form.password} onChange={set("password")} />
            </label>
            <label className="block text-sm">
              Login page URL <span className="text-muted">(optional)</span>
              <input className={input} placeholder="/login" value={form.login_url} onChange={set("login_url")} />
            </label>
          </div>
        </fieldset>
        <label className="block text-sm font-medium">
          Max pages to crawl
          <input className={input} type="number" min={1} max={50} value={form.max_pages} onChange={set("max_pages")} />
        </label>
      </div>
      <label className="flex items-start gap-3 rounded-lg border border-border bg-surface-2 p-3 text-sm">
        <input type="checkbox" className="mt-1 h-4 w-4 accent-[var(--accent)]" checked={consent} onChange={(e) => setConsent(e.target.checked)} required />
        <span>
          I own this site or have permission to test it, and I understand the free Mistral tier may use submitted data for
          training. I will only test staging/demo apps without real customer data.
        </span>
      </label>
      {error && (
        <p role="alert" className="text-sm text-bad">
          {error}
        </p>
      )}
      <div className="flex gap-2">
        <Button type="submit" variant="primary" disabled={busy || !consent}>
          {busy ? "Creating…" : "Create project"}
        </Button>
        {onCancel && (
          <Button variant="ghost" onClick={onCancel}>
            Cancel
          </Button>
        )}
      </div>
    </form>
  );
}
