import { useState } from "react";
import { Link } from "react-router-dom";
import { useApi } from "../lib/api";
import { TradesData } from "../lib/types";
import { money, rmult, wib, dur } from "../lib/format";
import TradeSparkbar from "../components/TradeSparkbar";

const STATUSES = ["closed", "open", "partially_open"];
const SOURCES: [string, string][] = [["ea", "EA"], ["disc", "Discretionary"]];

type Filters = { symbol: string; status: string; source: string };

function filterParams(f: Filters): URLSearchParams {
  const p = new URLSearchParams();
  if (f.symbol) p.set("symbol", f.symbol);
  if (f.status) p.set("status", f.status);
  if (f.source) p.set("source", f.source);
  return p;
}

function qs(f: Filters): string {
  const s = filterParams(f).toString();
  return s ? `/api/trades?${s}` : "/api/trades";
}

// Same filters as the /api/trades list query, so the row link carries the
// exact query-string TradeView will re-fetch for its neighbor list.
function linkQuery(f: Filters): string {
  const s = filterParams(f).toString();
  return s ? `?${s}` : "";
}

export default function Trades() {
  const [f, setF] = useState({ symbol: "", status: "", source: "" });
  const { data, error, loading } = useApi<TradesData>(qs(f));
  if (loading) return <div className="text-muted p-6">Memuat…</div>;
  if (error) return <div className="glass p-6 text-neg">Gagal memuat: {error}</div>;
  if (!data) return null;
  const { header, trades, tags, symbols, max_abs_net } = data;
  const linkQ = linkQuery(f);

  const chip = (active: boolean) =>
    "px-2.5 py-1 rounded-full text-meta ring-1 " +
    (active ? "bg-violet/20 ring-violet/45 text-ink" : "bg-white/5 ring-panel-border text-muted");

  return (
    <div>
      <h1 className="text-headline font-bold mb-1">
        Trades <span className="text-muted num text-title">({trades.length})</span>
      </h1>
      <div className="text-body text-muted mb-4">
        Net dalam {header.currency} (US cents) — bar hanya penanda arah. Waktu WIB (UTC+7).
      </div>

      <div className="flex flex-col gap-2 mb-4">
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="text-label uppercase text-muted mr-1">Symbol</span>
          <button className={chip(!f.symbol)} onClick={() => setF({ ...f, symbol: "" })}>semua</button>
          {symbols.map((s) => (
            <button key={s} className={chip(f.symbol === s)} onClick={() => setF({ ...f, symbol: s })}>{s}</button>
          ))}
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="text-label uppercase text-muted mr-1">Status</span>
          <button className={chip(!f.status)} onClick={() => setF({ ...f, status: "" })}>semua</button>
          {STATUSES.map((s) => (
            <button key={s} className={chip(f.status === s)} onClick={() => setF({ ...f, status: s })}>{s}</button>
          ))}
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="text-label uppercase text-muted mr-1">Source</span>
          <button className={chip(!f.source)} onClick={() => setF({ ...f, source: "" })}>semua</button>
          {SOURCES.map(([v, label]) => (
            <button key={v} className={chip(f.source === v)} onClick={() => setF({ ...f, source: v })}>{label}</button>
          ))}
        </div>
      </div>

      <div className="glass p-4 overflow-x-auto">
        {trades.length === 0 ? (
          <div className="text-muted text-body py-6">Tidak ada trade untuk filter ini.</div>
        ) : (
          <table className="w-full border-collapse text-body">
            <thead>
              <tr className="text-muted text-left">
                {["Dibuka", "Symbol", "Arah", "Status", "Src", "Durasi", "Net", "", "R", "Tags"].map((h, i) => (
                  <th key={i} className="pb-2 font-semibold uppercase text-label whitespace-nowrap">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {trades.map((t) => {
                const pnl = t.net_profit ?? 0;
                return (
                  <tr key={t.position_id} className="border-t border-white/5">
                    <td className="py-2 num whitespace-nowrap">
                      <Link className="text-cyan hover:underline" to={`/trades/${t.position_id}${linkQ}`}>
                        {wib(t.open_time_msc, header.offset_s)}
                      </Link>
                    </td>
                    <td className="py-2">{t.symbol_base}</td>
                    <td className="py-2 uppercase">{t.direction}</td>
                    <td className="py-2">{t.status}</td>
                    <td className="py-2">{t.magic ? "EA" : "Disc"}</td>
                    <td className="py-2 num whitespace-nowrap">{dur(t.duration_s)}</td>
                    <td className={"py-2 num whitespace-nowrap " + (pnl > 0 ? "text-pos" : pnl < 0 ? "text-neg" : "")}>
                      {money(t.net_profit, header.currency, { sign: true })}
                    </td>
                    <td className="py-2"><TradeSparkbar net={t.net_profit} maxAbsNet={max_abs_net} /></td>
                    <td className="py-2 num">{t.r_multiple === null ? <span className="text-muted">n/a</span> : rmult(t.r_multiple)}</td>
                    <td className="py-2">
                      <span className="flex flex-wrap gap-1">
                        {(tags[String(t.position_id)] ?? []).map(([tag, source]) => (
                          <span key={tag} className={"px-1.5 py-0.5 rounded text-label " +
                            (source === "manual" ? "bg-violet/15 text-violet" : "bg-white/6 text-muted")}>{tag}</span>
                        ))}
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
