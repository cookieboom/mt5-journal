import { useState, useEffect } from "react";
import { useParams } from "react-router-dom";
import { useApi } from "../lib/api";
import { TradeDetailData } from "../lib/types";
import { money, rmult, price, wib, dur } from "../lib/format";
import AnnotationForm from "../components/AnnotationForm";
import TagEditor from "../components/TagEditor";
import TradePngPanel from "../components/TradePngPanel";
import { useTradePngPrefs } from "../hooks/useTradePngPrefs";

function Fact({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex justify-between gap-4 py-1.5 border-b border-white/5 text-[13px]">
      <span className="text-muted">{label}</span>
      <span className="num text-right">{children}</span>
    </div>
  );
}
const unknown = <span className="text-muted" title="tidak diketahui — rule 4: NULL ≠ 0">unknown</span>;
const na = <span className="text-muted" title="perlu SL awal diketahui untuk menghitung R">n/a</span>;

export default function TradeDetail() {
  const { id } = useParams();
  const { data, error, loading, reload } = useApi<TradeDetailData>(`/api/trades/${id}`);
  const [chartFailed, setChartFailed] = useState(false);
  const png = useTradePngPrefs();
  useEffect(() => setChartFailed(false), [id, png.version]);
  if (loading) return <div className="text-muted p-6">Memuat…</div>;
  if (error) return <div className="glass p-6 text-neg">Gagal memuat: {error}</div>;
  if (!data) return null;
  const { header, trade, annotation, tags, session, is_ea, chartable } = data;
  const pnl = trade.net_profit ?? 0;

  return (
    <div>
      <h1 className="text-[18px] font-bold tracking-tight mb-4">
        {trade.symbol_base} <span className="uppercase">{trade.direction}</span>
        <span className="text-muted num text-[13px] ml-2">#{trade.position_id}</span>
      </h1>

      <div className="grid md:grid-cols-2 gap-4">
        <div className="glass p-4">
          <h2 className="text-[13px] font-semibold uppercase tracking-wider text-muted mb-2">Trade</h2>
          <Fact label="Status">{trade.status}</Fact>
          <Fact label="Source">{is_ea ? `EA (magic ${trade.magic})` : "Discretionary"}</Fact>
          <Fact label="Session">{session}</Fact>
          <Fact label="Dibuka">{wib(trade.open_time_msc, header.offset_s)}</Fact>
          <Fact label="Ditutup">{wib(trade.close_time_msc, header.offset_s)}</Fact>
          <Fact label="Durasi">{dur(trade.duration_s)}</Fact>
          <Fact label="Volume">{trade.volume}</Fact>
          <Fact label="Entry">{price(trade.open_price)}</Fact>
          <Fact label="Exit">{price(trade.close_price)}</Fact>
          <Fact label="SL awal">{trade.sl_initial === null ? unknown : price(trade.sl_initial)}</Fact>
          <Fact label="TP awal">{trade.tp_initial === null ? unknown : price(trade.tp_initial)}</Fact>
          <Fact label="Net">
            <span className={pnl > 0 ? "text-pos" : pnl < 0 ? "text-neg" : ""}>
              {money(trade.net_profit, header.currency, { sign: true })}
            </span>
          </Fact>
          <Fact label="R-multiple">{trade.r_multiple === null ? na : rmult(trade.r_multiple)}</Fact>
          <Fact label="MAE (R)">{trade.mae_r === null ? na : rmult(trade.mae_r)}</Fact>
          <Fact label="MFE (R)">{trade.mfe_r === null ? na : rmult(trade.mfe_r)}</Fact>
        </div>

        <div className="glass p-4">
          <h2 className="text-[13px] font-semibold uppercase tracking-wider text-muted mb-2">Chart</h2>
          {chartable ? (
            <>
              <TradePngPanel settings={png.settings} onChange={png.update} />
              {chartFailed ? (
                <p className="text-[12px] text-muted">
                  Chart belum tersedia — jalankan <code>uv run journal candles</code> lalu{" "}
                  <button className="text-cyan hover:underline" onClick={() => setChartFailed(false)}>coba lagi</button>.
                </p>
              ) : (
                <img className="w-full rounded" src={`/trades/${trade.position_id}/chart.png?v=${png.version}`}
                  alt={`chart trade ${trade.position_id}`} onError={() => setChartFailed(true)} />
              )}
              <a className="inline-block mt-2 text-[12px] text-cyan hover:underline"
                 href={`/trades/${trade.position_id}/view${window.location.search}`}>Lihat di chart interaktif →</a>
            </>
          ) : (
            <p className="text-[12px] text-muted">Hanya trade closed yang bisa di-chart.</p>
          )}
        </div>
      </div>

      <div className="grid md:grid-cols-2 gap-4 mt-4">
        <AnnotationForm positionId={trade.position_id} annotation={annotation} onSaved={reload} />
        <TagEditor positionId={trade.position_id} tags={tags} onChanged={reload} />
      </div>

      <p className="mt-4 text-[12px]"><a className="text-cyan hover:underline" href="/trades">← kembali ke daftar</a></p>
    </div>
  );
}
