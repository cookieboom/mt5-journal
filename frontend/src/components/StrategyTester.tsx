import { useEffect, useMemo, useState } from "react";
import {
  ageLabel, backtestApi, fmtR, headline,
  type BacktestResult, type BacktestSettings, type Bucket, type ContextCell, type ContextQuery,
  type ContextResult, type ContextSourceKey, type Period, type PeriodKind, type Segment, type TesterTrade,
} from "../lib/backtest";
import { wib } from "../lib/format";
import { palette, white } from "../lib/theme";

// Strategy Tester strip under the chart (spec 2026-10-07-indicators-strategy-
// tester §1.8). Collapsed: one line — the OOS headline with n and age, which is
// what rule 9 makes non-optional beside any live signal marker. Expanded: four
// tabs. Out-of-sample leads everywhere; in-sample is context, not the score.
// No order button, ever. "Trade saya" (spec 2026-10-08-indicators-trade-
// context) describes MY past trades against the script — no verdict (rule 9).

type Tab = "summary" | "trades" | "breakdown" | "context" | "settings";
const TABS: [Tab, string][] = [
  ["summary", "Ringkasan"], ["trades", "Daftar trade"], ["breakdown", "Breakdown"],
  ["context", "Trade saya"], ["settings", "Pengaturan"],
];
const REASON: Record<TesterTrade["reason"], string> = {
  sl: "SL", tp: "TP", exit: "exit()", opposite: "sinyal lawan", max_hold: "max hold", open: "terbuka",
};
const DOW = ["Sen", "Sel", "Rab", "Kam", "Jum", "Sab", "Min"];

export interface TesterCandidate { id: string; name: string }

export default function StrategyTester({
  candidates, activeId, onActive, settings, onSettings, result, error, loading, armed, onRun,
  onTradeClick, onReplay, onPeriodReplay, replayError, context, nowMs,
}: {
  candidates: TesterCandidate[];
  activeId: string;
  onActive: (id: string) => void;
  settings: BacktestSettings;
  onSettings: (s: BacktestSettings) => void;
  result: BacktestResult | null;
  error: string | null;
  loading: boolean;
  armed: boolean;
  onRun: () => void;
  onTradeClick: (t: TesterTrade) => void;
  onReplay: (t: TesterTrade) => void;
  onPeriodReplay: (p: Period) => void;
  replayError: string | null;        // the last Replay jump the server refused
  context: ContextQuery | null;      // the active script/symbol/TF, for "Trade saya"
  nowMs: number;
}) {
  const [open, setOpen] = useState(false);
  const [tab, setTab] = useState<Tab>("summary");
  // Kept here, above the tab switch, so leaving "Trade saya" and coming back
  // neither loses the result nor re-runs the server job.
  const [ctxDraft, setCtxDraft] = useState("3");
  const [ctxWin, setCtxWin] = useState(3);
  const [ctxOut, setCtxOut] = useState<CtxOut | null>(null);

  return (
    <section aria-label="Strategy Tester" className="glass mt-2 text-body">
      <div className="flex items-center gap-3 px-3 min-h-[40px]">
        <span className="font-semibold shrink-0">Tester</span>
        {candidates.length > 1 ? (
          <select aria-label="Script tester" value={activeId} onChange={(e) => onActive(e.target.value)}
                  className="bg-transparent border border-panel-border rounded px-1 text-body">
            {candidates.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
        ) : <span className="text-muted truncate">{candidates[0]?.name}</span>}
        <span className="flex-1 truncate tabular-nums" aria-live="polite">
          {error ? <span className="text-neg">{error}</span>
            : result ? <span className={tone(result.segments.oos.avg_r)}>{headline(result, nowMs)}</span>
            : <span className="text-muted">{loading ? "menghitung…" : "Belum dijalankan"}</span>}
        </span>
        {!armed && <button onClick={onRun} className="text-cyan hover:text-ink shrink-0">Jalankan</button>}
        {loading && result && <span className="text-meta text-muted shrink-0">menghitung…</span>}
        {replayError && <span className="text-meta text-neg shrink-0">Replay gagal: {replayError}</span>}
        <button onClick={() => setOpen((x) => !x)} aria-expanded={open}
                aria-label={open ? "Tutup tester" : "Buka tester"}
                className="text-muted hover:text-ink shrink-0 min-w-[32px]">{open ? "▼" : "▲"}</button>
      </div>
      {open && (
        <div className="border-t border-panel-border h-[320px] flex flex-col">
          <div role="tablist" className="flex gap-4 px-3 pt-2 text-meta">
            {TABS.map(([k, label]) => (
              <button key={k} role="tab" aria-selected={tab === k} onClick={() => setTab(k)}
                      className={tab === k ? "text-ink font-semibold" : "text-muted hover:text-ink"}>{label}</button>
            ))}
          </div>
          <div className="flex-1 min-h-0 overflow-auto p-3">
            {tab === "settings" ? <Settings s={settings} onChange={onSettings} />
              : tab === "context" ? (
                <TradeContext q={context} r={result} nowMs={nowMs} draft={ctxDraft} onDraft={setCtxDraft}
                              win={ctxWin} onWin={setCtxWin} out={ctxOut} onOut={setCtxOut} />
              )
              : !result ? <div className="text-muted">Jalankan tester untuk melihat hasil.</div>
              : tab === "summary" ? <Summary r={result} nowMs={nowMs} />
              : tab === "trades" ? <Trades r={result} onClick={onTradeClick} onReplay={onReplay} />
              : <BreakdownTab r={result} onPeriodReplay={onPeriodReplay} />}
          </div>
        </div>
      )}
    </section>
  );
}

function tone(v: number | null): string {
  return v === null ? "text-muted" : v > 0 ? "text-pos" : v < 0 ? "text-neg" : "text-ink";
}
const pct = (v: number | null) => (v === null ? "—" : `${Math.round(v * 100)}%`);
const num = (v: number | null, d = 2) => (v === null ? "—" : v.toFixed(d));

function Summary({ r, nowMs }: { r: BacktestResult; nowMs: number }) {
  const cols: [keyof BacktestResult["segments"], string][] = [["oos", "OOS"], ["is", "IS"], ["all", "Semua"]];
  const rows: [string, (s: Segment) => string][] = [
    ["n", (s) => String(s.n)],
    ["Win rate", (s) => pct(s.win_rate)],
    ["Avg R", (s) => fmtR(s.avg_r)],
    ["Total R", (s) => fmtR(s.total_r)],
    ["Profit factor", (s) => num(s.profit_factor)],
    ["Max DD (R)", (s) => num(s.max_dd_r)],
    ["Streak menang / kalah", (s) => `${s.max_win_streak} / ${s.max_loss_streak}`],
  ];
  const c = r.counts;
  const warn = [
    c.rejected && `${c.rejected} sinyal ditolak (SL/TP salah sisi)`,
    c.open && `${c.open} masih terbuka (tidak dihitung)`,
    c.no_stop && `${c.no_stop} tanpa SL (tanpa R)`,
    c.spread_fallback && `${c.spread_fallback} pakai spread cadangan`,
    c.unknown_cost && `${c.unknown_cost} spread tak diketahui (tanpa R)`,
    r.pending && "warm-up belum lengkap — data sedang diambil",
  ].filter(Boolean);
  return (
    <div className="flex flex-col md:flex-row gap-4">
      <table className="tabular-nums">
        <thead>
          <tr className="text-label uppercase text-muted">
            <th className="text-left pr-4 font-normal" />
            {cols.map(([k, l]) => <th key={k} className={`text-right px-2 font-normal ${k === "oos" ? "text-ink" : ""}`}>{l}</th>)}
          </tr>
        </thead>
        <tbody>
          {rows.map(([label, f]) => (
            <tr key={label}>
              <td className="pr-4 text-muted">{label}</td>
              {cols.map(([k]) => (
                <td key={k} className={`text-right px-2 ${k === "oos" ? "font-semibold" : "text-muted"}`}>{f(r.segments[k])}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      <div className="flex-1 min-w-0 space-y-1">
        <Equity r={r} />
        <div className="text-meta text-muted">
          R bersih spread · dihitung {ageLabel(r.computed_ms, nowMs)} · garis putus = batas IS/OOS
        </div>
        {warn.length > 0 && <div className="text-meta text-warn">{warn.join(" · ")}</div>}
      </div>
    </div>
  );
}

function Equity({ r }: { r: BacktestResult }) {
  const W = 400, H = 120;
  const pts = r.equity;
  if (pts.length === 0) return <div className="text-muted">Belum ada trade tertutup.</div>;
  const t0 = Math.min(pts[0].t, r.split_msc), t1 = Math.max(pts[pts.length - 1].t, r.split_msc);
  const lo = Math.min(0, ...pts.map((p) => p.r)), hi = Math.max(0, ...pts.map((p) => p.r));
  const x = (t: number) => (t1 === t0 ? W : ((t - t0) / (t1 - t0)) * W);
  const y = (v: number) => (hi === lo ? H / 2 : H - ((v - lo) / (hi - lo)) * H);
  const line = [`0,${y(0)}`, ...pts.map((p) => `${x(p.t)},${y(p.r)}`)].join(" ");
  return (
    <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className="w-full h-[120px]"
         role="img" aria-label="Kurva ekuitas dalam R">
      <line x1="0" x2={W} y1={y(0)} y2={y(0)} stroke={white(0.18)} strokeDasharray="2 3" />
      <line x1={x(r.split_msc)} x2={x(r.split_msc)} y1="0" y2={H} stroke={palette.muted} strokeDasharray="4 4" />
      <polyline points={line} fill="none" stroke={palette.cyan} strokeWidth="2" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

function Trades({ r, onClick, onReplay }: {
  r: BacktestResult; onClick: (t: TesterTrade) => void; onReplay: (t: TesterTrade) => void;
}) {
  const [byR, setByR] = useState<null | "asc" | "desc">(null);
  const list = useMemo(() => {
    const l = [...r.trades].reverse();          // newest first
    if (byR) l.sort((a, b) => ((a.r ?? -Infinity) - (b.r ?? -Infinity)) * (byR === "asc" ? 1 : -1));
    return l;
  }, [r.trades, byR]);
  if (list.length === 0) return <div className="text-muted">Tidak ada sinyal di rentang ini.</div>;
  return (
    <table className="w-full tabular-nums">
      <thead>
        <tr className="text-label uppercase text-muted text-left">
          <th className="font-normal pr-2">Sisi</th>
          <th className="font-normal pr-2">Masuk</th>
          <th className="font-normal pr-2">Keluar</th>
          <th className="font-normal pr-2">Alasan</th>
          <th className="font-normal pr-2 text-right">
            <button onClick={() => setByR(byR === "desc" ? "asc" : "desc")} aria-label="Urutkan menurut R"
                    className="uppercase hover:text-ink">R {byR === "desc" ? "↓" : byR === "asc" ? "↑" : ""}</button>
          </th>
          <th className="font-normal pr-2 text-right">MAE / MFE</th>
          <th />
        </tr>
      </thead>
      <tbody>
        {list.map((t) => (
          <tr key={t.id} onClick={() => onClick(t)} className="cursor-pointer hover:bg-panel">
            <td className={`pr-2 ${t.side === "long" ? "text-pos" : "text-neg"}`}>{t.side === "long" ? "Long" : "Short"}</td>
            <td className="pr-2">{wib(t.entry_msc)}</td>
            <td className="pr-2">{t.exit_msc === null ? "—" : wib(t.exit_msc)}</td>
            <td className="pr-2 text-muted">{REASON[t.reason]}</td>
            <td className={`pr-2 text-right ${tone(t.r)}`}>{fmtR(t.r)}</td>
            <td className="pr-2 text-right text-muted">{num(t.mae_r)} / {num(t.mfe_r)}</td>
            <td className="text-right">
              <button onClick={(e) => { e.stopPropagation(); onReplay(t); }} className="text-meta text-cyan hover:text-ink">
                Replay
              </button>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function BreakdownTab({ r, onPeriodReplay }: { r: BacktestResult; onPeriodReplay: (p: Period) => void }) {
  const [seg, setSeg] = useState<"oos" | "all">("oos");
  const b = r.breakdown[seg];
  const groups: [string, Bucket[], (k: Bucket["key"]) => string][] = [
    ["Jam (WIB)", b.hour, (k) => `${String((Number(k) + 7) % 24).padStart(2, "0")}:00`],
    ["Sesi", b.session, (k) => String(k)],
    ["Hari (UTC)", b.dow, (k) => DOW[Number(k)] ?? String(k)],
  ];
  return (
    <div className="space-y-2">
      <div className="flex gap-3 text-meta">
        {(["oos", "all"] as const).map((k) => (
          <button key={k} aria-pressed={seg === k} onClick={() => setSeg(k)}
                  className={seg === k ? "text-ink font-semibold" : "text-muted hover:text-ink"}>
            {k === "oos" ? "OOS" : "Semua"}
          </button>
        ))}
      </div>
      <div className="flex flex-wrap gap-6">
        {groups.map(([title, rows, label]) => (
          <table key={title} className="tabular-nums">
            <thead>
              <tr className="text-label uppercase text-muted">
                <th className="text-left font-normal pr-3">{title}</th>
                <th className="text-right font-normal px-2">n</th>
                <th className="text-right font-normal px-2">Win</th>
                <th className="text-right font-normal px-2">Avg R</th>
                <th className="text-right font-normal px-2">Total R</th>
              </tr>
            </thead>
            <tbody>
              {(title.startsWith("Jam") ? [...rows].sort((a, c) => (Number(a.key) + 7) % 24 - (Number(c.key) + 7) % 24) : rows)
                .map((x) => (
                  <tr key={String(x.key)}>
                    <td className="pr-3">{label(x.key)}</td>
                    <td className="text-right px-2 text-muted">{x.n}</td>
                    <td className="text-right px-2">{pct(x.win_rate)}</td>
                    <td className={`text-right px-2 ${tone(x.avg_r)}`}>{fmtR(x.avg_r)}</td>
                    <td className="text-right px-2">{fmtR(x.total_r)}</td>
                  </tr>
                ))}
            </tbody>
          </table>
        ))}
      </div>
      {r.breakdown.all.periods && <Periods ps={r.breakdown.all.periods} onReplay={onPeriodReplay} />}
    </div>
  );
}

const SEGMENT: Record<Period["segment"], string> = { is: "IS", oos: "OOS", mixed: "IS+OOS" };
const KINDS: [PeriodKind, string][] = [["day", "Hari"], ["week", "Minggu"], ["session", "Sesi"]];

/** Replay jump per period: days / weeks / session instances ranked by total R
 *  (all trades; each row says whether it lies in-sample or out-of-sample). */
function Periods({ ps, onReplay }: { ps: Record<PeriodKind, Period[]>; onReplay: (p: Period) => void }) {
  const [kind, setKind] = useState<PeriodKind>("day");
  const [worst, setWorst] = useState(false);
  const list = useMemo(
    () => [...ps[kind]].sort((a, b) => (a.total_r - b.total_r) * (worst ? 1 : -1)),
    [ps, kind, worst],
  );
  return (
    <div className="space-y-1 pt-2">
      <div className="flex gap-3 text-meta items-center">
        <span className="text-label uppercase text-muted">Periode</span>
        {KINDS.map(([k, l]) => (
          <button key={k} aria-pressed={kind === k} onClick={() => setKind(k)}
                  className={kind === k ? "text-ink font-semibold" : "text-muted hover:text-ink"}>{l}</button>
        ))}
      </div>
      {list.length === 0 ? <div className="text-muted">Belum ada trade tertutup.</div> : (
        <table className="tabular-nums">
          <thead>
            <tr className="text-label uppercase text-muted">
              <th className="text-left font-normal pr-3">Mulai</th>
              <th className="text-right font-normal px-2">n</th>
              <th className="text-right font-normal px-2">Win</th>
              <th className="text-right font-normal px-2">
                <button onClick={() => setWorst((x) => !x)} aria-label="Urutkan periode"
                        className="uppercase hover:text-ink">Total R {worst ? "↑" : "↓"}</button>
              </th>
              <th className="text-left font-normal px-2" />
              <th />
            </tr>
          </thead>
          <tbody>
            {list.map((p) => (
              <tr key={p.key} data-testid="period-row" data-key={p.key}>
                <td className="pr-3">{wib(p.key)}</td>
                <td className="text-right px-2 text-muted">{p.n}</td>
                <td className="text-right px-2">{pct(p.win_rate)}</td>
                <td className={`text-right px-2 ${tone(p.total_r)}`}>{fmtR(p.total_r)}</td>
                <td data-testid="period-segment"
                    className={`px-2 text-meta ${p.segment === "is" ? "text-muted" : "text-ink"}`}>
                  {SEGMENT[p.segment]}</td>
                <td className="text-right">
                  <button onClick={() => onReplay(p)} className="text-meta text-cyan hover:text-ink">Replay</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

const SOURCES: [ContextSourceKey, string][] = [
  ["real", "Real"], ["replay", "Replay · simulasi, tanpa gating"], ["paper", "Paper · simulasi, tanpa gating"],
];

interface CtxOut { key: string; res?: ContextResult; err?: string }
const WIN_DEBOUNCE_MS = 400;
const PENDING_RETRY_MS = 3000;

/** My closed trades, split by whether a same-side signal fired in the last N
 *  closed bars before them. Fetched when the tab opens (it mounts only then),
 *  and only beside a tester result — signal() output never shows without its
 *  OOS expectancy, n and age (rule 9). */
function TradeContext({ q, r, nowMs, draft, onDraft, win, onWin, out, onOut }: {
  q: ContextQuery | null; r: BacktestResult | null; nowMs: number;
  draft: string; onDraft: (d: string) => void;
  win: number; onWin: (w: number) => void; out: CtxOut | null; onOut: (o: CtxOut) => void;
}) {
  // The draft lives in the parent, so a value typed just before leaving the
  // tab is applied when it comes back instead of being lost with the timer.
  useEffect(() => {
    const v = Math.round(Number(draft));
    if (!(v >= 1 && v <= 50) || v === win) return;
    const t = setTimeout(() => onWin(v), WIN_DEBOUNCE_MS);
    return () => clearTimeout(t);
  }, [draft, win, onWin]);

  // Keyed by the tester result too: the context always pairs with the headline
  // beside it, and a new run (new closed bar, new inputs) refreshes it. Only a
  // clean, settled result is kept: an error retries on the next open, a pending
  // (warm-up still queued) one retries on a timer.
  const [retry, setRetry] = useState(0);
  const key = JSON.stringify([q, win, r?.computed_ms ?? null]);
  const kept = out?.key === key && !out.err && !out.res?.pending;
  const ready = q !== null && r !== null;
  useEffect(() => {
    if (!ready || kept) return;
    let live = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    void backtestApi.context(q!, win).then((x) => {
      if (!live) return;
      onOut(x.ok && x.data ? { key, res: x.data } : { key, err: x.error ?? "gagal menghitung" });
      if (x.ok && x.data?.pending) timer = setTimeout(() => setRetry((n) => n + 1), PENDING_RETRY_MS);
    });
    return () => { live = false; if (timer) clearTimeout(timer); };
  }, [key, ready, retry]);   // key covers q, win and the result; `kept` only short-circuits

  if (!q) return <div className="text-muted">Pilih script dengan signal().</div>;
  if (!r) {
    return <div className="text-muted">Jalankan tester dulu — hasil ini selalu tampil di samping headline OOS-nya.</div>;
  }
  const cur = out?.key === key ? out : null;
  const res = cur?.res;
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-4 text-meta">
        <label className="flex items-center gap-2">Jendela (bar)
          <input type="number" min={1} max={50} value={draft}
                 className="bg-transparent border border-panel-border rounded px-1 w-14 text-body tabular-nums"
                 onChange={(e) => onDraft(e.target.value)} />
        </label>
        <span data-testid="ctx-headline" className="tabular-nums text-muted">Tester: {headline(r, nowMs)}</span>
      </div>
      <div className="text-meta text-muted">
        "Ya" = sinyal searah menyala dalam {win} bar tertutup sebelum trade (real: waktu fill, bukan
        waktu keputusan). Sel abu = n &lt; 20, angkanya disembunyikan.
      </div>
      {cur?.err ? <div className="text-neg">{cur.err}</div>
        : !res ? <div className="text-muted">menghitung…</div>
        : (
          <div className="flex flex-wrap gap-6">
            {SOURCES.map(([k, title]) => {
              const s = res.sources[k];
              return (
                <div key={k} className="space-y-1">
                  <div className="text-label uppercase text-muted">{title}</div>
                  <table className="tabular-nums">
                    <thead>
                      <tr className="text-label uppercase text-muted">
                        <th className="text-left font-normal pr-3">Sinyal searah</th>
                        <th className="text-right font-normal px-2">n</th>
                        <th className="text-right font-normal px-2">Win</th>
                        <th className="text-right font-normal px-2">n R</th>
                        <th className="text-right font-normal px-2">Avg R</th>
                        <th className="text-right font-normal px-2">Total R</th>
                      </tr>
                    </thead>
                    <tbody>
                      <CtxRow label="ya" c={s.true} />
                      <CtxRow label="tidak" c={s.false} />
                    </tbody>
                  </table>
                  {s.n_unknown > 0 && <div className="text-meta text-muted">tidak diketahui: {s.n_unknown}</div>}
                </div>
              );
            })}
          </div>
        )}
      {res?.clipped_from_msc != null && (
        <div className="text-meta text-warn">
          Rentang terlalu panjang: trade sebelum {wib(res.clipped_from_msc)} tidak dievaluasi (masuk "tidak diketahui").
        </div>
      )}
      {res?.pending && <div className="text-meta text-warn">warm-up belum lengkap — data sedang diambil</div>}
    </div>
  );
}

function CtxRow({ label, c }: { label: string; c: ContextCell }) {
  // A null under the gate is "too few", not "unknown": say which n held it back.
  const gate = (v: string, isNull: boolean, n: number, what: string) =>
    isNull && c.gated && n > 0 && n < 20
      ? <span className="text-muted opacity-60" title={`${what} ${n} < 20`}>—</span> : v;
  return (
    <tr>
      <td className="pr-3">{label}</td>
      <td className="text-right px-2 text-muted">{c.n}</td>
      <td className="text-right px-2">{gate(pct(c.win_rate), c.win_rate === null, c.n, "n")}</td>
      <td className="text-right px-2 text-muted">{c.n_r}</td>
      <td className={`text-right px-2 ${tone(c.avg_r)}`}>{gate(fmtR(c.avg_r), c.avg_r === null, c.n_r, "n R")}</td>
      <td className={`text-right px-2 ${tone(c.total_r)}`}>{gate(fmtR(c.total_r), c.total_r === null, c.n_r, "n R")}</td>
    </tr>
  );
}

function Settings({ s, onChange }: { s: BacktestSettings; onChange: (s: BacktestSettings) => void }) {
  const set = (p: Partial<BacktestSettings>) => onChange({ ...s, ...p });
  const numIn = (v: string): number | null => (v.trim() === "" || !Number.isFinite(Number(v)) ? null : Number(v));
  const field = "bg-transparent border border-panel-border rounded px-1 w-20 text-body tabular-nums";
  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-2 max-w-[640px]">
      <label className="flex items-center justify-between gap-2">Rentang (hari)
        <input type="number" min={1} className={field} value={s.rangeDays}
               onChange={(e) => set({ rangeDays: numIn(e.target.value) ?? s.rangeDays })} /></label>
      <label className="flex items-center justify-between gap-2">In-sample (%)
        <input type="number" min={5} max={95} className={field} value={Math.round(s.split * 100)}
               onChange={(e) => { const v = numIn(e.target.value); if (v !== null && v >= 5 && v <= 95) set({ split: v / 100 }); }} /></label>
      <label className="flex items-center justify-between gap-2">SL bawaan
        <select className={field} value={s.sl} onChange={(e) => set({ sl: e.target.value as BacktestSettings["sl"] })}>
          <option value="atr">ATR × k</option><option value="points">poin</option><option value="none">tanpa</option>
        </select></label>
      <label className="flex items-center justify-between gap-2">{s.sl === "points" ? "SL (poin)" : "k"}
        <input type="number" min={0} step="any" className={field} value={s.slValue} disabled={s.sl === "none"}
               onChange={(e) => { const v = numIn(e.target.value); if (v !== null && v > 0) set({ slValue: v }); }} /></label>
      {s.sl === "atr" && (
        <label className="flex items-center justify-between gap-2">Panjang ATR
          <input type="number" min={1} className={field} value={s.atrLen}
                 onChange={(e) => { const v = numIn(e.target.value); if (v !== null && v >= 1) set({ atrLen: Math.round(v) }); }} /></label>
      )}
      <label className="flex items-center justify-between gap-2">TP (R, kosong = tanpa)
        <input type="number" min={0} step="any" className={field} value={s.tpR ?? ""}
               onChange={(e) => { const v = numIn(e.target.value); set({ tpR: v !== null && v > 0 ? v : null }); }} /></label>
      <label className="flex items-center justify-between gap-2">Max hold (bar, kosong = tanpa)
        <input type="number" min={1} className={field} value={s.maxHold ?? ""}
               onChange={(e) => { const v = numIn(e.target.value); set({ maxHold: v !== null && v >= 1 ? Math.round(v) : null }); }} /></label>
      <label className="flex items-center justify-between gap-2">Spread cadangan (poin)
        <input type="number" min={0} className={field} value={s.spreadFallback ?? ""}
               onChange={(e) => { const v = numIn(e.target.value); set({ spreadFallback: v !== null && v >= 0 ? v : null }); }} /></label>
      <label className="flex items-center gap-2">
        <input type="checkbox" checked={s.oppositeCloses} onChange={(e) => set({ oppositeCloses: e.target.checked })} />
        Sinyal lawan menutup posisi</label>
      <label className="flex items-center gap-2">
        <input type="checkbox" checked={s.overlap} onChange={(e) => set({ overlap: e.target.checked })} />
        Izinkan posisi bertumpuk</label>
      <label className="flex items-center gap-2">
        <input type="checkbox" checked={s.showOnChart} onChange={(e) => set({ showOnChart: e.target.checked })} />
        Tampilkan di chart</label>
    </div>
  );
}
