import { useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useApi } from "../lib/api";
import { parseSelection, loadChartSettings, saveChartSettings, type ChartSettings } from "../lib/chartPrefs";
import type { Sym, Timeframe } from "../lib/candles";
import type { HoverBar, LiveData } from "../lib/types";
import ChartToolbar from "../components/ChartToolbar";
import CandleChart from "../components/CandleChart";
import { useChartData } from "../hooks/useChartData";

export interface ChartHandle { jumpToNow: () => void }

export default function Chart() {
  const [params, setParams] = useSearchParams();
  const { symbol, tf } = parseSelection(params);
  const [settings, setSettings] = useState<ChartSettings>(() => loadChartSettings());
  const [hovered, setHovered] = useState<HoverBar | null>(null);
  const [nowVisible, setNowVisible] = useState(false);
  // hovered is consumed by ChartInfoPanel in Task 8.
  void hovered; // TODO(Task 8): consumed by ChartInfoPanel
  // nowVisible is consumed by the live overlay in Task 7.
  void nowVisible; // TODO(Task 7): consumed by live overlay
  const chartRef = useRef<ChartHandle>(null);

  const { data: live } = useApi<LiveData>("/api/live", 2500);
  const currency = live?.header.currency ?? "USC";
  void currency; // TODO(Task 8): consumed by ChartInfoPanel

  const data = useChartData(symbol, tf);
  const hasBars = data.candles.length > 0;

  const setSelection = (next: { symbol?: Sym; tf?: Timeframe }) => {
    const p = new URLSearchParams(params);
    p.set("symbol", next.symbol ?? symbol);
    p.set("tf", next.tf ?? tf);
    setParams(p, { replace: true });
  };
  const applySettings = (s: ChartSettings) => { setSettings(s); saveChartSettings(s); };

  return (
    <div className="flex flex-col h-[calc(100vh-2rem)]">
      <ChartToolbar
        symbol={symbol}
        tf={tf}
        settings={settings}
        onSymbol={(s) => setSelection({ symbol: s })}
        onTf={(t) => setSelection({ tf: t })}
        onSettings={applySettings}
        onJumpNow={() => chartRef.current?.jumpToNow()}
      />
      <div className="flex gap-3 flex-1 min-h-0">
        <div className="relative flex-1 min-h-0">
          {hasBars ? (
            <CandleChart
              ref={chartRef}
              symbol={symbol}
              tf={tf}
              settings={settings}
              candles={data.candles}
              lastBarMs={data.lastBarMs}
              onHover={setHovered}
              onNowVisibleChange={setNowVisible}
              onRequestOlder={data.loadOlder}
            />
          ) : (
            <div className="glass h-full flex items-center justify-center text-muted text-sm">
              {data.status === "loading" || data.status === "polling" ? (
                <span>⌛ Memuat data {symbol} {tf}…</span>
              ) : data.status === "gaveup" ? (
                <div className="text-center">
                  <div>Belum ada data ter-cache untuk rentang ini.</div>
                  <div className="mt-1">Jalankan <code>journal live</code> untuk mengisi cache.</div>
                  <button onClick={data.retry} className="glass mt-2 px-3 py-1 text-cyan">Coba lagi</button>
                </div>
              ) : (
                <span className="text-neg">Gagal memuat: {data.error}</span>
              )}
            </div>
          )}

          {/* Non-blocking banners while bars are already shown */}
          {hasBars && (data.status === "loading" || data.status === "polling") && (
            <div className="glass absolute top-2 left-2 px-2 py-1 text-[11px] text-muted">⌛ memuat data…</div>
          )}
          {hasBars && data.status === "gaveup" && (
            <div className="glass absolute top-2 left-2 px-2 py-1 text-[11px] text-muted flex items-center gap-2">
              <span>Data belum lengkap — jalankan <code>journal live</code>.</span>
              <button onClick={data.retry} className="text-cyan">Coba lagi</button>
            </div>
          )}
          {hasBars && data.status === "error" && (
            <div className="glass absolute top-2 left-2 px-2 py-1 text-[11px] text-neg flex items-center gap-2">
              <span>Gagal memuat: {data.error}</span>
              <button onClick={data.retry} className="text-cyan">Coba lagi</button>
            </div>
          )}
        </div>
        {/* ChartInfoPanel (Task 8) mounts here */}
        <aside className="glass w-[240px] shrink-0 p-3 hidden lg:block">
          <div className="text-muted text-[12px]">info panel</div>
        </aside>
      </div>
    </div>
  );
}
