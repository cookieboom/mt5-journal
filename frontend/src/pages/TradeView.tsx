import { useEffect, useMemo } from "react";
import { useParams, useSearchParams, useNavigate } from "react-router-dom";
import { useApi } from "../lib/api";
import type { TradeDetailData, TradesData } from "../lib/types";
import { useChartData } from "../hooks/useChartData";
import { useChartPrefs } from "../hooks/useChartPrefs";
import CandleChart from "../components/CandleChart";
import AnnotationForm from "../components/AnnotationForm";
import TagEditor from "../components/TagEditor";
import { money, rmult, price, wib, dur } from "../lib/format";
import { tradeLines, navNeighbors, pickTf } from "../lib/tradeView";
import { timeframeMs, type Sym } from "../lib/candles";

function Fact({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex justify-between gap-4 py-1 border-b border-white/5 text-[13px]">
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
  const goto = (pid: number | null) => { if (pid != null) nav(`/trades/${pid}/view${listQ ? `?${listQ}` : ""}`); };

  if (!data || !t) return <div className="text-muted p-6">Memuat…</div>;
  const pnl = t.net_profit ?? 0;

  return (
    <div className="flex gap-3 h-[calc(100vh-2rem)]">
      <div className="relative flex-1 min-h-0 flex flex-col">
        <h1 className="text-[16px] font-bold mb-2">{t.symbol_base}{" "}
          <span className="uppercase">{t.direction}</span>
          <span className="text-muted num text-[12px] ml-2">#{t.position_id}</span></h1>
        <div className="flex-1 min-h-0">
          {chart.candles.length ? (
            <CandleChart symbol={t.symbol as Sym} tf={tf} settings={settings}
              candles={chart.candles} overlayLines={overlay} lastBarMs={chart.lastBarMs}
              onHover={() => {}} onNowVisibleChange={() => {}} onRequestOlder={chart.loadOlder}
              live={null} nowVisible={false} />
          ) : (
            <div className="glass h-full flex items-center justify-center text-muted text-sm">
              {chart.status === "gaveup"
                ? <span>Belum ada data ter-cache — jalankan <code>journal live</code>.</span>
                : <span>⌛ Memuat data {t.symbol} {tf}…</span>}
            </div>
          )}
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
        <a className="text-[12px] text-cyan hover:underline" href={`/trades/${t.position_id}`}>← detail trade</a>
      </aside>
    </div>
  );
}
