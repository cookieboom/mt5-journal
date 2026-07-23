import { CommandRow } from "../lib/types";
import { wib, price } from "../lib/format";

const STATUS_TONE: Record<string, string> = {
  done: "text-pos bg-pos/10", failed: "text-neg bg-neg/10",
  rejected: "text-neg bg-neg/10", pending: "text-muted bg-white/6",
};

export default function CommandsTable({ rows, offsetS }: { rows: CommandRow[]; offsetS: number }) {
  if (rows.length === 0) return <div className="text-muted text-sm py-6">Belum ada perintah.</div>;
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-[12px]">
        <thead>
          <tr className="text-muted text-left">
            {["Waktu", "Posisi", "Jenis", "SL/TP/Vol", "Status", "Retcode", "Catatan"].map((h) => (
              <th key={h} className="pb-2 font-semibold uppercase text-[9.5px] tracking-wider whitespace-nowrap">{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((c) => (
            <tr key={c.id} className="border-t border-white/5 align-top">
              <td className="py-2 num whitespace-nowrap">{wib(c.requested_msc, offsetS)}</td>
              <td className="py-2 num">#{c.position_id}</td>
              <td className="py-2">{c.kind}</td>
              <td className="py-2 num">{price(c.sl)} / {price(c.tp)} / {c.volume ?? "—"}</td>
              <td className="py-2"><span className={"px-2 py-0.5 rounded text-[10px] " + (STATUS_TONE[c.status] ?? "text-muted bg-white/6")}>{c.status}</span></td>
              <td className="py-2 num">{c.retcode_name ?? "—"}</td>
              <td className="py-2 text-muted max-w-[280px]">{c.error ?? c.broker_comment ?? ""}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
