import { useState } from "react";
import { optNum } from "../lib/parse";
import Modal from "./Modal";

export default function SltpConfirmDialog(props: {
  positionId: number;
  kind: "sl" | "tp";
  price: number;
  removing?: boolean;
  onConfirm: (price: number) => void;
  onCancel: () => void;
}) {
  // props.price originates from chart.priceToCoordinate/coordinateToPrice's
  // unrounded linear pixel->price mapping — round the initial pre-fill to 5
  // decimals (matches lib/sltpDrag.ts's ghostTitle convention for the same
  // kind of value). Only the initial value is rounded; edits afterward are
  // untouched.
  const [value, setValue] = useState(props.price.toFixed(5));
  const [fieldError, setFieldError] = useState<string | null>(null);
  const label = props.kind === "sl" ? "SL" : "TP";

  return (
    <Modal
      label={props.removing ? `Hapus ${label}` : `Atur ${label}`}
      width="w-[min(24rem,calc(100vw-2rem))]"
      onClose={props.onCancel}
    >
      <h2 className="text-headline font-bold mb-1">
        {props.removing ? `Hapus ${label}?` : `Atur ${label} — posisi #${props.positionId}`}
      </h2>
      {props.removing ? (
        <p className="text-body text-neg mb-3">
          Posisi jadi tanpa {label === "SL" ? "stop-loss" : "take-profit"}. Lanjutkan?
        </p>
      ) : (
        <label className="flex flex-col text-muted text-label mb-3">
          {label}
          <input
            className="bg-white/5 rounded px-2 py-1 text-ink num"
            aria-label={label}
            value={value}
            onChange={(e) => { setValue(e.target.value); setFieldError(null); }}
          />
        </label>
      )}
      {fieldError && <div className="text-neg text-meta mb-2">{fieldError}</div>}
      <div className="flex justify-end gap-2">
        <button className="px-3 py-1.5 rounded bg-white/8 ring-1 ring-panel-border text-ink"
          onClick={props.onCancel}>Batal</button>
        <button className="px-3 py-1.5 rounded bg-cyan/20 ring-1 ring-cyan/45 text-ink font-semibold"
          onClick={() => {
            if (props.removing) { props.onConfirm(0); return; }
            const n = optNum(value);
            if (n === null || Number.isNaN(n)) { setFieldError("angka tidak valid"); return; }
            props.onConfirm(n);
          }}>Konfirmasi</button>
      </div>
    </Modal>
  );
}
