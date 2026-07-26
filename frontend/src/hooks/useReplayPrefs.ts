import { useCallback, useEffect, useState } from "react";
import {
  DEFAULT_REPLAY_PREFS, STORAGE_KEY, loadReplayPrefs, reconcileReplayPrefs,
  saveReplayPrefs, type ReplayFormPrefs,
} from "../lib/replayPrefs";

function putPrefs(s: ReplayFormPrefs): void {
  // Fire-and-forget; a failed PUT leaves localStorage as the source of truth.
  void fetch("/api/replay/prefs", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(s),
  }).catch(() => { /* offline / dev — persistence is best-effort */ });
}

// Instant localStorage render, then reconcile with the DB (authoritative) once
// on mount. `save` (called on modal submit) writes localStorage immediately and
// PUTs — no debounce, since it fires once per launch rather than per keystroke.
export function useReplayPrefs(): {
  prefs: ReplayFormPrefs;
  save: (next: ReplayFormPrefs) => void;
} {
  const [prefs, setPrefs] = useState<ReplayFormPrefs>(() => loadReplayPrefs());

  useEffect(() => {
    let alive = true;
    const localExists = (() => {
      try { return localStorage.getItem(STORAGE_KEY) !== null; } catch { return false; }
    })();
    fetch("/api/replay/prefs")
      .then((r) => (r.ok ? r.json() : null))
      .then((body: { prefs: unknown } | null) => {
        if (!alive || !body) return;
        const { settings, shouldImport } =
          reconcileReplayPrefs(loadReplayPrefs(), body.prefs, localExists);
        setPrefs(settings);
        saveReplayPrefs(settings);
        if (shouldImport) putPrefs(settings);   // seed DB from this browser
      })
      .catch(() => { /* offline / dev — keep localStorage state */ });
    return () => { alive = false; };
  }, []);

  const save = useCallback((next: ReplayFormPrefs) => {
    setPrefs(next);
    saveReplayPrefs(next);   // instant + local source of truth
    putPrefs(next);
  }, []);

  return { prefs, save };
}

export { DEFAULT_REPLAY_PREFS };
