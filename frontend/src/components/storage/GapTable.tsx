import { useState } from "react";
import { fetchBackfill, fillAllGaps, type CandleCompleteness, type GapItem } from "../../lib/storageApi";

export interface GapTableProps {
  completeness?: CandleCompleteness | null;
  loading?: boolean;
  onRefresh?: () => void;
}

function formatDate(ms: number): string {
  if (!ms || ms <= 0) return "N/A";
  const d = new Date(ms);
  return d.toISOString().replace("T", " ").slice(0, 19) + " UTC";
}

export default function GapTable({ completeness, loading, onRefresh }: GapTableProps) {
  const [isFillingAll, setIsFillingAll] = useState<boolean>(false);
  const [loadingGapKey, setLoadingGapKey] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<{
    type: "success" | "error";
    message: string;
  } | null>(null);

  const handleFillAllGaps = async () => {
    if (!completeness || completeness.gaps.length === 0) return;

    setIsFillingAll(true);
    setFeedback(null);

    try {
      const res = await fillAllGaps(completeness.symbol, completeness.timeframe);
      if (!res.ok || !res.data) {
        throw new Error(res.error ?? "Failed to initiate fill all gaps request");
      }
      setFeedback({
        type: "success",
        message: `Successfully queued ${res.data.requests_count} backfill request(s) for all gaps in ${completeness.symbol} (${completeness.timeframe}).`,
      });
      if (onRefresh) {
        onRefresh();
      }
    } catch (err: any) {
      setFeedback({
        type: "error",
        message: err?.message || "Failed to fill all gaps.",
      });
    } finally {
      setIsFillingAll(false);
    }
  };

  const handleFillSingleGap = async (gap: GapItem) => {
    if (!completeness) return;

    const gapKey = `${gap.from_ms}-${gap.to_ms}`;
    setLoadingGapKey(gapKey);
    setFeedback(null);

    try {
      const res = await fetchBackfill(
        completeness.symbol,
        completeness.timeframe,
        gap.from_ms,
        gap.to_ms
      );
      if (!res.ok || !res.data) {
        throw new Error(res.error ?? "Failed to queue gap backfill request");
      }
      setFeedback({
        type: "success",
        message: `Queued backfill request #${res.data.request_id} for gap (${gap.duration_hours.toFixed(
          1
        )} hrs) from ${formatDate(gap.from_ms)}.`,
      });
      if (onRefresh) {
        onRefresh();
      }
    } catch (err: any) {
      setFeedback({
        type: "error",
        message: err?.message || "Failed to fill gap.",
      });
    } finally {
      setLoadingGapKey(null);
    }
  };

  const gaps = completeness?.gaps ?? [];

  return (
    <div className="glass p-5 rounded-xl border border-panel-border space-y-4">
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2.5">
          <h3 className="text-title font-bold text-ink flex items-center gap-2">
            <svg className="w-5 h-5 text-neg" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"
              />
            </svg>
            <span>Detected Gaps</span>
          </h3>
          <span className="px-2 py-0.5 rounded-full text-body font-mono font-semibold bg-neg/10 text-neg border border-neg/20">
            {gaps.length} {gaps.length === 1 ? "gap" : "gaps"}
          </span>
        </div>

        <button
          type="button"
          onClick={handleFillAllGaps}
          disabled={loading || isFillingAll || gaps.length === 0}
          className="py-1.5 px-3 rounded-lg bg-neg/20 hover:bg-neg/30 text-neg ring-1 ring-neg/40 text-body font-semibold transition-colors disabled:opacity-40 disabled:cursor-not-allowed flex items-center gap-2"
        >
          {isFillingAll ? (
            <>
              <svg className="w-3.5 h-3.5 animate-spin" viewBox="0 0 24 24" fill="none">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                <path
                  className="opacity-75"
                  fill="currentColor"
                  d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"
                />
              </svg>
              <span>Queueing All...</span>
            </>
          ) : (
            <>
              <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
              </svg>
              <span>Fill All Gaps</span>
            </>
          )}
        </button>
      </div>

      {/* Feedback Banner */}
      {feedback && (
        <div
          className={`p-3.5 rounded-xl border flex items-start justify-between gap-3 text-body ${
            feedback.type === "success"
              ? "bg-pos/10 border-pos/30 text-pos"
              : "bg-neg/10 border-neg/30 text-neg"
          }`}
        >
          <div className="flex items-center gap-2">
            {feedback.type === "success" ? (
              <svg className="w-4 h-4 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
              </svg>
            ) : (
              <svg className="w-4 h-4 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
            )}
            <span>{feedback.message}</span>
          </div>
          <button
            onClick={() => setFeedback(null)}
            className="text-muted hover:text-ink text-body px-1.5 py-0.5 rounded"
          >
            Dismiss
          </button>
        </div>
      )}

      {/* Table Content */}
      {loading ? (
        <div className="space-y-2 animate-pulse py-2">
          {[1, 2, 3].map((i) => (
            <div key={i} className="h-10 bg-white/5 rounded-lg w-full"></div>
          ))}
        </div>
      ) : gaps.length === 0 ? (
        <div className="p-6 rounded-xl bg-pos/10 border border-pos/20 text-center space-y-2">
          <div className="flex items-center justify-center gap-2 text-pos font-semibold text-body">
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
            <span>No Gaps Detected</span>
          </div>
          <p className="text-body text-muted">
            Candle coverage for {completeness?.symbol || "symbol"} ({completeness?.timeframe || "M1"}) is completely continuous!
          </p>
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-body border-collapse">
            <thead>
              <tr className="border-b border-panel-border/80 text-muted font-medium">
                <th className="py-2.5 px-3">Start Time</th>
                <th className="py-2.5 px-3">End Time</th>
                <th className="py-2.5 px-3">Duration</th>
                <th className="py-2.5 px-3 text-right">Action</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-panel-border/40">
              {gaps.map((gap, idx) => {
                const gapKey = `${gap.from_ms}-${gap.to_ms}`;
                const isItemLoading = loadingGapKey === gapKey;

                return (
                  <tr key={`gap-row-${idx}-${gap.from_ms}`} className="hover:bg-white/[0.02] transition-colors">
                    <td className="py-2.5 px-3 font-mono text-ink">
                      {formatDate(gap.from_ms)}
                    </td>
                    <td className="py-2.5 px-3 font-mono text-ink">
                      {formatDate(gap.to_ms)}
                    </td>
                    <td className="py-2.5 px-3 font-mono text-warn">
                      {gap.duration_hours >= 24
                        ? `${(gap.duration_hours / 24).toFixed(1)} days`
                        : `${gap.duration_hours.toFixed(1)} hrs`}
                    </td>
                    <td className="py-2.5 px-3 text-right">
                      <button
                        type="button"
                        onClick={() => handleFillSingleGap(gap)}
                        disabled={isItemLoading || isFillingAll}
                        className="py-1 px-2.5 rounded bg-cyan/10 hover:bg-cyan/20 text-cyan ring-1 ring-cyan/30 text-body font-semibold transition-colors disabled:opacity-40 disabled:cursor-not-allowed inline-flex items-center gap-1.5"
                      >
                        {isItemLoading ? (
                          <>
                            <svg className="w-3 h-3 animate-spin" viewBox="0 0 24 24" fill="none">
                              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                              <path
                                className="opacity-75"
                                fill="currentColor"
                                d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"
                              />
                            </svg>
                            <span>Queuing...</span>
                          </>
                        ) : (
                          <span>Fill Gap</span>
                        )}
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

export { GapTable };
