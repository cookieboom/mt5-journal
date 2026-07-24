import { useEffect } from "react";
import { useParams, useNavigate, Link } from "react-router-dom";
import { useApi } from "../lib/api";
import { WeeklyData } from "../lib/types";
import { money, pct } from "../lib/format";

const wk = (y: number, w: number) => `${y}-W${String(w).padStart(2, "0")}`;

export default function Weekly() {
  const { week } = useParams();
  const navigate = useNavigate();
  const { data, error, loading } = useApi<WeeklyData>(week ? `/api/weekly/${week}` : "/api/weekly");

  // No week in the URL → redirect to the server-resolved latest week's dated URL.
  useEffect(() => {
    if (!week && data) navigate(`/weekly/${wk(data.result.iso_year, data.result.iso_week)}`, { replace: true });
  }, [week, data, navigate]);

  if (loading) return <div className="text-muted p-6">Memuat…</div>;
  if (error) return <div className="glass p-6 text-neg">Gagal memuat: {error}</div>;
  if (!data) return null;
  const { result: r, weeks } = data;
  const ccy = r.currency;
  const tone = r.net_total > 0 ? "text-pos" : r.net_total < 0 ? "text-neg" : "";

  return (
    <div>
      <h1 className="text-[18px] font-bold tracking-tight mb-1">Weekly · {wk(r.iso_year, r.iso_week)}</h1>
      <div className="text-[12px] text-muted mb-4">Mon–Sun UTC · trade diatribusikan ke minggu saat ditutup (realized).</div>

      {weeks.length > 0 && (
        <div className="flex flex-wrap gap-1.5 mb-4">
          {weeks.map(([y, w]) => {
            const active = y === r.iso_year && w === r.iso_week;
            return (
              <Link key={wk(y, w)} to={`/weekly/${wk(y, w)}`}
                className={"px-2.5 py-1 rounded-full text-[11px] ring-1 num " +
                  (active ? "bg-violet/20 ring-violet/45 text-ink" : "bg-white/5 ring-panel-border text-muted")}>
                {wk(y, w)}
              </Link>
            );
          })}
        </div>
      )}

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-4">
        <div className="glass p-4">
          <div className="text-[10px] uppercase tracking-wider text-muted">Realized net</div>
          <div className={"text-[18px] font-bold num mt-1 " + tone}>{money(r.net_total, ccy, { sign: true })}</div>
          <div className="text-[11px] text-muted mt-0.5">selalu ditampilkan (bukan gated)</div>
        </div>
        <div className="glass p-4">
          <div className="text-[10px] uppercase tracking-wider text-muted">Closed</div>
          <div className="text-[18px] font-bold num mt-1">{r.n_closed}</div>
        </div>
        <div className="glass p-4">
          <div className="text-[10px] uppercase tracking-wider text-muted">W / L / BE</div>
          <div className="text-[18px] font-bold num mt-1">
            <span className="text-pos">{r.n_wins}</span>/<span className="text-neg">{r.n_losses}</span>/{r.n_breakeven}
          </div>
        </div>
        <div className="glass p-4">
          <div className="text-[10px] uppercase tracking-wider text-muted">Win rate</div>
          <div className="text-[18px] font-bold num mt-1">{pct(r.win_rate)}</div>
          <div className="text-[11px] text-muted mt-0.5">n={r.n_closed}{r.n_closed < 20 ? ", perlu ≥20" : ""}</div>
        </div>
      </div>

      <div className="glass p-4 mb-4">
        <h2 className="text-[13px] font-semibold uppercase tracking-wider text-muted mb-2">Money (§9-gated di level minggu)</h2>
        <div className="grid grid-cols-2 gap-x-6 text-[13px]">
          <div className="flex justify-between py-1.5 border-b border-white/5"><span className="text-muted">Avg win</span><span className="num">{money(r.avg_win, ccy)}</span></div>
          <div className="flex justify-between py-1.5 border-b border-white/5"><span className="text-muted">Avg loss</span><span className="num">{money(r.avg_loss, ccy, { sign: true })}</span></div>
          <div className="flex justify-between py-1.5 border-b border-white/5"><span className="text-muted">Profit factor</span><span className="num">{r.profit_factor === null ? "n/a" : r.profit_factor.toFixed(2)}</span></div>
          <div className="flex justify-between py-1.5 border-b border-white/5"><span className="text-muted">Expectancy</span><span className="num">{money(r.expectancy, ccy, { sign: true })}</span></div>
        </div>
        <p className="text-[11px] text-muted mt-2">Satu minggu jarang mencapai n≥20, jadi rate/rata-rata umumnya "n/a" — itu jujur, bukan bug.</p>
      </div>

      <div className="glass p-4">
        <h2 className="text-[13px] font-semibold uppercase tracking-wider text-muted mb-3">
          Notes ({r.notes.length} trade dengan anotasi / tag manual)
        </h2>
        {r.notes.length === 0 ? (
          <div className="text-muted text-sm py-4">Belum ada trade beranotasi minggu ini.</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full border-collapse text-[12px]">
              <thead>
                <tr className="text-muted text-left">
                  {["Trade", "Net", "Setup", "Conf", "Emosi", "Plan", "Catatan", "Tags"].map((h, i) => (
                    <th key={i} className="pb-2 font-semibold uppercase text-[9.5px] tracking-wider">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {r.notes.map((n) => (
                  <tr key={n.position_id} className="border-t border-white/5">
                    <td className="py-2">
                      <Link className="text-cyan hover:underline" to={`/trades/${n.position_id}`}>{n.symbol_base} #{n.position_id}</Link>
                    </td>
                    <td className={"py-2 num " + (n.net_profit > 0 ? "text-pos" : n.net_profit < 0 ? "text-neg" : "")}>
                      {money(n.net_profit, ccy, { sign: true })}
                    </td>
                    <td className="py-2">{n.setup ?? "—"}</td>
                    <td className="py-2 num">{n.confidence ?? "—"}</td>
                    <td className="py-2">{n.emotion ?? "—"}</td>
                    <td className="py-2">{n.followed_plan === 1 ? "ya" : n.followed_plan === 0 ? "tidak" : "—"}</td>
                    <td className="py-2">{n.notes ?? "—"}</td>
                    <td className="py-2">
                      <span className="flex flex-wrap gap-1">
                        {n.tags.map((t) => <span key={t} className="px-1.5 py-0.5 rounded text-[10px] bg-white/6 text-muted">{t}</span>)}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
