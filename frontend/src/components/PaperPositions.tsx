import { money, price, rmult } from "../lib/format";
import type { PaperAccountView, PaperPosition } from "../lib/types";

const CLOSED_ROWS = 20;

/** The account is cross-symbol while the chart shows one. A row from elsewhere
 *  says so, rather than looking like it belongs to what is on screen. */
function Symbol_({ row, chartSymbol }: { row: PaperPosition; chartSymbol: string }) {
  const foreign = row.symbol !== chartSymbol;
  return (
    <span className={foreign ? "text-muted" : "text-ink"}
          title={foreign ? "posisi di simbol lain" : undefined}>
      {row.symbol}
    </span>
  );
}

const arrow = (d: string) => (d === "buy" ? "▲" : "▼");

export default function PaperPositions(props: {
  view: PaperAccountView;
  chartSymbol: string;
  onClose: (id: number) => void;
  onPartial: (id: number) => void;
  onReverse: (id: number) => void;
  onCancel: (id: number) => void;
  onCloseAll: () => void;
}) {
  const { open, pending, closed, header } = props.view;
  const ccy = header.currency;
  const closedRows = [...closed]
    .sort((a, b) => (b.exit_msc ?? 0) - (a.exit_msc ?? 0))
    .slice(0, CLOSED_ROWS);

  return (
    <div className="glass p-3 space-y-2 text-body">
      <div className="flex items-center justify-between gap-2">
        <span className="font-semibold">Posisi paper</span>
        {open.length + pending.length > 0 && (
          <button className="glass px-2 py-0.5 text-neg" onClick={props.onCloseAll}>
            Tutup semua
          </button>
        )}
      </div>

      {open.length === 0 && pending.length === 0 && (
        <div className="text-muted">Tidak ada posisi terbuka.</div>
      )}

      {open.map((p) => (
        <div key={p.id}
             className="flex items-center justify-between gap-2 border-b border-white/5 pb-1">
          <span>
            #{p.id} {arrow(p.direction)} {p.volume}{" "}
            <Symbol_ row={p} chartSymbol={props.chartSymbol} /> @ {price(p.entry_price)}
            <span className="text-muted">
              {" "}· SL {price(p.sl)} · TP {p.tp ? price(p.tp) : "—"}
            </span>
          </span>
          <span className="flex items-center gap-2">
            <span className={p.floating != null && p.floating < 0 ? "text-neg" : "text-pos"}>
              {money(p.floating ?? null, ccy, { sign: true })}
            </span>
            <button className="glass px-2 py-0.5 text-neg"
                    onClick={() => props.onClose(p.id)}>Tutup</button>
            <button className="glass px-2 py-0.5"
                    onClick={() => props.onPartial(p.id)}>Sebagian</button>
            <button className="glass px-2 py-0.5"
                    onClick={() => props.onReverse(p.id)}>Balik</button>
          </span>
        </div>
      ))}

      {pending.length > 0 && (
        <div className="pt-2 space-y-1">
          <div className="font-semibold text-muted">Order pending</div>
          {pending.map((p) => (
            <div key={p.id} className="flex items-center justify-between gap-2">
              <span>
                #{p.id} {p.order_kind} {arrow(p.direction)} {p.volume}{" "}
                <Symbol_ row={p} chartSymbol={props.chartSymbol} /> @{" "}
                {price(p.request_price)}
              </span>
              <button className="glass px-2 py-0.5 text-neg"
                      onClick={() => props.onCancel(p.id)}>Batalkan</button>
            </div>
          ))}
        </div>
      )}

      {closedRows.length > 0 && (
        <div className="pt-2 space-y-1">
          <div className="font-semibold text-muted">Selesai</div>
          {closedRows.map((p) => (
            <div key={p.id} className="flex justify-between gap-2">
              <span>
                #{p.id} <Symbol_ row={p} chartSymbol={props.chartSymbol} />{" "}
                <span className="text-muted">{p.exit_reason ?? ""}</span>
              </span>
              <span className={p.net_profit != null && p.net_profit < 0 ? "text-neg" : "text-pos"}>
                {rmult(p.r_multiple)} · {money(p.net_profit, ccy, { sign: true })}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
