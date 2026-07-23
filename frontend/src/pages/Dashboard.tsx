import { useApi } from "../lib/api";
import { DashboardData } from "../lib/types";
import { money, pct, rmult } from "../lib/format";
import KpiCard from "../components/KpiCard";
import EquityChart from "../components/EquityChart";
import SymbolBars from "../components/SymbolBars";
import RecentTrades from "../components/RecentTrades";

export default function Dashboard() {
  const { data, error, loading } = useApi<DashboardData>("/api/dashboard", 5000);

  if (loading) return <div className="text-muted p-6">Memuat…</div>;
  if (error) return <div className="glass p-6 text-neg">Gagal memuat: {error}</div>;
  if (!data) return null;

  const { header, report, live, equity } = data;
  const ccy = header.currency;
  const floatTone = live.total_floating >= 0 ? "pos" : "neg";

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <div>
          <h1 className="text-[18px] font-bold tracking-tight">Dashboard</h1>
          <div className="text-[12px] text-muted mt-0.5">{report.n_closed} trade tertutup</div>
        </div>
        <div className="text-[11px] text-cyan flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-cyan/10 ring-1 ring-cyan/25">
          <span className="w-1.5 h-1.5 rounded-full bg-cyan shadow-[0_0_8px_#22d3ee]" />
          {live.empty ? "live idle" : live.stale ? "stale" : `live · ${live.age_s}s`}
        </div>
      </div>

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-4">
        <KpiCard label="Net R" value={rmult(equity.r_last)} sub={`n=${equity.n_with_r}`}
                 tone={(equity.r_last ?? 0) >= 0 ? "pos" : "neg"} />
        <KpiCard label="Win rate" value={pct(report.win_rate)}
                 sub={`${report.n_wins}W · ${report.n_losses}L · ${report.n_breakeven}BE`} />
        <KpiCard label="Expectancy" value={money(report.expectancy, ccy)} sub="per trade" />
        <KpiCard label="Floating P&L" value={money(live.total_floating, ccy, { sign: true })}
                 sub={`${live.count} posisi open`} tone={floatTone} />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-[1.55fr_1fr] gap-3.5 mb-3.5">
        <div className="glass p-4">
          <h2 className="text-[13px] font-semibold">Kurva R kumulatif</h2>
          <div className="text-[11px] text-muted mb-3">pertumbuhan R dari waktu ke waktu</div>
          <EquityChart svg={equity.r_svg} label="R" />
        </div>
        <div className="glass p-4">
          <h2 className="text-[13px] font-semibold">Per simbol</h2>
          <div className="text-[11px] text-muted mb-3">rata-rata R · win rate</div>
          <SymbolBars report={report} />
        </div>
      </div>

      <div className="glass p-4">
        <h2 className="text-[13px] font-semibold mb-3">Trade terakhir</h2>
        <RecentTrades equity={equity} currency={ccy} offsetS={header.offset_s} />
      </div>
    </div>
  );
}
