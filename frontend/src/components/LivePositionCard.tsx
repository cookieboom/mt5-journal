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
  // "" = leave unchanged (null); a typed number (incl. 0) = that value.
  const opt = (s: string): number | null => (s.trim() === "" ? null : Number(s));
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
            onClick={() => onAction("sltp", { sl: opt(sl), tp: opt(tp) })}>Ubah SL/TP…</button>
        </div>
        <button className="px-3 py-1.5 rounded bg-neg/15 ring-1 ring-neg/35 text-ink"
          onClick={() => onAction("close", {})}>Tutup {pos.volume} lot…</button>
        <div className="flex gap-2 items-end">
          <label className="flex flex-col text-muted text-[10px]">Vol sebagian
            <input className="bg-white/5 rounded px-2 py-1 w-20 text-ink num" value={vol}
              onChange={(e) => setVol(e.target.value)} placeholder="0.01" /></label>
          <button className="px-3 py-1.5 rounded bg-white/8 ring-1 ring-panel-border text-ink"
            onClick={() => onAction("close-partial", { volume: opt(vol) })}>Tutup sebagian…</button>
          <button className="px-3 py-1.5 rounded bg-white/8 ring-1 ring-panel-border text-ink"
            onClick={() => onAction("add-volume", { volume: opt(vol) })}>Tambah (posisi BARU)…</button>
        </div>
      </div>
    </div>
  );
}
