import { useEffect, useRef, useState } from "react";
import { MAX_TEXT_LEN } from "../lib/drawings";

// Inline label editor, positioned at the anchor's pixel. Enter commits, Escape
// cancels, and a blank label is discarded rather than stored — an empty note is
// an accident, not an annotation.
//
// `settledRef` guards against a double commit: Enter fires onCommit and then,
// when the parent unmounts this input, some browsers dispatch a "blur" on the
// about-to-be-removed focused node — which would otherwise re-fire onCommit
// with the same closed-over value. Same guard makes Escape's onCancel immune
// to that trailing blur committing instead of cancelling.
export default function TextDrawingInput({
  x, y, initial, onCommit, onCancel,
}: {
  x: number;
  y: number;
  initial: string;
  onCommit: (text: string) => void;
  onCancel: () => void;
}) {
  const [value, setValue] = useState(initial);
  const ref = useRef<HTMLInputElement>(null);
  const settledRef = useRef(false);
  useEffect(() => { ref.current?.focus(); ref.current?.select(); }, []);

  return (
    <input
      ref={ref}
      aria-label="teks anotasi"
      data-testid="text-drawing-input"
      className="glass absolute z-30 px-1 py-0.5 text-[11px] bg-bg text-ink"
      style={{ left: x, top: y - 18, width: 160 }}
      value={value}
      maxLength={MAX_TEXT_LEN}
      onChange={(e) => setValue(e.target.value)}
      onKeyDown={(e) => {
        if (e.key === "Enter") {
          e.preventDefault();
          if (settledRef.current) return;
          settledRef.current = true;
          onCommit(value.trim());
        }
        if (e.key === "Escape") {
          e.preventDefault();
          if (settledRef.current) return;
          settledRef.current = true;
          onCancel();
        }
      }}
      onBlur={() => {
        if (settledRef.current) return;
        settledRef.current = true;
        onCommit(value.trim());
      }}
    />
  );
}
