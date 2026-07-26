import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useApi } from "../lib/api";
import { clampBars, parseSelection } from "../lib/chartPrefs";
import { useChartPrefs } from "../hooks/useChartPrefs";
import type { Sym, Timeframe } from "../lib/candles";
import type { HoverBar, LiveData } from "../lib/types";
import { clipToCursor, replayLines, type TrainingSummary } from "../lib/replay";
import { useReplaySession, type ReplayConfig } from "../hooks/useReplaySession";
import { useReplayPrefs } from "../hooks/useReplayPrefs";
import type { ReplayFormPrefs } from "../lib/replayPrefs";
import ChartToolbar from "../components/ChartToolbar";
import CandleChart from "../components/CandleChart";
import ChartInfoPanel from "../components/ChartInfoPanel";
import ReplayConfigModal from "../components/ReplayConfigModal";
import ReplayControls from "../components/ReplayControls";
import ReplayOrderTicket from "../components/ReplayOrderTicket";
import ReplayPositions from "../components/ReplayPositions";
import ReplaySummary from "../components/ReplaySummary";
import { useChartData } from "../hooks/useChartData";

export interface ChartHandle { jumpToNow: () => void }

export default function Chart() {
  const [params, setParams] = useSearchParams();
  const { settings, update, reset } = useChartPrefs();
  const { symbol, tf } = parseSelection(params, {
    symbol: settings.defaultSymbol, tf: settings.defaultTimeframe,
  });
  const [hovered, setHovered] = useState<HoverBar | null>(null);
  const [nowVisible, setNowVisible] = useState(false);
  const chartRef = useRef<ChartHandle>(null);

  const { data: live } = useApi<LiveData>("/api/live", 2500);
  const currency = live?.header.currency ?? "USC";

  // --- Replay/training mode --------------------------------------------
  // Phase C isolation: this whole block only reads `settings` (for rendering)
  // and never calls `update`/`reset` from useChartPrefs. Training state lives
  // entirely in useReplaySession + local state below.
  const [replayOpen, setReplayOpen] = useState(false);
  const [configOpen, setConfigOpen] = useState(false);
  const replay = useReplaySession();
  const replayPrefs = useReplayPrefs();
  const snapshotRef = useRef<string>("");

  // Clamp at consumption: drawer inputs can transiently hold an out-of-range
  // or empty (Number("")===0) value mid-typing, which must never reach the hook.
  const bars = clampBars(settings);
  // In replay, anchor the initial window at the session start cursor so a
  // far-back start date loads its real bars; live mode anchors at "now".
  const replayAnchor = replayOpen ? replay.anchorMsc ?? undefined : undefined;
  const data = useChartData(symbol, tf, bars.initialBars, bars.maxBars, replayAnchor);
  const hasBars = data.candles.length > 0;

  const setSelection = (next: { symbol?: Sym; tf?: Timeframe }) => {
    const p = new URLSearchParams(params);
    p.set("symbol", next.symbol ?? symbol);
    p.set("tf", next.tf ?? tf);
    setParams(p, { replace: true });
  };

  const enterReplay = () => {
    if (replayOpen || configOpen) return; // already in / entering replay — never re-snapshot
    snapshotRef.current = params.toString();
    setConfigOpen(true);
  };
  const exitReplay = async () => {
    await replay.discard();
    setReplayOpen(false); setConfigOpen(false);
    setParams(new URLSearchParams(snapshotRef.current), { replace: true }); // restore prior view
  };
  const onStart = (cfg: ReplayConfig, form: ReplayFormPrefs) => {
    setConfigOpen(false); setReplayOpen(true);
    // Point the chart at the replay symbol/tf so CandleChart fetches the right series.
    setParams(new URLSearchParams({ symbol: cfg.symbol, tf: cfg.timeframe }), { replace: true });
    replayPrefs.save(form);   // remember these specs for next time
    replay.start(cfg);
  };

  const cursor = replay.cursorMsc;
  // Keep loaded bars ahead of the advancing reveal cursor (no-op once covered).
  useEffect(() => {
    if (replayOpen && cursor !== null) data.loadUpTo(cursor);
  }, [replayOpen, cursor, data.loadUpTo]);
  const shownCandles = replayOpen && cursor !== null
    ? clipToCursor(data.candles, cursor)
    : data.candles;
  // Memoized: CandleChart's overlay effect re-runs on identity change of this
  // prop, so an inline replayLines(...) here would thrash price lines every render.
  const overlay = useMemo(
    () => (replayOpen ? replayLines(replay.positions) : undefined),
    [replayOpen, replay.positions],
  );
  const currentClose = shownCandles.length ? shownCandles[shownCandles.length - 1].c : null;
  const atEnd = !!replay.session && cursor !== null && cursor >= replay.session.range_end_msc;

  const { data: career } = useApi<TrainingSummary>("/api/training/summary", replayOpen ? 3000 : undefined);

  return (
    <div className="flex flex-col h-[calc(100vh-2rem)]">
      {configOpen && <ReplayConfigModal initial={replayPrefs.prefs} onStart={onStart} onCancel={exitReplay} />}
      <ChartToolbar
        symbol={symbol}
        tf={tf}
        settings={settings}
        onSymbol={(s) => setSelection({ symbol: s })}
        onTf={(t) => setSelection({ tf: t })}
        onSettings={update}
        onReset={reset}
        onJumpNow={() => chartRef.current?.jumpToNow()}
        onReplay={enterReplay}
        replayActive={replayOpen}
      />
      {replayOpen && (
        <div className="mb-3">
          <ReplayControls
            cursorMsc={replay.cursorMsc}
            playing={replay.playing}
            atEnd={atEnd}
            onStep={() => replay.step(1)}
            onPlayPause={() => (replay.playing ? replay.pause() : replay.play())}
            onJump={replay.jump}
            onReset={replay.reset}
            onExit={exitReplay}
          />
        </div>
      )}
      <div className="flex gap-3 flex-1 min-h-0">
        <div className="relative flex-1 min-h-0">
          {hasBars ? (
            <CandleChart
              ref={chartRef}
              symbol={symbol}
              tf={tf}
              settings={settings}
              candles={shownCandles}
              overlayLines={overlay}
              lastBarMs={data.lastBarMs}
              onHover={setHovered}
              onNowVisibleChange={setNowVisible}
              onRequestOlder={data.loadOlder}
              live={live ?? null}
              nowVisible={nowVisible}
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
        <aside className="w-[240px] shrink-0 hidden lg:flex lg:flex-col gap-3 overflow-y-auto">
          {replayOpen ? (
            <>
              <ReplayOrderTicket disabled={!replay.session || atEnd} onSubmit={replay.open} />
              <ReplayPositions
                positions={replay.positions}
                currentClose={currentClose}
                currency={currency}
                onClose={replay.close}
              />
              <ReplaySummary title="Sesi ini" s={replay.sessionSummary} />
              <ReplaySummary title="Kumulatif" s={career ?? null} />
            </>
          ) : (
            <div className="glass w-full p-3">
              <ChartInfoPanel
                symbol={symbol}
                tf={tf}
                candles={data.candles}
                hovered={hovered}
                live={live ?? null}
                currency={currency}
                chartType={settings.chartType}
              />
            </div>
          )}
        </aside>
      </div>
    </div>
  );
}
