import { useCallback, useEffect, useState } from "react";

export interface PaperPrefs { mode: "real" | "paper"; accountId: number | null }

const KEY = "paper";
const DEFAULTS: PaperPrefs = { mode: "real", accountId: null };

function load(): PaperPrefs {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return DEFAULTS;
    const p = JSON.parse(raw) as Partial<PaperPrefs>;
    return {
      mode: p.mode === "paper" ? "paper" : "real",
      accountId: typeof p.accountId === "number" ? p.accountId : null,
    };
  } catch { return DEFAULTS; }
}

/** Which account the chart's buttons aim at, and which paper account is
 *  selected. Same shape as `useChartPrefs`: localStorage renders instantly, the
 *  DB reconciles once on mount, every change writes through to both. Its own
 *  `paper` key — this is not chart appearance. */
export function usePaperPrefs() {
  const [prefs, setPrefs] = useState<PaperPrefs>(load);

  useEffect(() => {
    let alive = true;
    // localStorage wins on a fresh browser with nothing stored server-side; the
    // DB wins otherwise, so a mode set on another browser follows the user.
    fetch("/api/prefs/paper")
      .then((r) => (r.ok ? r.json() : null))
      .then((body: { prefs: PaperPrefs | null } | null) => {
        if (!alive || !body?.prefs) return;
        setPrefs({
          mode: body.prefs.mode === "paper" ? "paper" : "real",
          accountId: typeof body.prefs.accountId === "number" ? body.prefs.accountId : null,
        });
      })
      .catch(() => { /* offline / dev — keep the local value */ });
    return () => { alive = false; };
  }, []);

  const update = useCallback((next: Partial<PaperPrefs>) => {
    setPrefs((cur) => {
      const merged = { ...cur, ...next };
      try { localStorage.setItem(KEY, JSON.stringify(merged)); } catch { /* private mode */ }
      void fetch("/api/prefs/paper", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(merged),
      }).catch(() => { /* offline / dev */ });
      return merged;
    });
  }, []);

  return { prefs, update };
}
