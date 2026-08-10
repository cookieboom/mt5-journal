import { useState } from "react";
import type { Tool } from "../lib/drawings";

const TOOLS: { tool: Tool; icon: string; label: string }[] = [
  { tool: "cursor", icon: "⌖", label: "kursor" },
  { tool: "trend", icon: "╱", label: "trendline" },
  { tool: "hline", icon: "─", label: "garis horizontal" },
  { tool: "rect", icon: "▭", label: "kotak" },
  { tool: "text", icon: "T", label: "teks" },
];

// Vertical icon column on the pane's left edge (TradingView layout). Clearing
// every drawing is destructive and unrecoverable, so it takes two clicks — a
// modal would be heavier than the action deserves, but one click is too few.
export default function DrawingPalette({
  tool, onTool, onClearAll, count,
}: {
  tool: Tool;
  onTool: (t: Tool) => void;
  onClearAll: () => void;
  count: number;
}) {
  const [confirming, setConfirming] = useState(false);
  return (
    <div className="glass absolute left-2 top-2 z-20 flex flex-col p-1 gap-1 text-[13px]">
      {TOOLS.map(({ tool: t, icon, label }) => (
        <button
          key={t}
          aria-label={label}
          aria-pressed={tool === t}
          title={label}
          onClick={() => onTool(t)}
          className={
            "w-7 h-7 leading-none " +
            (tool === t ? "bg-violet/25 text-ink" : "text-muted hover:text-ink")
          }
        >
          {icon}
        </button>
      ))}
      {count > 0 && (
        <button
          aria-label={confirming ? "yakin hapus semua" : "hapus semua"}
          title={confirming ? "Klik lagi untuk menghapus" : "Hapus semua gambar"}
          onClick={() => {
            if (confirming) { onClearAll(); setConfirming(false); } else { setConfirming(true); }
          }}
          onBlur={() => setConfirming(false)}
          className={"w-7 h-7 leading-none " + (confirming ? "text-neg" : "text-muted hover:text-neg")}
        >
          {confirming ? "!" : "🗑"}
        </button>
      )}
    </div>
  );
}
