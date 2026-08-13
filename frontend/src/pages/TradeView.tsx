import { useEffect, useMemo, useState } from "react";
import { useParams, useSearchParams, useNavigate } from "react-router-dom";
import { useApi } from "../lib/api";
import type { TradeDetailData, TradesData } from "../lib/types";
import { useChartData } from "../hooks/useChartData";
import { useChartPrefs } from "../hooks/useChartPrefs";
import { useDrawings } from "../hooks/useDrawings";
import CandleChart from "../components/CandleChart";
import AnnotationForm from "../components/AnnotationForm";
import TagEditor from "../components/TagEditor";
import { money, rmult, price, wib, dur } from "../lib/format";
import { tradeLines, navNeighbors, pickTf } from "../lib/tradeView";
import type { SeriesMarker, Time } from "lightweight-charts";
import { timeframeMs, toSeconds, type Sym } from "../lib/candles";
import { clipToCursor } from "../lib/replay";

function Fact({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex justify-between gap-4 py-1 border-b border-white/5 text-title">
      <span className="text-muted">{label}</span><span className="num text-right">{children}</span>
    </div>
  );
}
const dash = <span className="text-muted">—</span>;

export default function TradeView() {
  const { id } = useParams();
  const nav = useNavigate();
  const [params] = useSearchParams();
  const { settings } = useChartPrefs();
  const { data, reload } = useApi<TradeDetailData>(`/api/trades/${id}`);

  // Filter-aware neighbor list (same query the Trades page used).
  const listQ = params.toString();
  const { data: list } = useApi<TradesData>(`/api/trades${listQ ? `?${listQ}` : ""}`);
  const neighbors = useMemo(
    () => (list ? navNeighbors(list.trades, Number(id)) : { prevId: null, nextId: null, index: -1 }),
    [list, id],
  );

  const t = data?.trade;
  const tf = t ? pickTf(t.duration_s) : "M5";
  const anchor = t?.open_time_msc;
  const chart = useChartData(t?.symbol ?? "XAUUSDc", tf, 60, 3000, anchor);
  // Forward-load past the exit so context after the trade is visible.
  useEffect(() => {
    if (t?.close_time_msc != null) chart.loadUpTo(t.close_time_msc + timeframeMs(tf) * 15);
  }, [t?.close_time_msc, tf, chart.loadUpTo]);

  const overlay = useMemo(() => (t ? tradeLines(t) : undefined), [t]);

  const markers = useMemo(() => {
    if (!t) return undefined;
    const m: SeriesMarker<Time>[] = [];
    const isBuy = t.direction.toLowerCase() === "buy";
    const upColor = settings.colors.up;
    const downColor = settings.colors.down;

    const findTime = (ms: number) => {
      const c = chart.candles;
      if (!c.length) return toSeconds(ms) as Time;
      for (let i = c.length - 1; i >= 0; i--) {
        if (c[i].time_msc <= ms) return toSeconds(c[i].time_msc) as Time;
      }
      return toSeconds(c[0].time_msc) as Time;
    };

    // Entry Marker
    m.push({
      time: findTime(t.open_time_msc),
      position: isBuy ? "belowBar" : "aboveBar",
      color: isBuy ? upColor : downColor,
      shape: isBuy ? "arrowUp" : "arrowDown",
    });

    // Exit Marker
    if (t.close_time_msc != null) {
      m.push({
        time: findTime(t.close_time_msc),
        position: isBuy ? "aboveBar" : "belowBar",
        color: isBuy ? downColor : upColor,
        shape: isBuy ? "arrowDown" : "arrowUp",
      });
    }

    return m;
  }, [t, settings.colors, chart.candles]);

  const fitToRange = useMemo(() => {
    if (!t) return undefined;
    return { startMs: t.open_time_msc, endMs: t.close_time_msc ?? t.open_time_msc };
  }, [t]);

  // Read-only: the viewer inspects past trades, it does not annotate them.
  const drawings = useDrawings(t?.symbol ?? "", null, !!t);
  const drawingsProp = useMemo(() => ({
    items: drawings.items,
    editable: false,
    onAdd: () => {}, onUpdate: () => {}, onDelete: () => {}, onClearAll: () => {},
  }), [drawings.items]);

  // --- Optional playback reveal (Task 10) -------------------------------
  // Pure visual reveal: no evaluator, no fills. cursor === null means "show
  // the full window" (the default). Play/Step move the cursor forward from a
  // few bars before entry; clipToCursor (lib/replay.ts) hides everything past it.
  const [cursor, setCursor] = useState<number | null>(null);
  const [playing, setPlaying] = useState(false);
  const startMs = (t?.open_time_msc ?? 0) - timeframeMs(tf) * 10; // a few bars before entry
  const shown = cursor == null ? chart.candles : clipToCursor(chart.candles, cursor);

  useEffect(() => {
    if (!playing || cursor == null) return;
    const iv = setInterval(() => {
      setCursor((c) => {
        const next = (c ?? startMs) + timeframeMs(tf);
        const last = chart.lastBarMs ?? next;
        if (next >= last) { setPlaying(false); return null; } // reached the end -> full view
        return next;
      });
    }, 600);
    return () => clearInterval(iv);
  }, [playing, cursor, tf, chart.lastBarMs, startMs]);

  const goto = (pid: number | null) => { if (pid != null) nav(`/trades/${pid}/view${listQ ? `?${listQ}` : ""}`); };

  // Keyboard prev/next: ArrowLeft -> older neighbor, ArrowRight -> newer neighbor.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "ArrowLeft") goto(neighbors.prevId);
      else if (e.key === "ArrowRight") goto(neighbors.nextId);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [neighbors.prevId, neighbors.nextId, listQ]);

  if (!data || !t) return <div className="text-muted p-6">Memuat…</div>;
  const pnl = t.net_profit ?? 0;

  return (
    <div className="flex gap-3 h-[calc(100vh-2rem)]">
      <div className="relative flex-1 min-h-0 flex flex-col">
        <h1 className="text-headline font-bold mb-2">{t.symbol_base}{" "}
          <span className="uppercase">{t.direction}</span>
          <span className="text-muted num text-body ml-2">#{t.position_id}</span></h1>
        <div className="flex-1 min-h-0">
          {chart.candles.length ? (
            <CandleChart symbol={t.symbol as Sym} tf={tf} settings={settings}
              candles={shown} overlayLines={overlay} lastBarMs={chart.lastBarMs}
              onHover={() => {}} onNowVisibleChange={() => {}} onRequestOlder={chart.loadOlder}
              live={null} nowVisible={false}
              fitToRange={fitToRange}
              markers={markers}
              drawings={drawingsProp} />
          ) : (
            <div className="glass h-full flex items-center justify-center text-muted text-body">
              {chart.status === "gaveup"
                ? <span>Belum ada data ter-cache — jalankan <code>journal live</code>.</span>
                : <span>Memuat data {t.symbol} {tf}…</span>}
            </div>
          )}
        </div>
        {/* optional playback reveal — pure visual, no evaluator/fills */}
        <div className="flex justify-center items-center gap-2 mt-2 text-body">
          <button className="glass px-2 py-1" onClick={() => { setCursor(startMs); setPlaying(true); }}>
            Putar ulang
          </button>
          <button className="glass px-2 py-1"
            onClick={() => setCursor((c) => (c ?? startMs) + timeframeMs(tf))}>
            Step ▸
          </button>
          <button className="glass px-2 py-1" onClick={() => { setCursor(null); setPlaying(false); }}>
            Reset
          </button>
          <span className="text-muted num">bar: <span data-testid="bar-count">{shown.length}</span></span>
        </div>
        {/* bottom-center prev/next */}
        <div className="flex justify-center gap-3 mt-2">
          <button className="glass px-3 py-1 disabled:opacity-30" disabled={neighbors.prevId == null}
            onClick={() => goto(neighbors.prevId)}>← lebih lama</button>
          <button className="glass px-3 py-1 disabled:opacity-30" disabled={neighbors.nextId == null}
            onClick={() => goto(neighbors.nextId)}>lebih baru →</button>
        </div>
      </div>

      <aside className="w-[280px] shrink-0 overflow-y-auto flex flex-col gap-3">
        <div className="glass p-3">
          <Fact label="R-multiple">{t.r_multiple == null ? dash : rmult(t.r_multiple)}</Fact>
          <Fact label="Net"><span className={pnl > 0 ? "text-pos" : pnl < 0 ? "text-neg" : ""}>
            {money(t.net_profit, data.header.currency, { sign: true })}</span></Fact>
          <Fact label="Volume">{t.volume}</Fact>
          <Fact label="MAE (R)">{t.mae_r == null ? dash : rmult(t.mae_r)}</Fact>
          <Fact label="MFE (R)">{t.mfe_r == null ? dash : rmult(t.mfe_r)}</Fact>
          <Fact label="Entry">{price(t.open_price)}</Fact>
          <Fact label="Exit">{price(t.close_price)}</Fact>
          <Fact label="SL awal">{t.sl_initial == null ? dash : price(t.sl_initial)}</Fact>
          <Fact label="TP awal">{t.tp_initial == null ? dash : price(t.tp_initial)}</Fact>
          <Fact label="Durasi">{dur(t.duration_s)}</Fact>
          <Fact label="Dibuka">{wib(t.open_time_msc, data.header.offset_s)}</Fact>
          <Fact label="Ditutup">{wib(t.close_time_msc, data.header.offset_s)}</Fact>
          <Fact label="Session">{data.session}</Fact>
        </div>
        <AnnotationForm positionId={t.position_id} annotation={data.annotation} onSaved={reload} />
        <TagEditor positionId={t.position_id} tags={data.tags} onChanged={reload} />
        <a className="text-body text-cyan hover:underline" href={`/trades/${t.position_id}`}>← detail trade</a>
      </aside>
    </div>
  );
}
