import { useState } from "react";
import { placeOrder } from "../lib/paperApi";

type Sizing = "lot" | "risk";
type Kind = "market" | "limit" | "stop";

const KINDS: Kind[] = ["market", "limit", "stop"];
const KIND_LABEL: Record<Kind, string> = { market: "Market", limit: "Limit", stop: "Stop" };

function Field(props: {
  label: string; value: string; onChange: (v: string) => void; placeholder?: string;
}) {
  return (
    <label className="flex flex-col gap-0.5">
      <span className="text-caption text-muted">{props.label}</span>
      <input aria-label={props.label} value={props.value} inputMode="decimal"
             placeholder={props.placeholder}
             onChange={(e) => props.onChange(e.target.value)}
             className="glass px-2 py-1 text-body" />
    </label>
  );
}

function Toggle(props: { on: boolean; label: string; onClick: () => void }) {
  return (
    <button onClick={props.onClick}
            className={`glass px-2 py-0.5 text-caption ${props.on ? "text-ink" : "text-muted"}`}>
      {props.label}
    </button>
  );
}

export default function PaperOrderPanel(props: {
  accountId: number; symbol: string; lastPrice: number | null;
  onPlaced: () => void;
}) {
  const [kind, setKind] = useState<Kind>("market");
  const [sizing, setSizing] = useState<Sizing>("lot");
  const [volume, setVolume] = useState("0.01");
  const [riskPct, setRiskPct] = useState("1");
  const [price, setPrice] = useState("");
  const [sl, setSl] = useState("");
  const [tp, setTp] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(direction: "buy" | "sell") {
    setBusy(true);
    setError(null);
    try {
      // Exactly one sizing field, the other explicitly null. Enforced HERE, not
      // left to the server to catch: sending both would make the browser and the
      // server disagree about which number sized the position.
      const r = await placeOrder(props.accountId, {
        symbol: props.symbol, direction, kind,
        volume: sizing === "lot" ? Number(volume) : null,
        risk_pct: sizing === "risk" ? Number(riskPct) : null,
        price: kind === "market" ? null : Number(price),
        sl: Number(sl) || 0,
        tp: Number(tp) || 0,
      });
      // `postJson` RESOLVES a refusal as {ok:false, error} — it does not throw.
      // Reporting that as placed is how a rejected order reads as a filled one.
      if (r && (r as { ok?: boolean }).ok === false) {
        throw new Error((r as { error?: string }).error ?? "Order ditolak.");
      }
      props.onPlaced();
    } catch (e) {
      // The server's refusals are written for a human to read ("Harga XAUUSDc
      // basi 62s"). Replacing them with a generic message throws away the only
      // sentence that says what to do next.
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  // A pending order with no trigger price has nothing to wait for.
  const disabled = busy || (kind !== "market" && !price);

  return (
    <div className="glass p-3 space-y-2 text-body">
      <div className="flex items-center justify-between gap-2">
        <span className="font-semibold">{props.symbol}</span>
        <span className="text-caption text-muted">
          {props.lastPrice == null ? "harga belum ada" : props.lastPrice}
        </span>
      </div>

      <div className="flex gap-1">
        {KINDS.map((k) => (
          <Toggle key={k} on={kind === k} label={KIND_LABEL[k]} onClick={() => setKind(k)} />
        ))}
      </div>

      <div className="flex gap-1">
        <Toggle on={sizing === "lot"} label="Lot" onClick={() => setSizing("lot")} />
        <Toggle on={sizing === "risk"} label="Risiko %" onClick={() => setSizing("risk")} />
      </div>

      <div className="grid grid-cols-2 gap-2">
        {sizing === "lot"
          ? <Field label="volume" value={volume} onChange={setVolume} />
          : <Field label="risk-pct" value={riskPct} onChange={setRiskPct} />}
        {kind !== "market" && (
          <Field label="price" value={price} onChange={setPrice} placeholder="harga pemicu" />
        )}
        <Field label="sl" value={sl} onChange={setSl} placeholder="0 = tanpa SL" />
        <Field label="tp" value={tp} onChange={setTp} placeholder="0 = tanpa TP" />
      </div>

      {sizing === "risk" && (
        <div className="text-caption text-muted">
          Sizing dari risiko butuh SL — tanpa jarak stop tidak ada risiko untuk dibagi.
        </div>
      )}

      <div className="flex gap-2">
        <button disabled={disabled} onClick={() => void submit("buy")}
                className="glass px-3 py-1 text-pos font-semibold disabled:opacity-40">
          Beli
        </button>
        <button disabled={disabled} onClick={() => void submit("sell")}
                className="glass px-3 py-1 text-neg font-semibold disabled:opacity-40">
          Jual
        </button>
      </div>

      {error && <div role="alert" className="text-neg text-caption">{error}</div>}
    </div>
  );
}
