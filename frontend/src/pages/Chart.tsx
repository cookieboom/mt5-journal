import { useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useApi } from "../lib/api";
import { parseSelection, loadChartSettings, saveChartSettings, type ChartSettings } from "../lib/chartPrefs";
import type { Sym, Timeframe } from "../lib/candles";
import type { HoverBar, LiveData } from "../lib/types";
import ChartToolbar from "../components/ChartToolbar";

export interface ChartHandle { jumpToNow: () => void }

export default function Chart() {
  const [params, setParams] = useSearchParams();
  const { symbol, tf } = parseSelection(params);
  const [settings, setSettings] = useState<ChartSettings>(() => loadChartSettings());
  const [hovered, setHovered] = useState<HoverBar | null>(null);
  const [, setNowVisible] = useState(false);
  // setHovered / setNowVisible are wired to CandleChart's onHover /
  // onNowVisibleChange props in Task 6 — no consumer exists yet in this shell.
  void setHovered;
  void setNowVisible;
  const chartRef = useRef<ChartHandle>(null);

  const { data: live } = useApi<LiveData>("/api/live", 2500);
  const currency = live?.header.currency ?? "USC";

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
        {/* CandleChart (Task 6) mounts here */}
        <div className="glass flex-1 min-h-0 flex items-center justify-center text-muted text-sm">
          chart — {symbol} {tf}{hovered ? ` · ${hovered.c}` : ""}
        </div>
        {/* ChartInfoPanel (Task 8) mounts here */}
        <aside className="glass w-[240px] shrink-0 p-3 hidden lg:block">
          <div className="text-muted text-[12px]">info panel · {currency}</div>
        </aside>
      </div>
    </div>
  );
}
