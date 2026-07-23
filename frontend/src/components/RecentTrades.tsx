import { Equity } from "../lib/types";
import { wib, money } from "../lib/format";

// The dashboard's recent strip reads the equity series (closed trades, ordered).
export default function RecentTrades({
  equity, currency, offsetS,
}: { equity: Equity; currency: string; offsetS: number }) {
  const rows = [...equity.series].slice(-5).reverse();
  if (rows.length === 0)
    return <div className="text-muted text-sm py-6">Belum ada trade tertutup.</div>;
  return (
    <table className="w-full border-collapse text-[12px]">
      <thead>
        <tr className="text-muted text-left">
          <th className="pb-2 font-semibold uppercase text-[9.5px] tracking-wider">Tutup</th>
          <th className="pb-2 font-semibold uppercase text-[9.5px] tracking-wider num text-right">Equity</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.close_time_msc} className="border-t border-white/5">
            <td className="py-2 num">{wib(r.close_time_msc, offsetS)}</td>
            <td className="py-2 num text-right">{money(r.equity, currency)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
