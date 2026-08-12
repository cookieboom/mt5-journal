// Pure helpers for the Phase B chart. Time stays epoch-ms (broker SERVER = UTC);
// divide by 1000 only when feeding lightweight-charts. Rule 4: liveLines draws
// real prices only (skips null = unknown and 0.0 = none set).
import type { Candle, CandlesResponse, LivePosition } from "./types";

export type Timeframe = "M1" | "M5" | "M15" | "H1" | "H4" | "D1";
export const TIMEFRAMES: Timeframe[] = ["M1", "M5", "M15", "H1", "H4", "D1"];

export type Sym = "XAUUSDc" | "BTCUSDc" | "EURUSDc";
export const SYMBOLS: Sym[] = ["XAUUSDc", "BTCUSDc", "EURUSDc"];

const MIN = 60_000;
const TF_MS: Record<Timeframe, number> = {
  M1: 1 * MIN, M5: 5 * MIN, M15: 15 * MIN, H1: 60 * MIN, H4: 240 * MIN, D1: 1440 * MIN,
};

export function timeframeMs(tf: Timeframe): number {
  return TF_MS[tf];
}

export function toSeconds(ms: number): number {
  return Math.floor(ms / 1000);
}

// Start of the bar containing `ms`. Buckets are epoch-aligned, exactly as the
// backend's `domain.resample.bucket_start` is — the two must agree, or a
// window computed here would not line up with the stored bar times.
export function bucketStart(ms: number, tf: Timeframe): number {
  const size = timeframeMs(tf);
  return Math.floor(ms / size) * size;
}

export function initialWindow(tf: Timeframe, nowMs: number, bars = 300): [number, number] {
  return [nowMs - timeframeMs(tf) * bars, nowMs];
}

export function olderWindow(currentFromMs: number, tf: Timeframe, bars = 300): [number, number] {
  return [currentFromMs - timeframeMs(tf) * bars, currentFromMs - 1];
}

// A fetch that came back empty means the requested window fell entirely inside a
// market-closed gap (weekend/holiday) — the newest real bar sits further back
// than the window's left edge. Widen backward by DOUBLING the span (same right
// edge `to`) so the next fetch reaches past the gap into real data. Returns the
// next [from, to] to try, or null once the span already covers `maxSpanMs`
// (give up: no data within reach — never loop forever into empty history).
// This is why M1 was blank on a Saturday while wider timeframes rendered: their
// 300-bar windows already spanned past the weekend, M1's 5-hour window did not.
export function backfillWindow(
  from: number, to: number, maxSpanMs: number,
): [number, number] | null {
  const span = to - from;
  if (span >= maxSpanMs) return null;
  const nextSpan = Math.min(span * 2, maxSpanMs);
  return [to - nextSpan, to];
}

export function mergeCandles(existing: Candle[], incoming: Candle[]): Candle[] {
  const m = new Map<number, Candle>();
  for (const c of existing) m.set(c.time_msc, c);
  for (const c of incoming) m.set(c.time_msc, c); // incoming wins on collision
  return [...m.values()].sort((a, b) => a.time_msc - b.time_msc);
}

// Bound the in-memory array: keep the newest `maxBars` bars, drop the oldest.
// Live-correct — the now side is always retained. Assumes ascending order
// (mergeCandles guarantees it). Returns the same array when under the cap so
// callers can skip a state update.
export function capCandles(candles: Candle[], maxBars: number): Candle[] {
  if (candles.length <= maxBars) return candles;
  return candles.slice(candles.length - maxBars);
}

export function isNowVisible(
  lastBarMs: number | null, visibleToMs: number | null, tf: Timeframe,
): boolean {
  if (lastBarMs === null || visibleToMs === null) return false;
  return visibleToMs >= lastBarMs - timeframeMs(tf); // right edge within one bar of last
}

// Time left in the bar forming right now, TradingView-style: "MM:SS", or
// "H:MM:SS" once a bar is an hour or longer. Buckets are epoch-aligned exactly
// as the backend's bucket_start is, so this needs no bar data — the clock alone
// says when the current bar closes.
export function barCloseCountdown(nowMs: number, tf: Timeframe): string {
  const size = timeframeMs(tf);
  const left = size - (((nowMs % size) + size) % size);
  const s = Math.ceil(left / 1000);
  const p = (n: number) => String(n).padStart(2, "0");
  const hh = Math.floor(s / 3600);
  return hh > 0 ? `${hh}:${p(Math.floor((s % 3600) / 60))}:${p(s % 60)}`
                : `${p(Math.floor(s / 60))}:${p(s % 60)}`;
}

export const LINE_COLORS ={ sl: "#fb7185", tp: "#34d399", entry: "#9a97c4" };

export type LiveLineKind = "entry" | "sl" | "tp";

export function liveLines(
  pos: LivePosition,
): { kind: LiveLineKind; price: number; color: string; title: string }[] {
  const out: { kind: LiveLineKind; price: number; color: string; title: string }[] = [];
  const add = (kind: LiveLineKind, v: number | null, color: string, title: string) => {
    if (v !== null && v !== undefined && Math.abs(v) > 1e-9) out.push({ kind, price: v, color, title });
  };
  add("entry", pos.open_price, LINE_COLORS.entry, `entry #${pos.position_id}`);
  add("sl", pos.sl, LINE_COLORS.sl, `SL #${pos.position_id}`);
  add("tp", pos.tp, LINE_COLORS.tp, `TP #${pos.position_id}`);
  return out;
}

// Merge the single realtime forming bar into a sorted candle array: replace the
// last bar if it shares time_msc, append if it is EXACTLY one bar-interval
// newer, ignore otherwise. Returns a new array; never mutates input.
//
// The moment a bar closes, `forming` (the live poll) advances to the next
// bucket immediately — but `candles` (data.candles, via loadUpTo) only picks
// up the just-closed bar once its own async fetch resolves, a bit later.
// During that gap forming.time_msc sits MORE than one interval ahead of the
// last historical bar. Appending it anyway would splice the brand-new,
// barely-started next bar's tiny OHLC directly after the OLD last bar —
// visually indistinguishable from "the just-closed bar's shape changing the
// instant it becomes historical," since the wrong bar is shown in that slot
// until the fetch resolves and this runs again. Requiring an exact
// one-interval gap makes that state a no-op (just show `candles` as-is)
// instead of a wrong bar.
export function mergeForming(candles: Candle[], forming: Candle | null, intervalMs: number): Candle[] {
  if (!forming) return candles;
  if (candles.length === 0) return [forming];
  const last = candles[candles.length - 1];
  if (forming.time_msc === last.time_msc) return [...candles.slice(0, -1), forming];
  if (forming.time_msc === last.time_msc + intervalMs) return [...candles, forming];
  return candles;
}

// Mirrors of `execute.FEED_STALE_MS` / `execute.PRICE_REF_STOP_FRACTION`. The
// server owns these — it is what actually refuses the write. They are repeated
// here because the button has to disarm BEFORE the click, and the numbers must
// match or the two verdicts disagree: that disagreement is the whole bug this
// pair of constants exists to close. Change one, change the other.
export const FEED_STALE_MS = 15_000;
export const PRICE_REF_STOP_FRACTION = 0.25;

// Is the price an order would be SIZED against fresh enough to commit to?
//
// The order's volume is frozen at enqueue (execute.enqueue_open), and the
// executor's re-validation only catches a stop that has ended up on the wrong
// SIDE — not a size that no longer matches the budget. So a reference price the
// market has left produces a real, silently wrong lot, bounded only by the 5%
// ceiling. Returns the reason to show the human, or null when it is safe.
//
// This is the browser half of `execute._check_feed_fresh`, and it checks the
// same four things in the same order, because anything it lets through the
// server refuses with a 400 the human cannot act on from a chart that still
// looks alive:
//   1. `journal live` is not beating at all (`feedLive`);
//   2. it beats, but nobody is refreshing THIS symbol's forming row
//      (`formingUpdatedMs`) — a lapsed watch, or the bridge gone blind;
//   3. the feed is fine but the price the lot is sized from is not the one the
//      server sees (`priceRef` vs `formingClose`);
//   4. the shown bar has simply stopped advancing (the original 2× timeframe
//      backstop, which needs nothing from the poll and so still applies when
//      the poll has not answered).
//
// `entryBarMs`/`priceRef` are the bar the ENTRY PRICE is read off — the last
// bar actually shown — not the forming bar from the poll. The two diverge
// exactly when it matters: `mergeForming` refuses to append a forming bar more
// than one interval ahead of the history, so a stalled candle fetch leaves an
// old bar on screen, and being sized off, while the poll keeps reporting a
// current one. Check 3 is the only thing that sees that.
//
// `feedLive` is tri-state: true/false are the server's heartbeat verdict, `null`
// means the chart is not polling it at all (replay/config drawer open, or the
// first poll has not answered yet). Unknown still blocks — but it must not
// accuse the daemon, which is what it used to do while the liveness badge, read
// off the same heartbeat, said `live · 1s`.
//
// The poll-derived fields are optional: before the first response there is
// nothing to compare, and `feedLive === null` has already blocked by then.
export function staleEntryReason(a: {
  feedLive: boolean | null;
  entryBarMs: number | null;
  intervalMs: number;
  nowMs: number;
  // `updated_msc` of the forming row — when the SERVER last refreshed it, which
  // is not when the price last moved. A quiet bucket is restamped by
  // `live_store.touch_forming` precisely so it does not read as a dead feed.
  formingUpdatedMs?: number | null;
  formingClose?: number | null;
  priceRef?: number | null;
  sl?: number | null;
}): string | null {
  const { feedLive, entryBarMs, intervalMs, nowMs } = a;
  if (feedLive === null) return "Status feed belum diketahui — chart belum polling harga live.";
  if (!feedLive) return "`journal live` tidak berjalan — harga acuan tidak segar.";
  if (entryBarMs === null) return "Belum ada bar sebagai harga acuan.";

  if (a.formingUpdatedMs != null && nowMs - a.formingUpdatedMs >= FEED_STALE_MS) {
    const age = Math.round((nowMs - a.formingUpdatedMs) / 1000);
    return `Feed beku — bar berjalan terakhir diperbarui ${age}s lalu.`;
  }

  // The lot is `risk / stop distance`, so the same drift matters in proportion
  // to that distance: 0.5 off a 5.0 stop is a tenth of the intended risk, off a
  // 0.5 stop it is all of it. No stop means no lot at all, so nothing to guard.
  if (a.priceRef != null && a.formingClose != null && a.sl != null) {
    const tolerance = Math.abs(a.priceRef - a.sl) * PRICE_REF_STOP_FRACTION;
    if (Math.abs(a.priceRef - a.formingClose) > tolerance) {
      return `Harga acuan ${a.priceRef} tidak cocok dengan harga terakhir yang `
        + `dilihat server (${a.formingClose}) — chart tertinggal, muat ulang halaman.`;
    }
  }

  if (nowMs - entryBarMs > 2 * intervalMs) {
    return "Harga acuan basi — bar terakhir lebih tua dari 2× timeframe.";
  }
  return null;
}

export async function fetchCandles(
  symbol: string, tf: Timeframe, fromMs: number, toMs: number,
): Promise<CandlesResponse> {
  const q = new URLSearchParams({
    symbol, timeframe: tf, from: String(Math.floor(fromMs)), to: String(Math.floor(toMs)),
  });
  const r = await fetch(`/api/candles?${q}`);
  if (!r.ok) {
    let msg = `HTTP ${r.status}`;
    try {
      const b = await r.json();
      if (b && typeof b.error === "string") msg = b.error;
    } catch {
      /* non-JSON error page — keep HTTP {status} */
    }
    throw new Error(msg);
  }
  return (await r.json()) as CandlesResponse;
}
