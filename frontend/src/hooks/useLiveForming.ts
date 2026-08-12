import { useEffect } from "react";
import { useApi, postJson } from "../lib/api";
import type { Candle, LiveCandle } from "../lib/types";
import type { Timeframe } from "../lib/candles";

const POLL_MS = 5000;
const WATCH_REFRESH_MS = 12_000;   // < server TTL (30s) so the watch never lapses

// Keeps a demand-driven watch alive and polls the forming bar while `enabled`
// (normal chart mode). Disabled (enabled=false) in replay/training — there is no
// live bar in the past. Passing an empty path to useApi when disabled stops both
// the poll and the watch upserts.
export function useLiveForming(symbol: string, tf: Timeframe, enabled: boolean) {
  useEffect(() => {
    if (!enabled) return;
    let alive = true;
    const ping = () => { if (alive) postJson("/api/watch", { symbol, timeframe: tf }); };
    ping();
    const id = setInterval(ping, WATCH_REFRESH_MS);
    return () => { alive = false; clearInterval(id); };
  }, [symbol, tf, enabled]);

  const path = enabled
    ? `/api/candles/live?symbol=${encodeURIComponent(symbol)}&timeframe=${tf}`
    : "";
  const { data } = useApi<LiveCandle>(path, enabled ? POLL_MS : undefined);
  const forming: Candle | null = enabled && data ? data.forming : null;
  // `null` = nothing to say about the feed: not polling, or the first poll is
  // still in flight. Only a real response makes this a verdict about the daemon.
  const live = enabled && data ? data.live : null;
  return { forming, live };
}
