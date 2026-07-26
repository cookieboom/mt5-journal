import { useCallback, useEffect, useRef, useState } from "react";
import { DEFAULT_TRADE_PNG, fromApi, toApi, type TradePngSettings } from "../lib/tradePngPrefs";

const DEBOUNCE_MS = 400;

// DB is authoritative (the server renders the PNG from it). GET on mount, then
// debounced PUT on change; `version` (last updated_ms) busts the <img> cache.
export function useTradePngPrefs(): {
  settings: TradePngSettings; update: (n: TradePngSettings) => void; version: number;
} {
  const [settings, setSettings] = useState<TradePngSettings>(DEFAULT_TRADE_PNG);
  const [version, setVersion] = useState(0);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    let alive = true;
    fetch("/api/trades/png-prefs")
      .then((r) => (r.ok ? r.json() : null))
      .then((b: { prefs: unknown } | null) => {
        if (!alive || !b || b.prefs == null) return;
        setSettings(fromApi(b.prefs));
        setVersion((v) => v + 1);   // reflect the loaded value in the img key
      })
      .catch(() => { /* offline/dev — keep defaults */ });
    return () => { alive = false; };
  }, []);

  const update = useCallback((next: TradePngSettings) => {
    setSettings(next);
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => {
      fetch("/api/trades/png-prefs", {
        method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(toApi(next)),
      })
        .then((r) => (r.ok ? r.json() : null))
        .then((b: { updated_ms: number } | null) => { if (b) setVersion(b.updated_ms); })
        .catch(() => { /* offline/dev */ });
    }, DEBOUNCE_MS);
  }, []);

  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);
  return { settings, update, version };
}
