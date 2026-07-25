import { useState } from "react";

// Fake market order at the NEXT bar's open (backend fills). SL/TP blank = none set
// (stored 0, rule 4). No signal/recommendation anywhere (rule 9).
export default function ReplayOrderTicket(props: {
  disabled: boolean;
  onSubmit: (o: { direction: "buy" | "sell"; volume: number; sl: number; tp: number }) => void;
}) {
  const [volume, setVolume] = useState(0.1);
  const [sl, setSl] = useState("");
  const [tp, setTp] = useState("");

  const submit = (direction: "buy" | "sell") => {
    props.onSubmit({
      direction, volume,
      sl: sl.trim() === "" ? 0 : Number(sl),
      tp: tp.trim() === "" ? 0 : Number(tp),
    });
  };

  return (
    <div className="glass p-3 space-y-2 text-xs">
      <div className="font-semibold">Order</div>
      <label className="block">Volume (lot)
        <input type="number" step="0.01" min="0.01" className="glass mt-1 w-full px-2 py-1"
               value={volume} onChange={(e) => setVolume(Number(e.target.value))} />
      </label>
      <label className="block">SL (kosong = tidak ada)
        <input type="number" step="0.001" className="glass mt-1 w-full px-2 py-1"
               value={sl} onChange={(e) => setSl(e.target.value)} />
      </label>
      <label className="block">TP (kosong = tidak ada)
        <input type="number" step="0.001" className="glass mt-1 w-full px-2 py-1"
               value={tp} onChange={(e) => setTp(e.target.value)} />
      </label>
      <div className="flex gap-2 pt-1">
        <button className="glass flex-1 py-1 text-pos" disabled={props.disabled}
                onClick={() => submit("buy")}>Buy</button>
        <button className="glass flex-1 py-1 text-neg" disabled={props.disabled}
                onClick={() => submit("sell")}>Sell</button>
      </div>
    </div>
  );
}
