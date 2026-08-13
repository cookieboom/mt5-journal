import { useState, useEffect, useCallback } from "react";
import {
  fetchStorageOverview,
  fetchCompleteness,
  type StorageOverview,
  type CandleCompleteness,
} from "../lib/storageApi";
import DiskStatsCard from "../components/storage/DiskStatsCard";
import MaintenancePanel from "../components/storage/MaintenancePanel";
import CoverageVisualizer from "../components/storage/CoverageVisualizer";
import GapTable from "../components/storage/GapTable";
import DataExportFetchPanel from "../components/storage/DataExportFetchPanel";
import PrunePanel from "../components/storage/PrunePanel";

type StorageTab = "overview" | "completeness" | "retention";

// Fallback only — the live list comes from the store. These three are what the
// account trades; a symbol the server does not have would query nothing.
const DEFAULT_SYMBOLS = ["XAUUSD", "BTCUSD", "EURUSD"];
const DEFAULT_TIMEFRAMES = ["M1", "M5", "M15", "M30", "H1", "H4", "D1"];

export default function StoragePage() {
  const [activeTab, setActiveTab] = useState<StorageTab>("overview");

  // Shared symbol & timeframe selection
  const [selectedSymbol, setSelectedSymbol] = useState<string>("XAUUSD");
  const [selectedTf, setSelectedTf] = useState<string>("M1");

  // Storage Overview state
  const [overview, setOverview] = useState<StorageOverview | null>(null);
  const [loadingOverview, setLoadingOverview] = useState<boolean>(true);
  const [overviewError, setOverviewError] = useState<string | null>(null);

  // Candle Completeness state
  const [completeness, setCompleteness] = useState<CandleCompleteness | null>(null);
  const [loadingCompleteness, setLoadingCompleteness] = useState<boolean>(false);
  const [completenessError, setCompletenessError] = useState<string | null>(null);

  // Load Overview Data
  const loadOverview = useCallback(async () => {
    setLoadingOverview(true);
    setOverviewError(null);
    try {
      const data = await fetchStorageOverview();
      setOverview(data);
      if (data.symbols && data.symbols.length > 0) {
        if (!data.symbols.includes(selectedSymbol)) {
          setSelectedSymbol(data.symbols[0]);
        }
      }
    } catch (err: any) {
      setOverviewError(err?.message || "Gagal memuat ringkasan storage.");
    } finally {
      setLoadingOverview(false);
    }
  }, [selectedSymbol]);

  // Load Completeness Data
  const loadCompleteness = useCallback(async (sym: string, tf: string) => {
    setLoadingCompleteness(true);
    setCompletenessError(null);
    try {
      const data = await fetchCompleteness(sym, tf);
      setCompleteness(data);
    } catch (err: any) {
      setCompletenessError(err?.message || `Gagal memuat kelengkapan candle ${sym}.`);
      setCompleteness(null);
    } finally {
      setLoadingCompleteness(false);
    }
  }, []);

  // Fetch initial overview on mount
  useEffect(() => {
    loadOverview();
  }, [loadOverview]);

  // Fetch completeness whenever selected symbol or timeframe changes
  useEffect(() => {
    if (selectedSymbol) {
      loadCompleteness(selectedSymbol, selectedTf);
    }
  }, [selectedSymbol, selectedTf, loadCompleteness]);

  const handleRefreshAll = () => {
    loadOverview();
    if (selectedSymbol) {
      loadCompleteness(selectedSymbol, selectedTf);
    }
  };

  const availableSymbols = overview?.symbols && overview.symbols.length > 0
    ? overview.symbols
    : DEFAULT_SYMBOLS;

  return (
    // AppShell already pads main (p-5 md:p-6); a second p-6 here made Storage
    // the one route with 48px gutters and a centred column.
    <div className="space-y-6">
      {/* Header & Page Title */}
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-headline font-bold text-ink flex items-center gap-2.5">
            <svg className="w-6 h-6 text-cyan" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M4 7v10c0 2.21 3.582 4 8 4s8-1.79 8-4V7M4 7c0 2.21 3.582 4 8 4s8-1.79 8-4M4 7c0-2.21 3.582-4 8-4s8 1.79 8 4m0 5c0 2.21-3.582 4-8 4s-8-1.79-8-4"
              />
            </svg>
            <span>Storage</span>
          </h1>
          <p className="text-body text-muted mt-1">
            Pantau storage SQLite, periksa gap coverage candle, jalankan maintenance, atur retensi data.
          </p>
        </div>

        <button
          onClick={handleRefreshAll}
          className="px-3 py-1.5 rounded-lg bg-white/5 hover:bg-white/10 ring-1 ring-panel-border text-ink text-body font-medium transition-colors flex items-center gap-1.5"
        >
          <svg className="w-3.5 h-3.5 text-cyan" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
          </svg>
          <span>Muat ulang semua</span>
        </button>
      </div>

      {/* Three tabs with icons do not fit a phone, so the strip scrolls rather
          than squashing. role="tab" + aria-selected because a styled <button>
          announces nothing about which panel it switches to. */}
      <div role="tablist" aria-label="Bagian storage"
           className="flex items-center gap-2 border-b border-panel-border overflow-x-auto">
        {([
          {
            id: "overview" as const,
            label: "Overview & maintenance",
            icon: "M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z",
          },
          {
            id: "completeness" as const,
            label: "Kelengkapan & data",
            icon: "M13 10V3L4 14h7v7l9-11h-7z",
          },
          {
            id: "retention" as const,
            label: "Retensi & prune",
            icon: "M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16",
          },
        ]).map((tab) => (
          <button
            key={tab.id}
            role="tab"
            aria-selected={activeTab === tab.id}
            onClick={() => setActiveTab(tab.id)}
            className={`pb-3 px-4 text-body font-semibold transition-colors relative flex items-center gap-2 shrink-0 ${
              activeTab === tab.id
                ? "text-violet border-b-2 border-violet"
                : "text-muted hover:text-ink"
            }`}
          >
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d={tab.icon} />
            </svg>
            <span>{tab.label}</span>
          </button>
        ))}
      </div>

      {/* Tab 1: Overview & Maintenance */}
      {activeTab === "overview" && (
        <div className="space-y-6">
          {overviewError && (
            <div className="p-4 rounded-lg bg-neg/10 border border-neg/30 text-neg text-body">
              {overviewError}
            </div>
          )}

          <DiskStatsCard overview={overview} loading={loadingOverview} />

          <div className="pt-2">
            <h2 className="text-title font-bold text-ink mb-3">Maintenance & optimalkan database</h2>
            <MaintenancePanel onSuccess={handleRefreshAll} />
          </div>
        </div>
      )}

      {/* Tab 2: Completeness & Data Center */}
      {activeTab === "completeness" && (
        <div className="space-y-6">
          {/* Controls Bar for Symbol & Timeframe selection */}
          <div className="glass p-4 flex flex-wrap items-center justify-between gap-4">
            <div className="flex flex-wrap items-center gap-4">
              <div>
                <label htmlFor="storage-symbol" className="block text-meta font-medium text-muted mb-1">Symbol</label>
                <select
                  id="storage-symbol"
                  value={selectedSymbol}
                  onChange={(e) => setSelectedSymbol(e.target.value)}
                  className="glass bg-transparent px-3 py-1.5 text-body text-ink font-semibold"
                >
                  {availableSymbols.map((sym) => (
                    <option key={sym} value={sym} className="bg-bg">
                      {sym}
                    </option>
                  ))}
                </select>
              </div>

              {/* A timeframe is the reader's own choice, so the selected member
                  takes the segmented group's violet tint-and-ring — cyan is
                  reserved for values that change with time (DESIGN.md). */}
              <fieldset>
                <legend className="block text-meta font-medium text-muted mb-1">Timeframe</legend>
                <div className="flex items-center gap-1 glass p-1 overflow-x-auto">
                  {DEFAULT_TIMEFRAMES.map((tf) => (
                    <button
                      key={tf}
                      aria-pressed={selectedTf === tf}
                      onClick={() => setSelectedTf(tf)}
                      className={`px-2.5 py-1 rounded text-body font-mono font-medium transition-colors shrink-0 ${
                        selectedTf === tf
                          ? "bg-violet/25 ring-1 ring-violet/40 text-ink font-bold"
                          : "text-muted hover:text-ink"
                      }`}
                    >
                      {tf}
                    </button>
                  ))}
                </div>
              </fieldset>
            </div>

            <button
              onClick={() => loadCompleteness(selectedSymbol, selectedTf)}
              disabled={loadingCompleteness}
              className="px-3 py-1.5 rounded-lg bg-cyan/10 hover:bg-cyan/20 ring-1 ring-cyan/30 text-cyan text-body font-medium transition-colors disabled:opacity-40 flex items-center gap-1.5"
            >
              <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
              </svg>
              <span>Muat ulang coverage</span>
            </button>
          </div>

          {completenessError && (
            <div className="p-4 rounded-lg bg-neg/10 border border-neg/30 text-neg text-body">
              {completenessError}
            </div>
          )}

          {/* 1. Coverage Visualizer */}
          <CoverageVisualizer completeness={completeness} loading={loadingCompleteness} />

          {/* 2. Detected Gaps Table */}
          <GapTable
            completeness={completeness}
            loading={loadingCompleteness}
            onRefresh={() => loadCompleteness(selectedSymbol, selectedTf)}
          />

          {/* 3. Data Export & Fetch Panel */}
          <DataExportFetchPanel
            symbol={selectedSymbol}
            timeframe={selectedTf}
            symbolsList={availableSymbols}
            timeframesList={DEFAULT_TIMEFRAMES}
            onFetchSuccess={() => loadCompleteness(selectedSymbol, selectedTf)}
          />
        </div>
      )}

      {/* Tab 3: Retention & Pruning */}
      {activeTab === "retention" && (
        <div className="space-y-6">
          <PrunePanel
            symbolsList={availableSymbols}
            onPruneSuccess={handleRefreshAll}
          />
        </div>
      )}
    </div>
  );
}
