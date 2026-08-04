import { useCallback, useEffect, useRef, useState } from "react";
import { postJson } from "../lib/api";
import type { RiskPrefs, SizeResult } from "../lib/types";

const DEBOUNCE_MS = 150;
const DEFAULT_PREFS: RiskPrefs = { mode: "pct", value: 1 };

// Sizing lives on the server and ONLY on the server. Mirroring the formula here
// would give instant feedback and a second source of truth that drifts from the
// first — and the number it produces is a lot size on a real account.
export function useRiskSizing(input: {
  symbol: string;
  entry: number | null;
  sl: number | null;
  tp: number | null;
}) {
  const [prefs, setPrefsState] = useState<RiskPrefs>(DEFAULT_PREFS);
  const [result, setResult] = useState<SizeResult | null>(null);
  const [loading, setLoading] = useState(false);
  const seq = useRef(0);

  useEffect(() => {
    let live = true;
    fetch("/api/risk-prefs")
      .then((r) => r.json())
      .then((d) => { if (live && d?.prefs) setPrefsState(d.prefs as RiskPrefs); })
      .catch(() => { /* prefs are a convenience; defaults are fine */ });
    return () => { live = false; };
  }, []);

  const setPrefs = useCallback((p: RiskPrefs) => {
    setPrefsState(p);
    fetch("/api/risk-prefs", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(p),
    }).then((r) => r.json()).catch(() => { /* a failed save must not block sizing */ });
  }, []);

  const { symbol, entry, sl, tp } = input;

  useEffect(() => {
    // Bump first so any in-flight request from a prior run is disowned even
    // when this run returns early below — otherwise its stale answer would
    // land after us and overwrite the null we're about to set.
    const mine = ++seq.current;
    // No stop, no risk, no size. Not an error state — nothing has been asked yet.
    if (entry === null || sl === null) { setResult(null); setLoading(false); return; }
    setLoading(true);
    const t = setTimeout(async () => {
      const r = await postJson<SizeResult>("/api/size", {
        symbol, entry, sl, tp,
        risk_mode: prefs.mode, risk_value: prefs.value,
      });
      // A drag fires many of these; only the newest answer may win, or the
      // panel shows the lot for a price the line has already left.
      if (mine !== seq.current) return;
      setLoading(false);
      setResult(r.ok ? (r.data ?? null) : {
        volume: null, risk_usc: null, risk_pct: null, distance: null,
        rr: null, direction: null, error: r.error ?? "gagal menghitung ukuran",
      });
    }, DEBOUNCE_MS);
    return () => clearTimeout(t);
  }, [symbol, entry, sl, tp, prefs.mode, prefs.value]);

  return { prefs, setPrefs, result, loading };
}
