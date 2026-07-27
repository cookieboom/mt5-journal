import { useState } from "react";
import { clearCache, vacuumDb, rebuildTrades } from "../../lib/storageApi";
import { formatBytes } from "./DiskStatsCard";

export interface MaintenancePanelProps {
  onSuccess?: () => void;
}

type ActionType = "cache" | "vacuum" | "rebuild" | null;

export default function MaintenancePanel({ onSuccess }: MaintenancePanelProps) {
  const [activeAction, setActiveAction] = useState<ActionType>(null);
  const [submitting, setSubmitting] = useState<boolean>(false);
  const [feedback, setFeedback] = useState<{
    type: "success" | "error";
    message: string;
  } | null>(null);

  const handleExecute = async () => {
    if (!activeAction) return;

    setSubmitting(true);
    setFeedback(null);

    try {
      if (activeAction === "cache") {
        const res = await clearCache();
        if (!res.ok || !res.data) {
          throw new Error(res.error ?? "Failed to clear cache");
        }
        setFeedback({
          type: "success",
          message: `Cache cleared successfully! Removed ${res.data.cleared_files.toLocaleString()} file(s), freeing ${formatBytes(res.data.freed_bytes)}.`,
        });
      } else if (activeAction === "vacuum") {
        const res = await vacuumDb();
        if (!res.ok || !res.data) {
          throw new Error(res.error ?? "Failed to vacuum database");
        }
        setFeedback({
          type: "success",
          message: `Database vacuum completed successfully! New database size: ${formatBytes(res.data.db_size_after)}.`,
        });
      } else if (activeAction === "rebuild") {
        const res = await rebuildTrades();
        if (!res.ok || !res.data) {
          throw new Error(res.error ?? "Failed to rebuild trades");
        }
        setFeedback({
          type: "success",
          message: `Trade reconstruction completed successfully! Rebuilt ${res.data.trades_rebuilt.toLocaleString()} trade(s) and auto-tags.`,
        });
      }

      setActiveAction(null);
      if (onSuccess) {
        onSuccess();
      }
    } catch (err: any) {
      setFeedback({
        type: "error",
        message: err?.message || `Failed to execute ${activeAction} operation.`,
      });
      setActiveAction(null);
    } finally {
      setSubmitting(false);
    }
  };

  const modalDetails: Record<
    Exclude<ActionType, null>,
    { title: string; body: string; buttonText: string; buttonClass: string }
  > = {
    cache: {
      title: "Clear PNG & Report Cache",
      body: "Are you sure you want to delete all cached trade PNG charts and rendered report HTML files? Trade history and candle data will not be deleted, and cache files will automatically regenerate when requested.",
      buttonText: "Clear Cache",
      buttonClass: "bg-cyan/20 ring-1 ring-cyan/45 hover:bg-cyan/30 text-ink font-semibold",
    },
    vacuum: {
      title: "Vacuum & Optimize Database",
      body: "Are you sure you want to run SQLite VACUUM? This defragments database pages and reclaims unused disk space. The database may be briefly locked during this operation.",
      buttonText: "Vacuum Database",
      buttonClass: "bg-violet/20 ring-1 ring-violet/45 hover:bg-violet/30 text-ink font-semibold",
    },
    rebuild: {
      title: "Rebuild Trades & Auto-tags",
      body: "Are you sure you want to rebuild all reconstructed trades from raw MT5 deal records? This will recalculate MAE/MFE excursions, performance metrics, and re-generate auto-tags across all trades.",
      buttonText: "Rebuild Trades",
      buttonClass: "bg-pos/20 ring-1 ring-pos/45 hover:bg-pos/30 text-ink font-semibold",
    },
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

      {/* Maintenance Action Cards */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {/* Card 1: Clear Cache */}
        <div className="glass p-5 rounded-xl border border-panel-border flex flex-col justify-between">
          <div>
            <div className="flex items-center gap-2 text-ink font-semibold mb-2">
              <svg className="w-5 h-5 text-cyan" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2}
                  d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"
                />
              </svg>
              <span>PNG & Report Cache</span>
            </div>
            <p className="text-xs text-muted leading-relaxed mb-4">
              Clear pre-rendered trade PNG images and weekly summary cache to free disk space.
            </p>
          </div>
          <button
            onClick={() => setActiveAction("cache")}
            className="w-full py-2 px-3 rounded-lg bg-cyan/10 hover:bg-cyan/20 ring-1 ring-cyan/30 text-cyan text-xs font-semibold transition-colors flex items-center justify-center gap-1.5"
          >
            Clear PNG & Report Cache
          </button>
        </div>

        {/* Card 2: Vacuum DB */}
        <div className="glass p-5 rounded-xl border border-panel-border flex flex-col justify-between">
          <div>
            <div className="flex items-center gap-2 text-ink font-semibold mb-2">
              <svg className="w-5 h-5 text-violet" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2}
                  d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"
                />
              </svg>
              <span>SQLite Database</span>
            </div>
            <p className="text-xs text-muted leading-relaxed mb-4">
              Run SQLite VACUUM to defragment storage and reclaim unused space from deleted records.
            </p>
          </div>
          <button
            onClick={() => setActiveAction("vacuum")}
            className="w-full py-2 px-3 rounded-lg bg-violet/10 hover:bg-violet/20 ring-1 ring-violet/30 text-violet text-xs font-semibold transition-colors flex items-center justify-center gap-1.5"
          >
            Vacuum & Optimize DB
          </button>
        </div>

        {/* Card 3: Rebuild Trades */}
        <div className="glass p-5 rounded-xl border border-panel-border flex flex-col justify-between">
          <div>
            <div className="flex items-center gap-2 text-ink font-semibold mb-2">
              <svg className="w-5 h-5 text-pos" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2}
                  d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"
                />
              </svg>
              <span>Trades & Auto-tags</span>
            </div>
            <p className="text-xs text-muted leading-relaxed mb-4">
              Re-analyze MT5 raw deals, fold trade positions, recalculate MAE/MFE and refresh auto-tags.
            </p>
          </div>
          <button
            onClick={() => setActiveAction("rebuild")}
            className="w-full py-2 px-3 rounded-lg bg-pos/10 hover:bg-pos/20 ring-1 ring-pos/30 text-pos text-xs font-semibold transition-colors flex items-center justify-center gap-1.5"
          >
            Rebuild Trades & Auto-tags
          </button>
        </div>
      </div>

      {/* Confirmation Modal */}
      {activeAction && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4"
          onClick={() => !submitting && setActiveAction(null)}
        >
          <div
            className="glass max-w-md w-full p-6 rounded-xl border border-panel-border space-y-4"
            onClick={(e) => e.stopPropagation()}
          >
            <h2 className="text-base font-bold text-ink">
              {modalDetails[activeAction].title}
            </h2>
            <p className="text-xs text-muted leading-relaxed">
              {modalDetails[activeAction].body}
            </p>

            <div className="flex items-center justify-end gap-3 pt-2 border-t border-panel-border/50">
              <button
                type="button"
                className="px-4 py-2 rounded-lg bg-white/5 hover:bg-white/10 ring-1 ring-panel-border text-ink text-xs font-medium transition-colors"
                onClick={() => setActiveAction(null)}
                disabled={submitting}
              >
                Cancel
              </button>
              <button
                type="button"
                className={`px-4 py-2 rounded-lg text-xs transition-colors flex items-center gap-2 ${modalDetails[activeAction].buttonClass}`}
                onClick={handleExecute}
                disabled={submitting}
              >
                {submitting ? (
                  <>
                    <svg className="w-3.5 h-3.5 animate-spin" viewBox="0 0 24 24" fill="none">
                      <circle
                        className="opacity-25"
                        cx="12"
                        cy="12"
                        r="10"
                        stroke="currentColor"
                        strokeWidth="4"
                      />
                      <path
                        className="opacity-75"
                        fill="currentColor"
                        d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"
                      />
                    </svg>
                    <span>Executing...</span>
                  </>
                ) : (
                  modalDetails[activeAction].buttonText
                )}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export { MaintenancePanel };
