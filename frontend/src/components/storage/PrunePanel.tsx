import { useState } from "react";
import { pruneCandles } from "../../lib/storageApi";

export interface PrunePanelProps {
  symbolsList?: string[];
  onPruneSuccess?: () => void;
}

const CUTOFF_OPTIONS = [
  { label: "30 Days", value: 30 },
  { label: "60 Days", value: 60 },
  { label: "90 Days", value: 90 },
  { label: "180 Days (6 Months)", value: 180 },
  { label: "365 Days (1 Year)", value: 365 },
];

export default function PrunePanel({
  symbolsList = ["XAUUSD", "EURUSD", "GBPUSD", "BTCUSD"],
  onPruneSuccess,
}: PrunePanelProps) {
  const [selectedSymbol, setSelectedSymbol] = useState<string>("all");
  const [olderThanDays, setOlderThanDays] = useState<number>(180);
  const [showConfirmModal, setShowConfirmModal] = useState<boolean>(false);
  const [submitting, setSubmitting] = useState<boolean>(false);
  const [feedback, setFeedback] = useState<{
    type: "success" | "error";
    message: string;
  } | null>(null);

  const handlePrune = async () => {
    setSubmitting(true);
    setFeedback(null);

    try {
      const symParam = selectedSymbol === "all" ? undefined : selectedSymbol;
      const res = await pruneCandles(symParam, olderThanDays);
      if (!res.ok || !res.data) {
        throw new Error(res.error ?? "Failed to prune candle data");
      }

      const deletedCount = res.data.deleted_bars ?? 0;
      const targetText = selectedSymbol === "all" ? "all symbols" : selectedSymbol;
      setFeedback({
        type: "success",
        message: `Prune operation completed successfully! Purged ${deletedCount.toLocaleString()} M1 candle bar(s) older than ${olderThanDays} days for ${targetText}.`,
      });

      setShowConfirmModal(false);
      if (onPruneSuccess) {
        onPruneSuccess();
      }
    } catch (err: any) {
      setFeedback({
        type: "error",
        message: err?.message || "Failed to prune historical candle data.",
      });
      setShowConfirmModal(false);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="space-y-4">
      {/* Feedback Banner */}
      {feedback && (
        <div
          className={`p-4 rounded-xl border flex items-start justify-between gap-3 text-sm ${
            feedback.type === "success"
              ? "bg-pos/10 border-pos/30 text-pos"
              : "bg-neg/10 border-neg/30 text-neg"
          }`}
        >
          <div className="flex items-center gap-2">
            {feedback.type === "success" ? (
              <svg className="w-5 h-5 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
              </svg>
            ) : (
              <svg className="w-5 h-5 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
            )}
            <span>{feedback.message}</span>
          </div>
          <button
            onClick={() => setFeedback(null)}
            className="text-muted hover:text-ink text-xs px-2 py-0.5 rounded"
          >
            Dismiss
          </button>
        </div>
      )}

      {/* Main Pruning Control Card */}
      <div className="glass p-6 rounded-xl border border-panel-border space-y-5">
        <div className="flex items-start justify-between gap-4">
          <div>
            <div className="flex items-center gap-2 text-ink font-bold text-base mb-1">
              <svg className="w-5 h-5 text-neg" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2}
                  d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"
                />
              </svg>
              <h3>Data Retention & Candle Pruning</h3>
            </div>
            <p className="text-xs text-muted leading-relaxed max-w-2xl">
              Purge historical M1 candle bars older than a specified threshold to free up disk space and optimize database query latency. Reconstructed trades and performance metrics are preserved.
            </p>
          </div>

          <div className="px-3 py-1.5 rounded-lg bg-neg/10 border border-neg/20 text-neg text-xs font-mono font-medium flex items-center gap-1.5 shrink-0">
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
            </svg>
            <span>Destructive Action</span>
          </div>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4 pt-2 border-t border-panel-border/50">
          {/* Target Symbol Selection */}
          <div>
            <label htmlFor="prune-symbol" className="block text-xs font-medium text-muted mb-1.5">
              Target Symbol
            </label>
            <select
              id="prune-symbol"
              value={selectedSymbol}
              onChange={(e) => setSelectedSymbol(e.target.value)}
              className="w-full bg-bg/90 border border-panel-border rounded-lg px-3 py-2 text-xs text-ink focus:border-neg"
            >
              <option value="all">All Symbols (Global Prune)</option>
              {symbolsList.map((sym) => (
                <option key={sym} value={sym}>
                  {sym}
                </option>
              ))}
            </select>
          </div>

          {/* Retention Threshold Selector */}
          <div>
            <label htmlFor="prune-cutoff" className="block text-xs font-medium text-muted mb-1.5">
              Cutoff Retention Threshold
            </label>
            <select
              id="prune-cutoff"
              value={olderThanDays}
              onChange={(e) => setOlderThanDays(Number(e.target.value))}
              className="w-full bg-bg/90 border border-panel-border rounded-lg px-3 py-2 text-xs text-ink focus:border-neg font-mono"
            >
              {CUTOFF_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  Older than {opt.label}
                </option>
              ))}
            </select>
          </div>
        </div>

        {/* Trigger Button */}
        <div className="pt-3 flex items-center justify-between border-t border-panel-border/50">
          <div className="text-xs text-muted/70 flex items-center gap-1.5">
            <span>Selected target:</span>
            <span className="font-mono text-ink font-semibold">
              {selectedSymbol === "all" ? "All Symbols" : selectedSymbol}
            </span>
            <span>•</span>
            <span>Cutoff:</span>
            <span className="font-mono text-neg font-semibold">&gt; {olderThanDays} days</span>
          </div>

          <button
            type="button"
            onClick={() => setShowConfirmModal(true)}
            className="px-4 py-2 rounded-lg bg-neg/20 hover:bg-neg/30 text-neg ring-1 ring-neg/40 text-xs font-semibold transition-colors flex items-center gap-2"
          >
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
            </svg>
            <span>Prune Historical Candles</span>
          </button>
        </div>
      </div>

      {/* Safety Confirmation Modal */}
      {showConfirmModal && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm p-4"
          onClick={() => !submitting && setShowConfirmModal(false)}
        >
          <div
            className="glass max-w-md w-full p-6 rounded-xl border border-neg/30 space-y-4 shadow-2xl"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center gap-2.5 text-neg font-bold text-base">
              <svg className="w-6 h-6 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
              </svg>
              <span>Confirm Candle Data Prune</span>
            </div>

            <p className="text-xs text-muted leading-relaxed">
              Are you sure you want to permanently purge M1 candle bars older than{" "}
              <strong className="text-ink font-mono">{olderThanDays} days</strong> for{" "}
              <strong className="text-ink font-mono">
                {selectedSymbol === "all" ? "All Symbols" : selectedSymbol}
              </strong>
              ? This action cannot be undone once executed.
            </p>

            <div className="p-3 rounded-lg bg-neg/10 border border-neg/20 text-neg text-xs font-mono">
              ⚡ Trade history, reconstruct logs, and chart PNG caches remain intact.
            </div>

            <div className="flex items-center justify-end gap-3 pt-3 border-t border-panel-border/50">
              <button
                type="button"
                className="px-4 py-2 rounded-lg bg-white/5 hover:bg-white/10 ring-1 ring-panel-border text-ink text-xs font-medium transition-colors"
                onClick={() => setShowConfirmModal(false)}
                disabled={submitting}
              >
                Cancel
              </button>
              <button
                type="button"
                className="px-4 py-2 rounded-lg bg-neg/90 hover:bg-neg text-bg text-xs font-semibold transition-colors flex items-center gap-2"
                onClick={handlePrune}
                disabled={submitting}
              >
                {submitting ? (
                  <>
                    <svg className="w-3.5 h-3.5 animate-spin" viewBox="0 0 24 24" fill="none">
                      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                      <path
                        className="opacity-75"
                        fill="currentColor"
                        d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"
                      />
                    </svg>
                    <span>Purging Data...</span>
                  </>
                ) : (
                  <span>Confirm & Purge</span>
                )}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export { PrunePanel };
