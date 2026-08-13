import type { Sym, Timeframe } from "../lib/candles";
import type { Candle, HoverBar, LiveData } from "../lib/types";
import type { ChartSettings } from "../lib/chartPrefs";
import { money, price, wib } from "../lib/format";

function Row({ k, v, cls = "" }: { k: string; v: string; cls?: string }) {
  return (
    <div className="flex justify-between text-body">
      <span className="text-muted">{k}</span>
      <span className={"num " + cls}>{v}</span>
    </div>
  );
}

export default function ChartInfoPanel({
  symbol, tf, candles, hovered, live, currency, chartType,
}: {
  symbol: Sym;
  tf: Timeframe;
  candles: Candle[];
  hovered: HoverBar | null;
  live: LiveData | null;
  currency: string;
  chartType: ChartSettings["chartType"];
}) {
  const latest = candles.length ? candles[candles.length - 1] : null;
  const bar = hovered ?? latest;
  const prev = candles.length >= 2 ? candles[candles.length - 2] : null;
  const change = latest && prev ? latest.c - prev.c : null;
  const changePct = latest && prev && prev.c !== 0 ? (latest.c - prev.c) / prev.c : null;
  const mine = live?.live.positions.filter((p) => p.symbol === symbol) ?? [];

  return (
    <div className="space-y-4">
      {/* 1 — crosshair OHLC (falls back to latest) */}
      <div>
        <div className="text-muted text-meta mb-1">
          {hovered ? "Bar (kursor)" : "Bar terakhir"}
        </div>
        {bar ? (
          <>
            <div className="text-meta text-muted mb-1">{wib(bar.time_msc, 0)}</div>
            {chartType === "line" || chartType === "area" ? (
              <Row k="Harga" v={price(bar.c)} />
            ) : (
              <>
                <Row k="O" v={price(bar.o)} />
                <Row k="H" v={price(bar.h)} />
                <Row k="L" v={price(bar.l)} />
                <Row k="C" v={price(bar.c)} />
                {bar.v != null ? <Row k="V" v={String(bar.v)} /> : null}
              </>
            )}
          </>
        ) : (
          <div className="text-muted text-body">—</div>
        )}
      </div>

      {/* 2 — last price + change (last-candle close, not a tick) */}
      <div>
        <div className="text-muted text-meta mb-1">Harga terakhir</div>
        <div className="num text-display">{latest ? price(latest.c) : "—"}</div>
        {change !== null && (
          <div className={"text-body num " + (change >= 0 ? "text-pos" : "text-neg")}>
            {change >= 0 ? "+" : ""}{price(change)}
            {changePct !== null ? ` (${(changePct * 100).toFixed(2)}%)` : ""}
          </div>
        )}
        <div className="text-label text-muted mt-0.5">close bar terakhir · bukan tick live</div>
      </div>

      {/* 3 — live position block */}
      {mine.length > 0 && (
        <div>
          <div className="text-muted text-meta mb-1">Posisi live</div>
          {mine.map((p) => (
            <div key={p.position_id} className="glass p-2 mb-2 space-y-0.5">
              <div className="flex justify-between text-body">
                <span className={p.direction === "buy" ? "text-pos" : "text-neg"}>
                  {p.direction.toUpperCase()} {p.volume}
                </span>
                <span className={"num " + ((p.profit ?? 0) >= 0 ? "text-pos" : "text-neg")}>
                  {money(p.profit, currency, { sign: true })}
                </span>
              </div>
              <Row k="entry" v={price(p.open_price)} />
              <Row k="now" v={price(p.price_current)} />
              <Row k="SL" v={price(p.sl)} cls="text-neg" />
              <Row k="TP" v={price(p.tp)} cls="text-pos" />
            </div>
          ))}
        </div>
      )}

      {/* 4 — symbol/tf meta */}
      <div className="text-meta text-muted border-t border-panel-border pt-2 space-y-0.5">
        <Row k="symbol" v={symbol} />
        <Row k="timeframe" v={tf} />
        <Row k="bars" v={String(candles.length)} />
      </div>
    </div>
  );
}
