import { money, price } from "../lib/format";
import { unrealizedR, type TrainingPosition } from "../lib/replay";

export default function ReplayPositions(props: {
  positions: TrainingPosition[];
  currentClose: number | null;
  currency: string;
  onClose: (pid: number) => void;
}) {
  const open = props.positions.filter((p) => p.status !== "closed");
  const closed = props.positions.filter((p) => p.status === "closed");

  return (
    <div className="glass p-3 space-y-2 text-xs">
      <div className="font-semibold">Posisi</div>
      {open.length === 0 && <div className="text-muted">Tidak ada posisi terbuka.</div>}
      {open.map((p) => {
        const uR = props.currentClose !== null ? unrealizedR(p, props.currentClose) : null;
        return (
          <div key={p.id} className="flex items-center justify-between gap-2 border-b border-white/5 pb-1">
            <span>
              #{p.id} {p.direction === "buy" ? "▲" : "▼"} {p.volume}
              {p.status === "pending" ? " · pending" : ` @ ${p.entry_price !== null ? price(p.entry_price) : "—"}`}
              {p.close_requested_msc ? " · closing…" : ""}
            </span>
            <span className="flex items-center gap-2">
              <span className={uR !== null && uR < 0 ? "text-neg" : "text-pos"}>
                {uR !== null ? `${uR.toFixed(2)}R` : "—"}
              </span>
              {p.status === "open" && !p.close_requested_msc && (
                <button className="glass px-2 py-0.5 text-neg" onClick={() => props.onClose(p.id)}>Close</button>
              )}
            </span>
          </div>
        );
      })}
      {closed.length > 0 && (
        <div className="pt-2">
          <div className="font-semibold text-muted">Selesai</div>
          {closed.map((p) => (
            <div key={p.id} className="flex justify-between">
              <span>#{p.id} {p.exit_reason ?? ""}</span>
              <span className={p.net_profit !== null && p.net_profit < 0 ? "text-neg" : "text-pos"}>
                {p.r_multiple !== null ? `${p.r_multiple.toFixed(2)}R` : "—"}
                {p.net_profit !== null ? ` · ${money(p.net_profit, props.currency)}` : ""}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
