import { useCallback, useEffect, useRef, useState } from "react";
import {
  DEFAULT_SETTINGS, STORAGE_KEY, loadChartSettings, reconcilePrefs,
  saveChartSettings, type ChartSettings,
} from "../lib/chartPrefs";

const DEBOUNCE_MS = 400;

function putPrefs(s: ChartSettings): void {
  // Fire-and-forget; a failed PUT leaves localStorage as the source of truth.
  void fetch("/api/chart/prefs", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(s),
  }).catch(() => { /* offline / dev — appearance-only */ });
}

// Instant localStorage render, then reconcile with the DB (authoritative), then
// write-through (localStorage immediately + debounced PUT) on every change.
export function useChartPrefs(): {
  settings: ChartSettings;
  update: (next: ChartSettings) => void;
  reset: () => void;
} {
  const [settings, setSettings] = useState<ChartSettings>(() => loadChartSettings());
  const putTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // DB reconcile once on mount.
  useEffect(() => {
    let alive = true;
    const localExists = (() => {
      try { return localStorage.getItem(STORAGE_KEY) !== null; } catch { return false; }
    })();
    fetch("/api/chart/prefs")
      .then((r) => (r.ok ? r.json() : null))
      .then((body: { prefs: unknown } | null) => {
        if (!alive || !body) return;
        const { settings: next, shouldImport } =
          reconcilePrefs(loadChartSettings(), body.prefs, localExists);
        setSettings(next);
        saveChartSettings(next);
        if (shouldImport) putPrefs(next);   // seed DB from this browser
      })
      .catch(() => { /* offline / dev — keep localStorage state */ });
    return () => { alive = false; };
  }, []);

  const update = useCallback((next: ChartSettings) => {
    setSettings(next);
    saveChartSettings(next);               // instant + local source of truth
    if (putTimer.current) clearTimeout(putTimer.current);
    putTimer.current = setTimeout(() => putPrefs(next), DEBOUNCE_MS);
  }, []);

  const reset = useCallback(() => update({ ...DEFAULT_SETTINGS }), [update]);

  // Flush a pending debounced PUT on unmount so a quick change isn't lost.
  useEffect(() => () => { if (putTimer.current) clearTimeout(putTimer.current); }, []);

  return { settings, update, reset };
}
