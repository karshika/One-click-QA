"use client";

import { useEffect, useRef, useState } from "react";
import { eventsUrl } from "./api";
import type { RunEventMsg } from "./types";

/** Subscribes to the run's SSE stream. Calls onChange whenever a stage/run status changes. */
export function useRunEvents(runId: number | null, active: boolean, onChange: () => void) {
  const [log, setLog] = useState<RunEventMsg[]>([]);
  const [connected, setConnected] = useState(false);
  const cb = useRef(onChange);
  useEffect(() => {
    cb.current = onChange;
  }, [onChange]);

  useEffect(() => {
    if (runId === null || !active) return;
    const es = new EventSource(eventsUrl(runId));
    const handle = (e: MessageEvent) => {
      try {
        const msg = JSON.parse(e.data) as RunEventMsg;
        setLog((prev) => (prev.some((p) => p.id === msg.id) ? prev : [...prev.slice(-300), msg]));
        if (msg.type === "stage_status" || msg.type === "run_status") cb.current();
      } catch {
        /* ignore malformed */
      }
    };
    es.onopen = () => setConnected(true);
    es.addEventListener("stage_status", handle);
    es.addEventListener("run_status", handle);
    es.addEventListener("log", handle);
    es.addEventListener("end", () => {
      es.close();
      setConnected(false);
      cb.current();
    });
    es.onerror = () => setConnected(false);
    return () => es.close();
  }, [runId, active]);

  return { log, connected };
}
