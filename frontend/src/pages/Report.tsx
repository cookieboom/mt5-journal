import { useApi } from "../lib/api";
import { ReportData, Bucket } from "../lib/types";
import { money, pct, isGated, gatedR } from "../lib/format";
import RHistogram from "../components/RHistogram";
import MaeMfeScatter from "../components/MaeMfeScatter";
import CalendarHeatmap from "../components/CalendarHeatmap";

function BucketTable({ title, rows, ccy }: { title: string; rows: Bucket[]; ccy: string }) {
  return (
    <div className="glass p-4">
      <h2 className="text-[13px] font-semibold uppercase tracking-wider text-muted mb-3">{title}</h2>
      <div className="overflow-x-auto">
        <table className="w-full border-collapse text-[12px]">
          <thead>
            <tr className="text-muted text-left">
              {["", "n", "Win", "Expectancy", "Avg R"].map((h, i) => (
                <th key={i} className="pb-2 font-semibold uppercase text-[9.5px] tracking-wider">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((b) => {
              const rowGated = isGated(b.n, b.expectancy);
              return (
                <tr key={b.label} className={"border-t border-white/5 " + (rowGated ? "text-muted/60" : "")}>
                  <td className="py-2">{b.label}</td>
                  <td className="py-2 num">{b.n}</td>
                  <td className="py-2 num">{pct(b.win_rate)}</td>
                  <td className="py-2 num">{money(b.expectancy, ccy, { sign: true })}</td>
                  <td className="py-2 num">{gatedR(b.n_with_r, b.avg_r)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export default function Report() {
  const { data, error, loading } = useApi<ReportData>("/api/report");
  if (loading) return <div className="text-muted p-6">Memuat…</div>;
  if (error) return <div className="glass p-6 text-neg">Gagal memuat: {error}</div>;
  if (!data) return null;
  const { header, report: r, series } = data;
  const ccy = r.currency;

  const Kv = ({ label, children }: { label: string; children: React.ReactNode }) => (
    <div className="flex justify-between gap-4 py-1.5 border-b border-white/5 text-[13px]">
      <span className="text-muted">{label}</span><span className="num text-right">{children}</span>
    </div>
  );

  return (
    <div>
      <h1 className="text-[18px] font-bold tracking-tight mb-1">Report</h1>
      <div className="text-[12px] text-muted mb-4">
        Coverage penuh untuk money (n={r.n_closed} closed). Net dalam {ccy} (US cents).
        Baris grey = bucket di bawah n≥20 (docs §9): count &amp; net tetap tampil, rate/rata-rata ditahan.
      </div>

      <div className="grid md:grid-cols-2 gap-4 mb-4">
        <div className="glass p-4">
          <h2 className="text-[13px] font-semibold uppercase tracking-wider text-muted mb-2">
            Money (coverage penuh, n={r.n_closed})
          </h2>
          <Kv label="Win rate">{pct(r.win_rate)}</Kv>
          <Kv label="Profit factor">{r.profit_factor === null ? "n/a" : r.profit_factor.toFixed(2)}</Kv>
          <Kv label="Expectancy">{money(r.expectancy, ccy, { sign: true })}</Kv>
          <Kv label="Avg win"><span className="text-pos">{money(r.avg_win, ccy)}</span></Kv>
          <Kv label="Avg loss"><span className="text-neg">{money(r.avg_loss, ccy, { sign: true })}</span></Kv>
          <Kv label="W / L / BE">{r.n_wins} / {r.n_losses} / {r.n_breakeven}</Kv>
        </div>
        <div className="glass p-4">
          <h2 className="text-[13px] font-semibold uppercase tracking-wider text-muted mb-2">
            MAE / MFE (§9: perlu n≥20, butuh candle + SL)
          </h2>
          <Kv label="Candle coverage">{r.n_with_mae} / {r.n_closed} closed</Kv>
          <Kv label="Avg MAE (R)">{gatedR(r.n_with_mae_r, r.avg_mae_r)}</Kv>
          <Kv label="Avg MFE (R)">{gatedR(r.n_with_mfe_r, r.avg_mfe_r)}</Kv>
          <Kv label="Avg R (akun)">{gatedR(r.n_with_r, r.avg_r)}</Kv>
        </div>
      </div>

      <div className="grid md:grid-cols-3 gap-4">
        <BucketTable title="Per session (UTC)" rows={r.by_session} ccy={ccy} />
        <BucketTable title="Per source (EA = magic≠0)" rows={r.by_source} ccy={ccy} />
        <BucketTable title="Per symbol" rows={r.by_symbol} ccy={ccy} />
      </div>

      <div className="grid lg:grid-cols-2 gap-4 mt-4">
        <RHistogram series={series} />
        <MaeMfeScatter series={series} />
      </div>
      <div className="mt-4">
        <CalendarHeatmap series={series} currency={r.currency} offsetS={header.offset_s} />
      </div>
    </div>
  );
}
