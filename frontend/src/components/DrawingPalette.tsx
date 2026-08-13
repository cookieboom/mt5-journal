import { useState, useEffect } from "react";
import type { Tool } from "../lib/drawings";

const TOOLS: { tool: Tool; icon: string; label: string }[] = [
  { tool: "cursor", icon: "⌖", label: "kursor" },
  { tool: "trend", icon: "╱", label: "trendline" },
  { tool: "hline", icon: "─", label: "garis horizontal" },
  { tool: "rect", icon: "▭", label: "kotak" },
  { tool: "text", icon: "T", label: "teks" },
];

const CONFIRM_TIMEOUT_MS = 3000; // ponytail: auto-expiry after 3s; extend if users find it too fast

// Vertical icon column on the pane's left edge (TradingView layout). Clearing
// every drawing is destructive and unrecoverable, so it takes two clicks — a
// modal would be heavier than the action deserves, but one click is too few.
// Auto-expiry on the confirmation state (via timer) covers browsers where blur
// never fires (Safari, historical Firefox on macOS); onBlur remains as a
// secondary fast-path for other browsers.
export default function DrawingPalette({
  tool, onTool, onClearAll, count,
}: {
  tool: Tool;
  onTool: (t: Tool) => void;
  onClearAll: () => void;
  count: number;
}) {
  const [confirming, setConfirming] = useState(false);

  // Auto-expiry timer ensures the confirm state cannot outlive user intent,
  // even when blur doesn't fire (macOS Safari/Firefox).
  useEffect(() => {
    if (!confirming) return;
    const timer = setTimeout(() => setConfirming(false), CONFIRM_TIMEOUT_MS);
    return () => clearTimeout(timer);
  }, [confirming]);

  // top-12, not top-2: Chart.tsx's loading/gaveup/error banners paint at
  // top-2 left-2 with no z-index of their own — at top-2 the palette
  // (persistent) painted over them (transient), so the loading banner never
  // showed on a background fetch. The palette is the one that should give
  // way; the banners are ephemeral and shouldn't have to dodge a
  // fixed-position tool column.
  return (
    <div className="glass absolute left-2 top-12 z-20 flex flex-col p-1 gap-1 text-title">
      {TOOLS.map(({ tool: t, icon, label }) => (
        <button
          key={t}
          type="button"
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
          type="button"
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
