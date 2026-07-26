import { LINE_COLORS } from "./candles";
import type { TradeFull, TradeRow } from "./types";

const real = (v: number | null): v is number => v !== null && Math.abs(v) > 1e-9;

// Overlay price lines for the interactive viewer (mirrors liveLines/replayLines).
export function tradeLines(t: TradeFull): { price: number; color: string; title: string }[] {
  const out: { price: number; color: string; title: string }[] = [];
  if (real(t.open_price)) out.push({ price: t.open_price!, color: LINE_COLORS.entry, title: "entry" });
  if (real(t.close_price)) out.push({ price: t.close_price!, color: "#f59e0b", title: "exit" });
  if (real(t.sl_initial)) out.push({ price: t.sl_initial!, color: LINE_COLORS.sl, title: "SL" });
  if (real(t.tp_initial)) out.push({ price: t.tp_initial!, color: LINE_COLORS.tp, title: "TP" });
  return out;
}

// The list is newest-open-first (open_time_msc DESC). Visually "next" = newer
// trade (earlier index), "prev" = older trade (later index).
export function navNeighbors(
  trades: TradeRow[], id: number,
): { prevId: number | null; nextId: number | null; index: number } {
  const index = trades.findIndex((t) => t.position_id === id);
  if (index === -1) return { prevId: null, nextId: null, index: -1 };
  return {
    index,
    nextId: index > 0 ? trades[index - 1].position_id : null,
    prevId: index < trades.length - 1 ? trades[index + 1].position_id : null,
  };
}
