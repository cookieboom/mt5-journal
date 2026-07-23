import { useState } from "react";
import { LivePosition, ActionKind, CommandBody } from "../lib/types";
import { money, price } from "../lib/format";

export default function LivePositionCard({
  pos, currency, onAction,
}: {
  pos: LivePosition;
  currency: string;
  onAction: (action: ActionKind, body: CommandBody) => void;
}) {
  const [sl, setSl] = useState("");
  const [tp, setTp] = useState("");
  const [vol, setVol] = useState("");
  const [fieldError, setFieldError] = useState<string | null>(null);
  // "" = leave unchanged (null); a typed number (incl. 0) = that value;
  // a non-empty but non-numeric entry (e.g. "40o0") returns NaN as a sentinel —
  // callers below must check for it and refuse to submit, since
  // JSON.stringify(NaN) === "null" and the server would silently read that
  // as "leave unchanged" instead of rejecting the typo.
  const opt = (s: string): number | null => {
    const t = s.trim();
    if (t === "") return null;
    const n = Number(t);
    return Number.isFinite(n) ? n : NaN;
  };
  const dirTone = pos.direction === "buy" ? "text-cyan" : "text-violet";
  const pnlTone = (pos.profit ?? 0) >= 0 ? "text-pos" : "text-neg";

  return (
    <div className="glass p-4 mb-3">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-[14px] font-semibold">
          {pos.symbol_base} <span className={`uppercase ${dirTone}`}>{pos.direction}</span>
          <span className="text-muted num text-[12px] ml-2">#{pos.position_id}</span>
        </h3>
        <div className={`num text-[15px] font-bold ${pnlTone}`}>
          {money(pos.profit, currency, { sign: true })} <span className="text-[10px] text-muted font-normal">floating</span>
        </div>
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-x-4 gap-y-1 text-[12px] mb-3">
        <div><span className="text-muted">Vol </span><span className="num">{pos.volume}</span></div>
        <div><span className="text-muted">Buka </span><span className="num">{price(pos.open_price)}</span></div>
        <div><span className="text-muted">Now </span><span className="num">{price(pos.price_current)}</span></div>
        <div><span className="text-muted">SL/TP </span><span className="num">{price(pos.sl)} / {price(pos.tp)}</span></div>
      </div>

      <div className="flex flex-wrap gap-3 items-end text-[12px]">
        <div className="flex gap-2 items-end">
          <label className="flex flex-col text-muted text-[10px]">SL
            <input className="bg-white/5 rounded px-2 py-1 w-24 text-ink num" value={sl}
              onChange={(e) => setSl(e.target.value)} placeholder="kosong=tetap · 0=hapus" /></label>
          <label className="flex flex-col text-muted text-[10px]">TP
            <input className="bg-white/5 rounded px-2 py-1 w-24 text-ink num" value={tp}
              onChange={(e) => setTp(e.target.value)} placeholder="kosong=tetap · 0=hapus" /></label>
          <button className="px-3 py-1.5 rounded bg-violet/20 ring-1 ring-violet/40 text-ink"
            onClick={() => {
              const slV = opt(sl), tpV = opt(tp);
              // Guard: a non-empty-but-invalid entry must never reach onAction —
              // NaN would serialize to null and be silently read as "unchanged".
              if (Number.isNaN(slV) || Number.isNaN(tpV)) { setFieldError("angka tidak valid"); return; }
              setFieldError(null);
              onAction("sltp", { sl: slV, tp: tpV });
            }}>Ubah SL/TP…</button>
        </div>
        <button className="px-3 py-1.5 rounded bg-neg/15 ring-1 ring-neg/35 text-ink"
          onClick={() => { setFieldError(null); onAction("close", {}); }}>Tutup {pos.volume} lot…</button>
        <div className="flex gap-2 items-end">
          <label className="flex flex-col text-muted text-[10px]">Vol sebagian
            <input className="bg-white/5 rounded px-2 py-1 w-20 text-ink num" value={vol}
              onChange={(e) => setVol(e.target.value)} placeholder="0.01" /></label>
          <button className="px-3 py-1.5 rounded bg-white/8 ring-1 ring-panel-border text-ink"
            onClick={() => {
              const volV = opt(vol);
              if (Number.isNaN(volV)) { setFieldError("angka tidak valid"); return; }
              setFieldError(null);
              onAction("close-partial", { volume: volV });
            }}>Tutup sebagian…</button>
          <button className="px-3 py-1.5 rounded bg-white/8 ring-1 ring-panel-border text-ink"
            onClick={() => {
              const volV = opt(vol);
              if (Number.isNaN(volV)) { setFieldError("angka tidak valid"); return; }
              setFieldError(null);
              onAction("add-volume", { volume: volV });
            }}>Tambah (posisi BARU)…</button>
        </div>
      </div>
      {fieldError && <div className="text-neg text-[11px] mt-1">{fieldError}</div>}
    </div>
  );
}
