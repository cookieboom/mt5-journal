import { useEffect, useMemo, useRef, useState, useCallback } from "react";
import { useSearchParams } from "react-router-dom";
import { useApi, postJson } from "../lib/api";
import { clampBars, parseSelection } from "../lib/chartPrefs";
import { useChartPrefs } from "../hooks/useChartPrefs";
import { mergeForming, staleEntryReason, type Sym, type Timeframe, timeframeMs } from "../lib/candles";
import type { HoverBar, LiveData } from "../lib/types";
import { clipToCursor, outcomeCounts, type TrainingSummary } from "../lib/replay";
import { useReplaySession, type ReplayConfig } from "../hooks/useReplaySession";
import { useReplayPrefs } from "../hooks/useReplayPrefs";
import { useCompetitiveReplay } from "../hooks/useCompetitiveReplay";
import type { ReplayFormPrefs } from "../lib/replayPrefs";
import { useLiveStatus } from "../hooks/useLiveStatus";
import { useLiveForming } from "../hooks/useLiveForming";
import { useLiveCommand } from "../hooks/useLiveCommand";
import { useRiskSizing } from "../hooks/useRiskSizing";
import { usePaperAccount } from "../hooks/usePaperAccount";
import { usePaperPrefs } from "../hooks/usePaperPrefs";
import { listAccounts, modifySltp } from "../lib/paperApi";
import type { PaperAccount } from "../lib/types";
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
import ReplayPositions from "../components/ReplayPositions";
import ReplaySummary from "../components/ReplaySummary";
import RiskSizePanel from "../components/RiskSizePanel";
import PaperAccountDialog from "../components/PaperAccountDialog";
import PaperSidePanel from "../components/PaperSidePanel";
import ScenarioEvalOverlay from "../components/ScenarioEvalOverlay";
import { ChartLoadBanner, ChartPlaceholder } from "../components/ChartLoadState";
import Sheet from "../components/Sheet";
import { useChartData } from "../hooks/useChartData";
import { useDrawings } from "../hooks/useDrawings";
import { PLANNED_ID } from "../lib/sltpDrag";
import IndicatorPanel from "../components/IndicatorPanel";
import { useIndicators } from "../hooks/useIndicators";
import { useIndicatorLayout } from "../hooks/useIndicatorLayout";
import { indicatorsApi, signalMarkers, type ScriptInfo } from "../lib/indicators";
import StrategyTester from "../components/StrategyTester";
import { useBacktest } from "../hooks/useBacktest";
import { backtestApi, hasSignal, tradeMarkers } from "../lib/backtest";
import type { TrainingSession } from "../lib/replay";

export interface ChartHandle {
  jumpToNow: () => void;
  // Epoch ms -> pixel x on the chart's own time scale, or null when the chart
  // isn't mounted yet or the time falls outside the current coordinate space.
  // Lets an external sibling overlay (Lab's RegimeOverlay/probability strip)
  // project by TIME the same way CandleChart's own internal overlays do,
  // without a second chart instance.
  timeToX: (timeMsc: number) => number | null;
}

export default function Chart() {
  const [params, setParams] = useSearchParams();
  // Below lg the side column is a sheet instead of a third of the screen.
  const [panelOpen, setPanelOpen] = useState(false);
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

  // Replay drawings live under their own session key: a live annotation was
  // made knowing what happened next, so showing it during training would leak
  // the answer. Passing null outside replay selects the per-symbol live key.
  //
  // `replay.start(cfg)` is an async POST — `setReplayOpen(true)` above lands
  // synchronously, well before `replay.session` is assigned from the
  // response. For that whole window `replayOpen` is true but `replay.session`
  // is still null, so a naive `replay.session?.id ?? null` falls back to the
  // LIVE per-symbol key — showing live drawings, editable, on the replay
  // chart, with an edit in that window persisting to the live key. Gate the
  // hook itself on `drawingsReady` so it neither reads nor writes ANY key
  // until the real session key is known.
  const drawingSession = replayOpen ? replay.session?.id ?? null : null;
  const drawingsReady = !replayOpen || replay.session != null;
  const drawings = useDrawings(symbol, drawingSession, drawingsReady);

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
  const { forming, live: feedLive, formingUpdatedMs } = useLiveForming(symbol, tf, liveEnabled);

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
    comp.reset();
    setParams(new URLSearchParams(snapshotRef.current), { replace: true }); // restore prior view
  };
  const onStart = (cfg: ReplayConfig, form: ReplayFormPrefs) => {
    setConfigOpen(false); setReplayOpen(true);
    // Point the chart at the replay symbol/tf so CandleChart fetches the right series.
    setParams(new URLSearchParams({ symbol: cfg.symbol, tf: cfg.timeframe }), { replace: true });
    replayPrefs.save(form);   // remember these specs for next time
    comp.reset();
    replay.start(cfg);
  };

  const comp = useCompetitiveReplay(replay, replayPrefs.prefs, replayOpen, exitReplay);

  const cursor = replay.cursorMsc;
  // Keep loaded bars ahead of the advancing reveal cursor (no-op once covered).
  useEffect(() => {
    if (replayOpen && cursor !== null) data.loadUpTo(cursor);
  }, [replayOpen, cursor, data.loadUpTo]);

  // Live mode: useChartData's own poll loop stops once its initial window is
  // fully covered (status "ready") — nothing else ever asks the backend for
  // bars that close afterward. mergeForming below can only bridge a SINGLE
  // bar against data.candles, so once the forming bar (polled forever by
  // useLiveForming) rolls over more than once since page load, the bar(s) in
  // between are lost and a gap opens between the frozen historical tail and
  // the live bar. Mirror replay's cursor-follow above: advance the loaded
  // window to the forming bar's time so newly closed bars get pulled in.
  // Keyed on `forming`'s IDENTITY (a fresh object every 5s poll), NOT on
  // forming.time_msc: on time_msc this fires exactly once per bar, so the single
  // attempt landing before `journal live` promoted the previous bar makes no
  // progress (loadUpTo advances toRef only over confirmed coverage) and nothing
  // retries for the rest of the interval — a whole minute on M1 with the history
  // a bar short and the live bar hidden, since mergeForming refuses a
  // two-interval jump. loadUpTo is a cheap no-op once the target is covered, so
  // re-running it per poll costs nothing and caps that hole at one poll.
  useEffect(() => {
    if (!replayOpen && forming) data.loadUpTo(forming.time_msc);
  }, [replayOpen, forming, data.loadUpTo]);
  // Memoized: CandleChart's data-push effect (and the overlay effect below)
  // re-run on identity change of `candles`/`draggablePositions`. An inline
  // recompute here handed CandleChart a new array on every unrelated Chart.tsx
  // render (hover, nowVisible, ...) — series.setData() can itself shift the
  // visible range, which calls back up through onNowVisibleChange/
  // onRequestOlder into more Chart.tsx state, re-triggering this same render.
  // Once data.candles actually started changing over time (live tail-follow
  // below) that became a tight synchronous cascade — "Maximum update depth
  // exceeded". Memoizing on the real inputs breaks the cascade.
  const shownCandles = useMemo(
    () => (replayOpen && cursor !== null
      ? clipToCursor(data.candles, cursor)
      : mergeForming(data.candles, forming, timeframeMs(tf))),
    [replayOpen, cursor, data.candles, forming, tf],
  );
  // ------------------------------------------------------------ indicators
  // One layout for live and replay. In replay nothing is computed until the
  // session exists: its id is what makes the server clip to the cursor.
  const indLayout = useIndicatorLayout();
  const [scripts, setScripts] = useState<ScriptInfo[]>([]);
  const [scriptsRev, setScriptsRev] = useState(0);
  useEffect(() => {
    void indicatorsApi.scripts().then((r) => { if (r.ok && Array.isArray(r.data?.scripts)) setScripts(r.data.scripts); });
  }, [scriptsRev]);
  const indicatorsReady = !replayOpen || replay.session != null;
  const ind = useIndicators({
    symbol, tf,
    items: indicatorsReady ? indLayout.layout.items : [],
    bars: shownCandles,
    sessionId: replayOpen ? replay.session?.id ?? null : null,
    live: !replayOpen,
    rev: scriptsRev,
  });
  // ------------------------------------------------------------ strategy tester
  // Live only: a backtest over a replay session's range would read past the
  // cursor. Rule 9: live signal markers exist ONLY beside a tester result for
  // the same script/symbol/TF, whose OOS headline the strip always shows.
  const testerCandidates = useMemo(() => {
    const byId = new Map(scripts.map((x) => [x.id, x]));
    return indLayout.layout.items
      .filter((i) => hasSignal(byId.get(i.script)?.source ?? ""))
      .map((i) => ({ id: i.id, name: byId.get(i.script)?.name ?? i.script }));
  }, [indLayout.layout.items, scripts]);
  const [testerPick, setTesterPick] = useState<string | null>(null);
  const testerId = testerCandidates.find((c) => c.id === testerPick)?.id ?? testerCandidates[0]?.id ?? null;
  const testerItem = indLayout.layout.items.find((i) => i.id === testerId) ?? null;
  const lastClosedMs = data.candles.length ? data.candles[data.candles.length - 1].time_msc : null;
  const tester = useBacktest({
    symbol, tf, item: replayOpen ? null : testerItem, lastClosedMs, rev: scriptsRev,
  });
  const [focus, setFocus] = useState<{ startMs: number; endMs: number } | null>(null);
  useEffect(() => { setFocus(null); }, [symbol, tf, replayOpen]);
  // A trade older than the loaded window: pull older bars until it is in.
  // ponytail: stops silently at maxBars (useChartData's cap) — raise it in settings.
  useEffect(() => {
    const first = data.candles[0]?.time_msc;
    if (focus && first !== undefined && first > focus.startMs) void data.loadOlder();
  }, [focus, data.candles, data.loadOlder]);
  // Study session around one tester trade, or one period (its start to its end).
  const onTesterReplay = async (decisionMsc: number, exitMsc: number | null) => {
    const r = await backtestApi.replay({
      symbol, timeframe: tf, decision_msc: decisionMsc, exit_msc: exitMsc,
    });
    if (!r.ok || !r.data) return;
    snapshotRef.current = params.toString();
    const session = r.data.session as TrainingSession;
    setReplayOpen(true);
    setParams(new URLSearchParams({ symbol: session.symbol, tf: session.timeframe }), { replace: true });
    comp.reset();
    replay.adopt(session, replayPrefs.prefs.speed);
  };
  const testerStrip = !replayOpen && testerId !== null && (
    <StrategyTester
      candidates={testerCandidates}
      activeId={testerId}
      onActive={setTesterPick}
      settings={tester.settings}
      onSettings={(b) => indLayout.setLayout({
        ...indLayout.layout,
        items: indLayout.layout.items.map((i) => (i.id === testerId ? { ...i, backtest: b } : i)),
      })}
      result={tester.result}
      error={tester.error}
      loading={tester.loading}
      armed={tester.armed}
      onRun={tester.run}
      onTradeClick={(t) => setFocus({ startMs: t.entry_msc, endMs: t.exit_msc ?? t.entry_msc })}
      onReplay={(t) => void onTesterReplay(t.decision_msc, t.exit_msc)}
      onPeriodReplay={(p) => void onTesterReplay(p.key, p.end_msc)}
      context={testerItem ? {
        script: testerItem.script, inputs: testerItem.inputs, symbol, tf, rev: scriptsRev,
      } : null}
      nowMs={Date.now()}
    />
  );

  // Replay: §1's behind-the-cursor signal markers. Live: the tester's simulated
  // trades, and only while it holds a result (rule 9).
  const indicatorMarkers = useMemo(
    () => (replayOpen ? signalMarkers(ind.renders.map((r) => r.result.signals))
      : tester.result && tester.settings.showOnChart ? tradeMarkers(tester.result.trades)
      : undefined),
    [replayOpen, ind.renders, tester.result, tester.settings.showOnChart],
  );
  const indicatorPanel = (
    <IndicatorPanel
      layout={indLayout.layout}
      onLayout={indLayout.setLayout}
      scripts={scripts}
      onScriptSaved={() => setScriptsRev((x) => x + 1)}
      results={ind.results}
      errors={ind.errors}
      hoverMs={hovered?.time_msc ?? null}
      firstShownMs={shownCandles[0]?.time_msc ?? null}
    />
  );

  // ------------------------------------------------------------ paper mode
  // A virtual account with its own balance. Replay owns the chart when it is
  // open, so paper only applies outside it — two simulated accounts fed by
  // different clocks on one chart is a trap, not a feature.
  const paperPrefs = usePaperPrefs();
  const paperMode = paperPrefs.prefs.mode === "paper" && !replayOpen;
  const paperAccountId = paperPrefs.prefs.accountId;
  const paper = usePaperAccount(paperMode ? paperAccountId : null);
  const [accountsOpen, setAccountsOpen] = useState(false);
  const [accounts, setAccounts] = useState<PaperAccount[]>([]);
  const loadAccounts = useCallback(() => {
    listAccounts().then(setAccounts).catch(() => { /* offline / dev */ });
  }, []);
  useEffect(() => { if (paperMode) loadAccounts(); }, [paperMode, loadAccounts]);

  const draggablePaper = useMemo(
    () => (paperMode
      ? (paper.view?.open ?? []).map((p) => ({
          id: p.id, direction: p.direction, entry_price: p.entry_price,
          sl: p.sl, tp: p.tp,
        }))
      : undefined),
    [paperMode, paper.view],
  );

  const draggableReplay = useMemo(
    () => (replayOpen
      ? replay.positions
          .filter((p) => p.status !== "closed")
          .map((p) => ({ id: p.id, direction: p.direction, entry_price: p.entry_price, sl: p.sl, tp: p.tp }))
      : undefined),
    [replayOpen, replay.positions],
  );
  const handleSlTpChange = useCallback((positionId: number, change: { sl?: number; tp?: number }) => {
    // A planned order is not a position: its "commit" is local state, and the
    // real command only leaves on the button. Instant, no dialog — the human is
    // still choosing.
    if (positionId === PLANNED_ID) {
      if (change.sl !== undefined) setPlannedSl(change.sl === 0 ? null : change.sl);
      if (change.tp !== undefined) setPlannedTp(change.tp === 0 ? null : change.tp);
      return;
    }
    if (replayOpen) {
      replay.modifySltp(positionId, change);
      return;
    }
    // A paper position is a position: the drag commits straight away, with no
    // ConfirmModal, because nothing reaches the broker.
    if (paperMode) {
      void modifySltp(positionId, change).then(() => paper.refresh());
      return;
    }
    const kind: "sl" | "tp" = change.sl !== undefined ? "sl" : "tp";
    const price = (change.sl ?? change.tp)!;
    setSltpDialog({ positionId, kind, price, removing: Math.abs(price) < 1e-9 });
  }, [replayOpen, replay, paperMode, paper]);
  const currentClose = shownCandles.length ? shownCandles[shownCandles.length - 1].c : null;
  const atEnd = !!replay.session && cursor !== null && cursor >= replay.session.range_end_msc;

  // The order being sized. `entry` is whatever price the chart is showing now:
  // the forming bar's close in live, the cursor bar's close in replay. Both are
  // already computed for the info panel — this reuses them rather than adding a
  // second notion of "current price".
  const [plannedSl, setPlannedSl] = useState<number | null>(null);
  const [plannedTp, setPlannedTp] = useState<number | null>(null);
  const plannedEntry = currentClose ?? null;
  // Live only. Recomputed on every 5s poll, which is what makes the clock read
  // here honest: a dead web server freezes this value, but it also makes the
  // open request itself unsendable, so nothing gets through on a frozen gate.
  // `priceRef` is `plannedEntry` on purpose — the exact number POST /api/live/open
  // will be sized from — compared against the poll's own close. Equal in the
  // normal case (the shown bar IS the forming bar); they only part when
  // `mergeForming` refused to append, which is the stalled-fetch case the
  // server's guard names and the browser used to sail straight through.
  const entryBlocked = replayOpen
    ? null
    : staleEntryReason({
        feedLive,
        entryBarMs: shownCandles.length ? shownCandles[shownCandles.length - 1].time_msc : null,
        intervalMs: timeframeMs(tf),
        nowMs: Date.now(),
        formingUpdatedMs,
        formingClose: forming?.c ?? null,
        priceRef: plannedEntry,
        sl: plannedSl,
      });
  const sizing = useRiskSizing({
    symbol, entry: plannedEntry, sl: plannedSl, tp: plannedTp,
  });
  // Memoized for the same reason as shownCandles/draggableReplay above: this
  // object is a CandleChart prop AND a dependency of its price-line effect,
  // which starts by wiping every line. A fresh object each render (every
  // hover, every unrelated Chart.tsx state change) would destroy and
  // recreate all entry/SL/TP lines on every mousemove.
  const plannedDirection = sizing.result?.direction ?? null;
  const plannedOrder = useMemo(
    () => (plannedEntry === null ? null : {
      entry: plannedEntry, sl: plannedSl, tp: plannedTp,
      direction: plannedDirection,
    }),
    [plannedEntry, plannedSl, plannedTp, plannedDirection],
  );
  const drawingsProp = useMemo(() => ({
    items: drawings.items,
    // Not editable while the replay-session key is still pending (see
    // drawingsReady above): the palette itself must not render, or a draw
    // made in that window would target the live key underneath it.
    editable: drawingsReady,
    onAdd: drawings.add,
    onUpdate: drawings.update,
    onDelete: drawings.remove,
    onClearAll: drawings.clear,
  }), [drawings.items, drawings.add, drawings.update, drawings.remove, drawings.clear, drawingsReady]);

  const competitive = replayPrefs.prefs.competitiveMode;
  const [includeStudy, setIncludeStudy] = useState(false);
  const { data: career } = useApi<TrainingSummary>(
    `/api/training/summary${includeStudy ? "?include_study=true" : ""}`,
    replayOpen && !competitive ? 3000 : undefined,
  );
  const replayCounts = useMemo(() => outcomeCounts(replay.positions), [replay.positions]);
  // Competitive: stats span the whole run (finished scenarios + current one).
  const sessionCounts = competitive ? comp.counts : replayCounts;
  const sessionSummary = competitive ? comp.summary : replay.sessionSummary;

  // One definition, two containers: the lg column and the sheet below it. Only
  // one of them renders it at a time — RiskSizePanel must not exist twice.
  const paperPanel = (
    <PaperSidePanel
      paper={paper}
      symbol={symbol}
      lastPrice={currentClose}
      live={liveStatus?.live ?? false}
      onAccounts={() => { loadAccounts(); setAccountsOpen(true); }}
    />
  );

  const sidePanel = paperMode ? <>{paperPanel}{indicatorPanel}</> : replayOpen ? (
    <>
      <RiskSizePanel
        disabled={!replay.session || atEnd}
        currency={currency}
        prefs={sizing.prefs}
        onPrefsChange={sizing.setPrefs}
        entry={plannedEntry}
        sl={plannedSl}
        tp={plannedTp}
        onSlChange={setPlannedSl}
        onTpChange={setPlannedTp}
        result={sizing.result}
        loading={sizing.loading}
        onSubmit={(o) => replay.open({
          direction: o.direction, volume: o.volume,
          sl: plannedSl ?? 0, tp: plannedTp ?? 0,
        })}
      />
      <ReplayPositions
        positions={replay.positions}
        currentClose={currentClose}
        currency={currency}
        onClose={replay.close}
      />
      {/* Career card is hidden in competitive mode — the run total below
          is the score that matters there. */}
      {!competitive && <ReplaySummary title="Kumulatif" s={career ?? null} />}
      {!competitive && (
        <label className="flex items-center gap-2 px-1 text-meta text-muted">
          <input type="checkbox" checked={includeStudy} onChange={(e) => setIncludeStudy(e.target.checked)} />
          sertakan sesi studi
        </label>
      )}
      <ReplaySummary
        title={competitive ? `Kompetitif · Skenario ${comp.round}` : "Sesi ini"}
        s={sessionSummary}
        counts={sessionCounts}
      />
      {indicatorPanel}
    </>
  ) : (
    <>
      <RiskSizePanel
        disabled={!live} // load gate only — live.live.empty is never undefined
        blocked={entryBlocked}
        currency={currency}
        prefs={sizing.prefs}
        onPrefsChange={sizing.setPrefs}
        entry={plannedEntry}
        sl={plannedSl}
        tp={plannedTp}
        onSlChange={setPlannedSl}
        onTpChange={setPlannedTp}
        result={sizing.result}
        loading={sizing.loading}
        onSubmit={() => liveCmd.request(null, "open", {
          symbol, entry: plannedEntry, sl: plannedSl, tp: plannedTp,
          risk_mode: sizing.prefs.mode, risk_value: sizing.prefs.value,
        })}
      />
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
      {indicatorPanel}
      <DataHealthPanel
        bars={shownCandles}
        missing={data.missing}
        window={[shownCandles[0]?.time_msc ?? Date.now(), shownCandles[shownCandles.length - 1]?.time_msc ?? Date.now()]}
        tf={tf}
        symbol={symbol}
        onBackfilled={() => data.retry()}
      />
    </>
  );

  // Below md the shell reserves 76px for the nav bar; the chart column has to
  // subtract it too or the canvas runs underneath it. The extra 2rem is for
  // CoverageRibbon, which renders after the chart inside the h-full pane and so
  // has always hung below the container — harmless until the nav bar sat there.
  return (
    <div className="flex flex-col h-[calc(100vh-8rem)] md:h-[calc(100vh-2rem)]">
      {/* The other ten routes carry a visible 18px title; this one gives that
          line to the toolbar, and on a phone a title row would cost the chart
          30px it does not have. The heading still has to exist — hidden, so a
          screen reader gets the route name the nav highlight gives everyone
          else. */}
      <h1 className="sr-only">Chart</h1>
      {configOpen && <ReplayConfigModal initial={replayPrefs.prefs} onStart={onStart} onCancel={exitReplay} />}
      <div className="flex flex-wrap items-center gap-3">
        <ChartToolbar
          symbol={symbol}
          tf={tf}
          settings={settings}
          onSymbol={(s) => setSelection({ symbol: s })}
          onTf={(t) => setSelection({ tf: t })}
          onSettings={update}
          onReset={reset}
          onJumpNow={() => { setFocus(null); chartRef.current?.jumpToNow(); }}
          onReplay={enterReplay}
          replayActive={replayOpen}
          paperMode={paperMode}
          onPaperMode={(on) => paperPrefs.update({ mode: on ? "paper" : "real" })}
        />
        <LiveDot status={liveStatus} />
        <button
          onClick={() => setPanelOpen(true)}
          aria-expanded={panelOpen}
          className="glass lg:hidden ml-auto shrink-0 px-3 min-h-[44px] text-body text-muted hover:text-ink"
        >Panel</button>
      </div>
      {liveCmd.toast && <div className="glass p-3 mb-3 text-body text-cyan">{liveCmd.toast}</div>}
      {liveCmd.error && !liveCmd.preview && (
        <div className="glass p-3 mb-3 text-body text-neg">Ditolak: {liveCmd.error}</div>
      )}
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
            <div className="flex items-center gap-4 text-body font-semibold">
               <span className="text-warn">
                 Skenario {comp.round} {replayPrefs.prefs.competitiveRounds > 0 ? `/ ${replayPrefs.prefs.competitiveRounds}` : ''}
               </span>
               <button className="glass px-3 py-1 text-cyan hover:bg-cyan/10" onClick={() => comp.next(true)}>
                 Skip ⏭
               </button>
            </div>
          )}
        </div>
      )}
      <div className="flex gap-3 flex-1 min-h-0">
        <div className={`relative flex-1 min-h-0 ${paperMode ? "ring-2 ring-violet rounded-lg" : ""}`}>
          {/* On the CHART, not only in the toolbar: a screenshot
              carries the chart alone, and a paper equity curve
              mistaken for the real account is the whole risk here. */}
          {paperMode && (
            <div aria-label="chart akun paper"
                 className="absolute top-2 right-2 z-20 glass px-2 py-1 text-meta text-violet font-semibold">
              PAPER
            </div>
          )}
          {hasBars ? (
            <CandleChart
              ref={chartRef}
              symbol={symbol}
              tf={tf}
              settings={settings}
              candles={shownCandles}
              draggablePositions={draggablePaper ?? draggableReplay}
              plannedOrder={plannedOrder}
              countdown={!replayOpen}
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
              drawings={drawingsProp}
              indicators={ind.renders}
              markers={indicatorMarkers}
              fitToRange={focus ?? undefined}
            />
          ) : (
            <ChartPlaceholder status={data.status} error={data.error} onRetry={data.retry}
                              symbol={symbol} tf={tf} />
          )}

          {/* Non-blocking banners while bars are already shown */}
          {hasBars && <ChartLoadBanner status={data.status} error={data.error} onRetry={data.retry} />}

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

          {comp.evalPause && <ScenarioEvalOverlay pause={comp.evalPause} round={comp.round} />}
        </div>
        <aside className="w-[240px] shrink-0 hidden lg:flex lg:flex-col gap-3 overflow-y-auto">
          {!panelOpen && sidePanel}
        </aside>
      </div>
      {testerStrip}
      {panelOpen && (
        <Sheet label={replayOpen ? "Panel replay" : "Panel chart"} onClose={() => setPanelOpen(false)}>
          <div className="flex flex-col gap-3">{sidePanel}</div>
        </Sheet>
      )}
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
      {accountsOpen && (
        <PaperAccountDialog
          accounts={accounts}
          selectedId={paperAccountId}
          onSelect={(id) => paperPrefs.update({ accountId: id })}
          onCreated={(a) => { loadAccounts(); paperPrefs.update({ accountId: a.id }); }}
          onArchived={(id) => {
            loadAccounts();
            if (id === paperAccountId) paperPrefs.update({ accountId: null });
          }}
          onClose={() => setAccountsOpen(false)}
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
