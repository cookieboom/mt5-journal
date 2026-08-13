import type { RiskPrefs, SizeResult } from "../lib/types";
import { money, price } from "../lib/format";

// Risk-first order panel. The human sets a stop and a budget; the SERVER
// derives the size (Task 9's useRiskSizing → POST /api/size). Nothing here
// computes a lot, a risk, a distance, or an R:R — every figure is read
// straight off `result`. And nothing here suggests a level, a side, or a
// moment (rule 9): the direction shown is read off the stop the human
// already placed, not proposed by this panel.
export default function RiskSizePanel(props: {
  disabled: boolean;
  // Non-null = the reference price is not fresh enough to commit size against
  // (`staleEntryReason`). Separate from `disabled` only so the panel can say
  // WHY: a dead button with no reason is its own trap. Live mode only —
  // replay's cursor bar is the price by definition.
  blocked?: string | null;
  currency: string;
  prefs: RiskPrefs;
  onPrefsChange: (p: RiskPrefs) => void;
  entry: number | null;
  sl: number | null;
  tp: number | null;
  onSlChange: (v: number | null) => void;
  onTpChange: (v: number | null) => void;
  result: SizeResult | null;
  loading: boolean;
  onSubmit: (o: { direction: "buy" | "sell"; volume: number }) => void;
}) {
  const r = props.result;
  const direction = r?.direction ?? null;
  const ready = !props.disabled && !props.blocked && !props.loading && !!r
    && r.error === null && r.volume !== null && direction !== null;

  // "" -> null, not 0: an empty field means "not set", and 0 is a real price
  // on the chart (rule 4).
  const toPriceOrNull = (s: string): number | null => (s.trim() === "" ? null : Number(s));

  const label = props.loading ? "Menghitung…"
    : direction === "buy" ? "Buy" : direction === "sell" ? "Sell" : "Buka posisi";
  const tone = direction === "buy" ? "text-pos" : direction === "sell" ? "text-neg" : "";

  return (
    <div className="glass p-3 space-y-2 text-body">
      <div className="font-semibold">Ukuran otomatis</div>

      <div className="flex gap-1">
        <button
          type="button"
          className={`glass flex-1 py-1 ${props.prefs.mode === "pct" ? "font-semibold" : "opacity-60"}`}
          onClick={() => props.onPrefsChange({ ...props.prefs, mode: "pct" })}
        >%</button>
        <button
          type="button"
          className={`glass flex-1 py-1 ${props.prefs.mode === "usc" ? "font-semibold" : "opacity-60"}`}
          onClick={() => props.onPrefsChange({ ...props.prefs, mode: "usc" })}
        >USC</button>
      </div>

      <label className="block">
        Risiko ({props.prefs.mode === "pct" ? "% balance" : props.currency})
        <input
          type="number" step={props.prefs.mode === "pct" ? "0.1" : "1"} min="0"
          className="glass mt-1 w-full px-2 py-1"
          value={props.prefs.value}
          onChange={(e) => props.onPrefsChange({ ...props.prefs, value: Number(e.target.value) })}
        />
      </label>

      <label className="block">
        SL (tarik garis di chart, atau ketik)
        <input
          type="number" step="0.001" className="glass mt-1 w-full px-2 py-1"
          value={props.sl ?? ""}
          onChange={(e) => props.onSlChange(toPriceOrNull(e.target.value))}
        />
      </label>

      <label className="block">
        TP (kosong = tidak ada)
        <input
          type="number" step="0.001" className="glass mt-1 w-full px-2 py-1"
          value={props.tp ?? ""}
          onChange={(e) => props.onTpChange(toPriceOrNull(e.target.value))}
        />
      </label>

      <div className="space-y-1 border-t border-panel-border pt-2">
        <Row label="Harga" value={price(props.entry)} />
        <Row label="Jarak SL" value={price(r?.distance ?? null)} />
        <Row label="Lot" value={r?.volume == null ? "—" : r.volume.toFixed(2)} testId="lot" />
        <Row
          label="Risiko"
          testId="risk"
          value={r?.risk_usc == null
            ? "n/a"
            : money(r.risk_usc, props.currency) +
              (r.risk_pct == null ? "" : ` (${r.risk_pct.toFixed(2)}%)`)}
        />
        <Row label="R:R" value={r?.rr == null ? "—" : r.rr.toFixed(2)} testId="rr" />
      </div>

      {props.blocked && (
        <div data-testid="stale-block" className="text-neg">{props.blocked}</div>
      )}
      {r?.error && (
        <div data-testid="size-error" className="text-neg">{r.error}</div>
      )}
      {!r && !props.loading && (
        <div data-testid="size-hint" className="text-muted">
          Tarik garis SL dari harga sekarang untuk mulai menghitung lot.
        </div>
      )}

      <button
        type="button"
        className={`glass w-full py-1 ${tone}`}
        disabled={!ready}
        onClick={() => {
          if (r?.volume != null && direction !== null) {
            props.onSubmit({ direction, volume: r.volume });
          }
        }}
      >
        {label}
      </button>

      {/* MARKET execution: the broker fills at its own price, so realised risk
          can differ from the target by entry slippage. Said once, here,
          rather than pretending the number above is exact. */}
      <div className="text-muted">
        Eksekusi pasar — harga isi bisa bergeser dari harga acuan.
      </div>
    </div>
  );
}

function Row(props: { label: string; value: string; testId?: string }) {
  return (
    <div className="flex justify-between">
      <span className="text-muted">{props.label}</span>
      <span className="num" data-testid={props.testId}>{props.value}</span>
    </div>
  );
}
