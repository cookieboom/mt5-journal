import { ReactNode, useEffect } from "react";

// The right-hand floating layer: scrim, panel, Escape. One of the two places
// elevation is real (DESIGN.md § Elevation), so it carries the floating-layer
// shadow and every dialog affordance in one spot instead of per call site.
//
// The scrim is deliberately transparent: chart settings edits candle colours
// while you watch the canvas behind it, and darkening the page would defeat
// that. It still swallows the click that closes the sheet.
export default function Sheet({
  label, onClose, children, footer,
}: {
  label: string;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
}) {
  useEffect(() => {
    const onEsc = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onEsc);
    return () => window.removeEventListener("keydown", onEsc);
  }, [onClose]);

  return (
    <>
      <div className="fixed inset-0 z-30" onClick={onClose} />
      {/* Not `.glass`: at 4.5% white over the chart canvas the form fields and
          the live "Buka posisi" button read straight through the candles. The
          ground colour at 95% keeps the floating-layer language — blur, one
          hairline, the floating shadow — while staying legible. Flush to three
          viewport edges, so no radius, and the bottom clears the mobile nav. */}
      <div
        className="fixed right-0 top-0 z-40 h-full w-[300px] max-w-[85vw]
                   p-4 pb-[76px] md:pb-4 flex flex-col
                   bg-bg/95 backdrop-blur-[8px] border-l border-panel-border
                   shadow-float animate-sheet-in"
        role="dialog"
        aria-modal="true"
        aria-label={label}
      >
        <div className="flex items-center justify-between mb-3">
          <div className="text-ink text-[13px]">{label}</div>
          <button
            onClick={onClose}
            aria-label="tutup"
            className="text-muted hover:text-ink min-h-[44px] min-w-[44px] -mr-2"
          >✕</button>
        </div>

        <div className="flex-1 overflow-y-auto min-h-0">{children}</div>
        {footer}
      </div>
    </>
  );
}
