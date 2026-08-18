import { money } from "../lib/format";
import type { PaperHeader } from "../lib/types";

/** The one number that decides liquidation should look different BEFORE it
 *  fires, not after. Within 1.5x of the stop-out level it turns to the `neg`
 *  token; unknown stays muted rather than alarming. */
function levelTone(level: number | null, stopoutPct: number): string {
  if (level == null) return "text-muted";
  return level <= stopoutPct * 1.5 ? "text-neg" : "text-ink";
}

function Figure(props: { label: string; value: string; tone?: string }) {
  return (
    <div className="flex flex-col">
      <span className="text-caption text-muted">{props.label}</span>
      <span aria-label={props.label.toLowerCase().replace(/ /g, "-")}
            className={props.tone ?? "text-ink"}>{props.value}</span>
    </div>
  );
}

/** Balance, equity and margin for one virtual account. Every money figure goes
 *  through `money()` — the same mirror of `web/format.py` the rest of the app
 *  uses — so an unknown reads as `n/a` and never as a wiped 0, and the USC unit
 *  is never implied (CLAUDE.md rule 4, and the account currency rule). */
export default function PaperAccountBar(props: {
  header: PaperHeader; name: string; live: boolean;
}) {
  const h = props.header;
  const ccy = h.currency;
  return (
    <div className="glass p-3 space-y-2 text-body">
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-title">{props.name}</span>
        <span className="text-caption text-muted">
          PAPER · 1:{h.leverage} · stop-out {h.stopout_pct}%
        </span>
      </div>

      <div className="grid grid-cols-3 gap-2 sm:grid-cols-6">
        <Figure label="balance" value={money(h.balance, ccy)} />
        <Figure label="equity" value={money(h.equity, ccy)} />
        <Figure label="margin" value={money(h.margin, ccy)} />
        <Figure label="free margin" value={money(h.free_margin, ccy)} />
        <Figure label="margin level"
                value={h.margin_level == null ? "n/a" : `${h.margin_level.toFixed(1)}%`}
                tone={levelTone(h.margin_level, h.stopout_pct)} />
        <Figure label="floating" value={money(h.floating, ccy, { sign: true })}
                tone={h.floating == null ? "text-muted"
                      : h.floating < 0 ? "text-neg" : "text-pos"} />
      </div>

      {!props.live && (
        <div role="status" className="text-caption text-neg">
          Feed mati — posisi tidak dipantau. SL/TP dan stop-out hanya jalan
          saat `journal live` menyuapi tick.
        </div>
      )}
    </div>
  );
}
