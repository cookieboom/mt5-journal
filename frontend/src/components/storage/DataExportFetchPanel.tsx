import { useState, useEffect } from "react";
import { fetchBackfill, getExportUrl } from "../../lib/storageApi";

export interface DataExportFetchPanelProps {
  symbol?: string;
  timeframe?: string;
  symbolsList?: string[];
  timeframesList?: string[];
  onFetchSuccess?: () => void;
}

const DEFAULT_TIMEFRAMES = ["M1", "M5", "M15", "M30", "H1", "H4", "D1"];

export default function DataExportFetchPanel({
  symbol = "XAUUSD",
  timeframe = "M1",
  // The three this account actually trades; a fourth would offer a fetch that
  // the server has no symbol for.
  symbolsList = ["XAUUSD", "BTCUSD", "EURUSD"],
  timeframesList = DEFAULT_TIMEFRAMES,
  onFetchSuccess,
}: DataExportFetchPanelProps) {
  // --- Backfill Fetcher Form State ---
  const [fetchSymbol, setFetchSymbol] = useState<string>(symbol);
  const [fetchTf, setFetchTf] = useState<string>(timeframe);
  const [startDate, setStartDate] = useState<string>("");
  const [endDate, setEndDate] = useState<string>("");
  const [isFetching, setIsFetching] = useState<boolean>(false);
  const [fetchFeedback, setFetchFeedback] = useState<{
    type: "success" | "error";
    message: string;
  } | null>(null);

  // --- Data Exporter Form State ---
  const [exportSymbol, setExportSymbol] = useState<string>(symbol);
  const [exportTf, setExportTf] = useState<string>(timeframe);
  const [exportFormat, setExportFormat] = useState<"csv" | "json">("csv");
  const [exportStartDate, setExportStartDate] = useState<string>("");
  const [exportEndDate, setExportEndDate] = useState<string>("");

  // Sync props when symbol/timeframe props change (if user hasn't overridden)
  useEffect(() => {
    setFetchSymbol(symbol);
    setExportSymbol(symbol);
  }, [symbol]);

  useEffect(() => {
    setFetchTf(timeframe);
    setExportTf(timeframe);
  }, [timeframe]);

  // Handle MT5 Bridge Fetch Submission
  const handleFetchBackfill = async (e: React.FormEvent) => {
    e.preventDefault();
    setFetchFeedback(null);

    if (!startDate || !endDate) {
      setFetchFeedback({
        type: "error",
        message: "Ditolak: mulai dan selesai harus diisi keduanya.",
      });
      return;
    }

    const from_ms = new Date(startDate).getTime();
    const to_ms = new Date(endDate).getTime();

    if (isNaN(from_ms) || isNaN(to_ms)) {
      setFetchFeedback({
        type: "error",
        message: "Ditolak: tanggal tidak valid.",
      });
      return;
    }

    if (from_ms >= to_ms) {
      setFetchFeedback({
        type: "error",
        message: "Ditolak: mulai harus lebih awal dari selesai.",
      });
      return;
    }

    setIsFetching(true);

    try {
      const res = await fetchBackfill(fetchSymbol, fetchTf, from_ms, to_ms);
      if (!res.ok || !res.data) {
        throw new Error(res.error ?? "Gagal mengantrikan backfill");
      }

      setFetchFeedback({
        type: "success",
        message: `Backfill #${res.data.request_id} diantrikan untuk ${fetchSymbol} (${fetchTf}).`,
      });

      if (onFetchSuccess) {
        onFetchSuccess();
      }
    } catch (err: any) {
      setFetchFeedback({
        type: "error",
        message: err?.message || "Gagal memicu backfill.",
      });
    } finally {
      setIsFetching(false);
    }
  };

  // Handle Data Export Trigger
  const handleDownloadExport = () => {
    let from_ms: number | undefined = undefined;
    let to_ms: number | undefined = undefined;

    if (exportStartDate) {
      const ms = new Date(exportStartDate).getTime();
      if (!isNaN(ms)) from_ms = ms;
    }

    if (exportEndDate) {
      const ms = new Date(exportEndDate).getTime();
      if (!isNaN(ms)) to_ms = ms;
    }

    const url = getExportUrl(exportSymbol, exportTf, exportFormat, from_ms, to_ms);
    window.open(url, "_blank");
  };

  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
      {/* 1. Custom Date Range Backfill Fetcher */}
      <div className="glass p-5 space-y-4 flex flex-col justify-between">
        <div>
          <div className="flex items-center gap-2 text-ink font-bold mb-1">
            <svg className="w-5 h-5 text-cyan" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4"
              />
            </svg>
            <h3 className="text-title">Fetch rentang tanggal</h3>
          </div>
          <p className="text-body text-muted leading-relaxed mb-4">
            Antrikan backfill untuk menarik candle historis langsung dari terminal MT5.
          </p>

          {/* Feedback Banner */}
          {fetchFeedback && (
            <div
              // Green is a profitable outcome, not "it worked" (DESIGN.md § Colors).
              className={`mb-4 p-3 rounded-lg border text-body flex items-center justify-between gap-2 ${
                fetchFeedback.type === "success"
                  ? "bg-white/5 border-panel-border text-ink"
                  : "bg-neg/10 border-neg/30 text-neg"
              }`}
            >
              <span>{fetchFeedback.message}</span>
              <button
                onClick={() => setFetchFeedback(null)}
                className="text-muted hover:text-ink text-body px-1"
              >
                ✕
              </button>
            </div>
          )}

          <form onSubmit={handleFetchBackfill} className="space-y-3.5">
            {/* Symbol & Timeframe Selection */}
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label htmlFor="backfill-symbol" className="block text-meta font-medium text-muted mb-1">
                  Symbol
                </label>
                <select
                  id="backfill-symbol"
                  value={fetchSymbol}
                  onChange={(e) => setFetchSymbol(e.target.value)}
                  className="w-full glass bg-transparent px-3 py-1.5 text-body text-ink"
                >
                  {symbolsList.map((sym) => (
                    <option key={sym} value={sym} className="bg-bg">
                      {sym}
                    </option>
                  ))}
                </select>
              </div>

              <div>
                <label htmlFor="backfill-timeframe" className="block text-meta font-medium text-muted mb-1">
                  Timeframe
                </label>
                <select
                  id="backfill-timeframe"
                  value={fetchTf}
                  onChange={(e) => setFetchTf(e.target.value)}
                  className="w-full glass bg-transparent px-3 py-1.5 text-body text-ink"
                >
                  {timeframesList.map((tf) => (
                    <option key={tf} value={tf} className="bg-bg">
                      {tf}
                    </option>
                  ))}
                </select>
              </div>
            </div>

            {/* Start & End Date Inputs */}
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <div>
                <label htmlFor="backfill-start" className="block text-meta font-medium text-muted mb-1">
                  Mulai (WIB)
                </label>
                <input
                  id="backfill-start"
                  type="datetime-local"
                  value={startDate}
                  onChange={(e) => setStartDate(e.target.value)}
                  className="w-full glass bg-transparent px-2.5 py-1.5 text-body text-ink font-mono"
                />
              </div>

              <div>
                <label htmlFor="backfill-end" className="block text-meta font-medium text-muted mb-1">
                  Selesai (WIB)
                </label>
                <input
                  id="backfill-end"
                  type="datetime-local"
                  value={endDate}
                  onChange={(e) => setEndDate(e.target.value)}
                  className="w-full glass bg-transparent px-2.5 py-1.5 text-body text-ink font-mono"
                />
              </div>
            </div>

            <div className="pt-2">
              <button
                type="submit"
                disabled={isFetching}
                className="w-full py-2 px-4 rounded-lg bg-cyan/20 hover:bg-cyan/30 text-cyan ring-1 ring-cyan/40 text-body font-semibold transition-colors disabled:opacity-40 flex items-center justify-center gap-2"
              >
                {isFetching ? (
                  <>
                    <svg className="w-3.5 h-3.5 animate-spin" viewBox="0 0 24 24" fill="none">
                      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                      <path
                        className="opacity-75"
                        fill="currentColor"
                        d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"
                      />
                    </svg>
                    <span>Meminta ke bridge MT5…</span>
                  </>
                ) : (
                  <>
                    <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 10V3L4 14h7v7l9-11h-7z" />
                    </svg>
                    <span>Fetch dari bridge MT5</span>
                  </>
                )}
              </button>
            </div>
          </form>
        </div>
      </div>

      {/* 2. Data Exporter */}
      <div className="glass p-5 space-y-4 flex flex-col justify-between">
        <div>
          <div className="flex items-center gap-2 text-ink font-bold mb-1">
            <svg className="w-5 h-5 text-violet" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M12 10v6m0 0l-3-3m3 3l3-3m2 8H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"
              />
            </svg>
            <h3 className="text-title">Export candle</h3>
          </div>
          <p className="text-body text-muted leading-relaxed mb-4">
            Keluarkan candle tersimpan sebagai CSV atau JSON untuk analisis di luar journal.
          </p>

          <div className="space-y-3.5">
            {/* Symbol & Timeframe Selection */}
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label htmlFor="export-symbol" className="block text-meta font-medium text-muted mb-1">
                  Symbol
                </label>
                <select
                  id="export-symbol"
                  value={exportSymbol}
                  onChange={(e) => setExportSymbol(e.target.value)}
                  className="w-full glass bg-transparent px-3 py-1.5 text-body text-ink"
                >
                  {symbolsList.map((sym) => (
                    <option key={sym} value={sym} className="bg-bg">
                      {sym}
                    </option>
                  ))}
                </select>
              </div>

              <div>
                <label htmlFor="export-timeframe" className="block text-meta font-medium text-muted mb-1">
                  Timeframe
                </label>
                <select
                  id="export-timeframe"
                  value={exportTf}
                  onChange={(e) => setExportTf(e.target.value)}
                  className="w-full glass bg-transparent px-3 py-1.5 text-body text-ink"
                >
                  {timeframesList.map((tf) => (
                    <option key={tf} value={tf} className="bg-bg">
                      {tf}
                    </option>
                  ))}
                </select>
              </div>
            </div>

            {/* Export Format Radio Group */}
            {/* fieldset/legend rather than a floating <label>: the name belongs
                to the radio group, and a label with no control names nothing. */}
            <fieldset>
              <legend className="block text-meta font-medium text-muted mb-1.5">
                Format export
              </legend>
              <div className="grid grid-cols-2 gap-3">
                <label
                  className={`flex items-center justify-center gap-2 p-2 rounded-lg border text-body cursor-pointer transition-colors focus-within:ring-2 focus-within:ring-cyan ${
                    exportFormat === "csv"
                      ? "bg-violet/20 border-violet/50 text-violet font-semibold"
                      : "bg-bg/60 border-panel-border text-muted hover:text-ink"
                  }`}
                >
                  <input
                    type="radio"
                    name="exportFormat"
                    value="csv"
                    checked={exportFormat === "csv"}
                    onChange={() => setExportFormat("csv")}
                    className="sr-only"
                  />
                  <span>CSV (.csv)</span>
                </label>

                <label
                  className={`flex items-center justify-center gap-2 p-2 rounded-lg border text-body cursor-pointer transition-colors focus-within:ring-2 focus-within:ring-cyan ${
                    exportFormat === "json"
                      ? "bg-violet/20 border-violet/50 text-violet font-semibold"
                      : "bg-bg/60 border-panel-border text-muted hover:text-ink"
                  }`}
                >
                  <input
                    type="radio"
                    name="exportFormat"
                    value="json"
                    checked={exportFormat === "json"}
                    onChange={() => setExportFormat("json")}
                    className="sr-only"
                  />
                  <span>JSON (.json)</span>
                </label>
              </div>
            </fieldset>

            {/* Optional Date Range Filter */}
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <div>
                <label htmlFor="export-start" className="block text-meta font-medium text-muted mb-1">
                  Mulai (opsional)
                </label>
                <input
                  id="export-start"
                  type="date"
                  value={exportStartDate}
                  onChange={(e) => setExportStartDate(e.target.value)}
                  className="w-full glass bg-transparent px-2.5 py-1.5 text-body text-ink font-mono"
                />
              </div>

              <div>
                <label htmlFor="export-end" className="block text-meta font-medium text-muted mb-1">
                  Selesai (opsional)
                </label>
                <input
                  id="export-end"
                  type="date"
                  value={exportEndDate}
                  onChange={(e) => setExportEndDate(e.target.value)}
                  className="w-full glass bg-transparent px-2.5 py-1.5 text-body text-ink font-mono"
                />
              </div>
            </div>

            <div className="pt-2">
              <button
                type="button"
                onClick={handleDownloadExport}
                className="w-full py-2 px-4 rounded-lg bg-violet/20 hover:bg-violet/30 text-violet ring-1 ring-violet/40 text-body font-semibold transition-colors flex items-center justify-center gap-2"
              >
                <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    strokeWidth={2}
                    d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4"
                  />
                </svg>
                <span>Unduh {exportFormat.toUpperCase()}</span>
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

export { DataExportFetchPanel };
