import { useEffect, useMemo, useRef, useState, useCallback } from "react";
import { useSearchParams } from "react-router-dom";
import { useApi, postJson } from "../lib/api";
import { clampBars, parseSelection } from "../lib/chartPrefs";
import { useChartPrefs } from "../hooks/useChartPrefs";
import { mergeForming, type Sym, type Timeframe, timeframeMs } from "../lib/candles";
import type { HoverBar, LiveData } from "../lib/types";
import { clipToCursor, type TrainingSummary } from "../lib/replay";
import { useReplaySession, type ReplayConfig } from "../hooks/useReplaySession";
import { useReplayPrefs } from "../hooks/useReplayPrefs";
import type { ReplayFormPrefs } from "../lib/replayPrefs";
import { useLiveStatus } from "../hooks/useLiveStatus";
import { useLiveForming } from "../hooks/useLiveForming";
import { useLiveCommand } from "../hooks/useLiveCommand";
import SltpConfirmDialog from "../components/SltpConfirmDialog";
import ConfirmModal from "../components/ConfirmModal";
import ChartToolbar from "../components/ChartToolbar";
import LiveDot from "../components/LiveDot";
import CandleChart from "../components/CandleChart";
import CoverageRibbon from "../components/CoverageRibbon";
import ChartInfoPanel from "../components/ChartInfoPanel";
import DataHealthPanel from "../components/DataHealthPanel";
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
  const { status: liveStatus } = useLiveStatus();
  const liveCmd = useLiveCommand();
  const [sltpDialog, setSltpDialog] = useState<
    { positionId: number; kind: "sl" | "tp"; price: number; removing?: boolean } | null
  >(null);

  // --- Replay/training mode --------------------------------------------
  // Phase C isolation: this whole block only reads `settings` (for rendering)
  // and never calls `update`/`reset` from useChartPrefs. Training state lives
  // entirely in useReplaySession + local state below.
  const [replayOpen, setReplayOpen] = useState(false);
  const [configOpen, setConfigOpen] = useState(false);
  const replay = useReplaySession();
  const replayPrefs = useReplayPrefs();
  const snapshotRef = useRef<string>("");

  const [compRound, setCompRound] = useState(1);
  const [evalPause, setEvalPause] = useState<{ pnl: number; isSkip: boolean } | null>(null);
  const prevPosCount = useRef(0);

  // Clamp at consumption: drawer inputs can transiently hold an out-of-range
  // or empty (Number("")===0) value mid-typing, which must never reach the hook.
  const bars = clampBars(settings);
  // In replay, anchor the initial window at the session start cursor so a
  // far-back start date loads its real bars; live mode anchors at "now".
  const replayAnchor = replayOpen ? replay.anchorMsc ?? undefined : undefined;
  const data = useChartData(symbol, tf, bars.initialBars, bars.maxBars, replayAnchor);
  const hasBars = data.candles.length > 0;

  // Realtime forming bar — normal mode only (never in replay/training, which is
  // historical). enabled flips the watch + poll off the instant replay opens.
  const liveEnabled = !replayOpen && !configOpen;
  const { forming } = useLiveForming(symbol, tf, liveEnabled);

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
    setEvalPause(null);
    setParams(new URLSearchParams(snapshotRef.current), { replace: true }); // restore prior view
  };
  const onStart = (cfg: ReplayConfig, form: ReplayFormPrefs) => {
    setConfigOpen(false); setReplayOpen(true);
    // Point the chart at the replay symbol/tf so CandleChart fetches the right series.
    setParams(new URLSearchParams({ symbol: cfg.symbol, tf: cfg.timeframe }), { replace: true });
    replayPrefs.save(form);   // remember these specs for next time
    setCompRound(1);
    setEvalPause(null);
    prevPosCount.current = 0;
    replay.start(cfg);
  };

  const nextCompetitiveRound = useCallback((isSkip = false) => {
    const prefs = replayPrefs.prefs;
    if (!isSkip && prefs.competitiveRounds > 0 && compRound >= prefs.competitiveRounds) {
       exitReplay();
       return;
    }
    
    // Generate new random date
    const endMs = Date.now() - 14 * 24 * 3600 * 1000;
    const startMs = Date.now() - 2 * 365 * 24 * 3600 * 1000;
    const cursor = Math.floor(startMs + Math.random() * (endMs - startMs));
    const range_start_msc = cursor - timeframeMs(prefs.timeframe) * prefs.historyBars;
    
    const newCfg: ReplayConfig = {
      symbol: prefs.symbol,
      timeframe: prefs.timeframe,
      range_start_msc,
      range_end_msc: Date.now(),
      cursor_start_msc: cursor,
      speed: prefs.speed,
    };
    
    if (!isSkip) setCompRound(r => r + 1);
    
    if (isSkip) {
       setEvalPause({ pnl: 0, isSkip: true });
       setTimeout(() => {
         setEvalPause(null);
         prevPosCount.current = 0;
         replay.start(newCfg);
       }, 1000);
    } else {
       setEvalPause(null);
       prevPosCount.current = 0;
       replay.start(newCfg);
    }
  }, [replayPrefs.prefs, compRound, replay, exitReplay]);

  useEffect(() => {
    if (!replayOpen || !replayPrefs.prefs.competitiveMode) return;
    const count = replay.positions.filter((p) => p.status !== "closed").length;
    if (prevPosCount.current > 0 && count === 0 && !evalPause) {
      // all positions closed, trigger evaluation pause
      setEvalPause({ pnl: replay.sessionSummary?.total_r || 0, isSkip: false });
      setTimeout(() => {
        nextCompetitiveRound();
      }, 3000);
    }
    prevPosCount.current = count;
  }, [replay.positions, replayOpen, replayPrefs.prefs.competitiveMode, evalPause, nextCompetitiveRound, replay.sessionSummary]);

  const cursor = replay.cursorMsc;
  // Keep loaded bars ahead of the advancing reveal cursor (no-op once covered).
  useEffect(() => {
    if (replayOpen && cursor !== null) data.loadUpTo(cursor);
  }, [replayOpen, cursor, data.loadUpTo]);
  const shownCandles = replayOpen && cursor !== null
    ? clipToCursor(data.candles, cursor)
    : mergeForming(data.candles, forming);
  // Memoized: CandleChart's overlay effect re-runs on identity change of this
  // prop, so an inline map(...) here would thrash price lines every render.
  const draggableReplay = useMemo(
    () => (replayOpen
      ? replay.positions
          .filter((p) => p.status !== "closed")
          .map((p) => ({ id: p.id, direction: p.direction, entry_price: p.entry_price, sl: p.sl, tp: p.tp }))
      : undefined),
    [replayOpen, replay.positions],
  );
  const handleSlTpChange = useCallback((positionId: number, change: { sl?: number; tp?: number }) => {
    if (replayOpen) {
      replay.modifySltp(positionId, change);
      return;
    }
    const kind: "sl" | "tp" = change.sl !== undefined ? "sl" : "tp";
    const price = (change.sl ?? change.tp)!;
    setSltpDialog({ positionId, kind, price, removing: price === 0 });
  }, [replayOpen, replay]);
  const currentClose = shownCandles.length ? shownCandles[shownCandles.length - 1].c : null;
  const atEnd = !!replay.session && cursor !== null && cursor >= replay.session.range_end_msc;

  const { data: career } = useApi<TrainingSummary>("/api/training/summary", replayOpen ? 3000 : undefined);

  return (
    <div className="flex flex-col h-[calc(100vh-2rem)]">
      {configOpen && <ReplayConfigModal initial={replayPrefs.prefs} onStart={onStart} onCancel={exitReplay} />}
      <div className="flex items-center gap-3">
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
        <LiveDot status={liveStatus} />
      </div>
      {replayOpen && (
        <div className="mb-3 flex items-center justify-between">
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
          {replayPrefs.prefs.competitiveMode && (
            <div className="flex items-center gap-4 text-xs font-semibold">
               <span className="text-orange-300">
                 Skenario {compRound} {replayPrefs.prefs.competitiveRounds > 0 ? `/ ${replayPrefs.prefs.competitiveRounds}` : ''}
               </span>
               <button className="glass px-3 py-1 text-cyan hover:bg-cyan/10" onClick={() => nextCompetitiveRound(true)}>
                 Skip ⏭
               </button>
            </div>
          )}
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
              draggablePositions={draggableReplay}
              onSlTpChange={handleSlTpChange}
              lastBarMs={data.lastBarMs}
              onHover={setHovered}
              onNowVisibleChange={setNowVisible}
              onRequestOlder={data.loadOlder}
              live={live ?? null}
              nowVisible={nowVisible}
              missing={data.missing}
              shadeCoverage={!replayOpen}
              hideDate={replayOpen && replayPrefs.prefs.competitiveMode && replayPrefs.prefs.competitiveHideDate}
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

          {!replayOpen && hasBars && (() => {
            const coverageWindow: [number, number] = [
              shownCandles[0]?.time_msc ?? Date.now(),
              shownCandles[shownCandles.length - 1]?.time_msc ?? Date.now(),
            ];
            return (
              <CoverageRibbon
                bars={shownCandles}
                missing={data.missing}
                window={coverageWindow}
                tf={tf}
                onBackfill={async () => {
                  await postJson("/api/backfill", { symbol, timeframe: tf, from_ms: coverageWindow[0], to_ms: coverageWindow[1] });
                  data.retry();
                }}
              />
            );
          })()}

          {evalPause && (
            <div className="absolute inset-0 bg-black/80 flex flex-col items-center justify-center z-50 text-center">
               {evalPause.isSkip ? (
                 <h2 className="text-2xl font-bold text-muted">Mencari Skenario Baru...</h2>
               ) : (
                 <>
                   <h2 className="text-2xl font-bold mb-2">Evaluasi Skenario {compRound}</h2>
                   <div className={`text-4xl font-bold ${evalPause.pnl >= 0 ? 'text-up' : 'text-down'}`}>
                     {evalPause.pnl > 0 ? '+' : ''}{evalPause.pnl.toFixed(2)}R
                   </div>
                   <div className="text-muted mt-4">Bersiap untuk skenario berikutnya...</div>
                 </>
               )}
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
              {!replayPrefs.prefs.competitiveMode && (
                <ReplaySummary title="Kumulatif" s={career ?? null} />
              )}
              <ReplaySummary title={replayPrefs.prefs.competitiveMode ? "Sesi Ini" : "Sesi ini"} s={replay.sessionSummary} />
            </>
          ) : (
            <>
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
              <DataHealthPanel
                bars={shownCandles}
                missing={data.missing}
                window={[shownCandles[0]?.time_msc ?? Date.now(), shownCandles[shownCandles.length - 1]?.time_msc ?? Date.now()]}
                tf={tf}
                symbol={symbol}
                onBackfilled={() => data.retry()}
              />
            </>
          )}
        </aside>
      </div>
      {sltpDialog && (
        <SltpConfirmDialog
          positionId={sltpDialog.positionId}
          kind={sltpDialog.kind}
          price={sltpDialog.price}
          removing={sltpDialog.removing}
          onConfirm={(price) => {
            setSltpDialog(null);
            liveCmd.request(sltpDialog.positionId, "sltp", { [sltpDialog.kind]: price });
          }}
          onCancel={() => setSltpDialog(null)}
        />
      )}
      {liveCmd.preview && (
        <ConfirmModal
          preview={liveCmd.preview}
          submitting={liveCmd.submitting}
          error={liveCmd.error}
          onConfirm={liveCmd.confirm}
          onCancel={liveCmd.cancel}
        />
      )}
    </div>
  );
}
