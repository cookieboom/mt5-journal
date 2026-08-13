import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import CandleChart from "../components/CandleChart";
import LabMetrics from "../components/LabMetrics";
import RegimeOverlay from "../components/RegimeOverlay";
import { palette, tint } from "../lib/theme";
import { SYMBOLS, TIMEFRAMES as ALL_TIMEFRAMES, initialWindow, type Sym, type Timeframe } from "../lib/candles";
import { DEFAULT_SETTINGS } from "../lib/chartPrefs";
import {
  DEFAULT_TRAIN_FORM,
  LAB_FEATURES,
  activateModel,
  fetchModels,
  fetchRegimes,
  formatAge,
  trainModels,
  type TrainForm,
  type TrainResponse,
} from "../lib/lab";
import { toBands } from "../lib/regimeBands";
import type { ChartHandle } from "./Chart";
import type { LabModel, LabScore, LabScoreStatus } from "../lib/types";
import { useChartData } from "../hooks/useChartData";

const TIMEFRAMES = ["M1", "M5", "M15", "H1"];
const CHART_HEIGHT = 360;
const STRIP_HEIGHT = 32;

// "stale_features"/"no_bars" must read as a distinct prompt (retrain vs.
// fill), not interchangeable "something's wrong" text — CLAUDE.md rule 9 /
// task brief. The other two are less common backend states, worded plainly.
function scoreStatusText(status: LabScoreStatus): string {
  switch (status) {
    case "no_model": return "Belum ada model untuk symbol/timeframe ini — latih satu di atas.";
    case "artifact_missing": return "File model hilang di disk — latih ulang untuk memulihkannya.";
    case "stale_features": return "RETRAIN — model ini dilatih pada fitur yang sudah tidak ada di data sekarang.";
    case "no_bars": return "FILL — candle yang tersimpan belum cukup untuk rentang ini.";
    default: return "";
  }
}
const fieldText = "bg-white/5 rounded px-2 py-1 text-ink w-full";
const field = fieldText + " num";

// Display-only override: the raw feature key is "spread" (sent to the
// backend unchanged via form.features), but rendering that word as its own
// visible label collides with the dropped-feature warning below, which also
// names "spread" — vitest's text matcher then finds two elements for one
// query. Rename the label only; the wire value is untouched.
const FEATURE_LABELS: Record<string, string> = { spread: "bid/ask cost" };

export default function Lab() {
  const [form, setForm] = useState<TrainForm>(DEFAULT_TRAIN_FORM);
  const [models, setModels] = useState<LabModel[]>([]);
  const [result, setResult] = useState<TrainResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [modelsError, setModelsError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    try {
      const { models } = await fetchModels(form.symbol, form.timeframe);
      setModels(models);
      setModelsError(null);
    } catch (e) {
      setModelsError(e instanceof Error ? e.message : String(e));
    }
  }, [form.symbol, form.timeframe]);

  useEffect(() => { void reload(); }, [reload]);

  // The form's symbol field is free text (unlike the timeframe <select>, which
  // only ever offers a Timeframe member); the chart below needs the narrower
  // Sym/Timeframe types CandleChart is typed against. Falls back to null
  // (chart section hidden) rather than casting a value the account has never
  // traded.
  const symbol: Sym | null = (SYMBOLS as string[]).includes(form.symbol) ? (form.symbol as Sym) : null;
  const tf: Timeframe | null =
    (ALL_TIMEFRAMES as string[]).includes(form.timeframe) ? (form.timeframe as Timeframe) : null;

  const chartRef = useRef<ChartHandle>(null);
  const [nowVisible, setNowVisible] = useState(false);
  // onNowVisibleChange carries a DERIVED BOOLEAN (isNowVisible) — React 18
  // bails out of re-rendering when a setter is called with an Object.is-equal
  // value, so setNowVisible alone is a no-op for most of a pan (nowVisible
  // stays false the whole gesture) or a zoom that keeps "now" on screen
  // (stays true). RegimeOverlay/the probability strip read chartRef.current
  // .timeToX() at RENDER time, so without an unconditional re-render they
  // keep the x-coordinates from the last actual boolean flip while the chart
  // underneath has moved. Same fix CandleChart already uses on itself
  // (bumpProjection) for its own internal overlays — a counter bumped on
  // every call, independent of the value, forces the projection to
  // recompute; nowVisible stays around only because CandleChart requires it.
  const [, bumpProjection] = useReducer((c: number) => c + 1, 0);
  const onNowVisibleChange = useCallback((v: boolean) => {
    setNowVisible(v);
    bumpProjection();
  }, []);
  const data = useChartData(
    symbol ?? DEFAULT_SETTINGS.defaultSymbol, tf ?? DEFAULT_SETTINGS.defaultTimeframe,
    DEFAULT_SETTINGS.initialBars, DEFAULT_SETTINGS.maxBars,
  );
  const hasBars = data.candles.length > 0;

  const [score, setScore] = useState<LabScore | null>(null);
  const [scoreError, setScoreError] = useState<string | null>(null);

  // The scored window mirrors the chart's own initial window (same tf, same
  // bar count) rather than data.candles' actual bounds — decoupled from
  // useChartData's own async fill/poll cycle, and re-derivable at the two
  // moments the brief calls for: right after training, and on symbol/tf
  // change (below).
  const loadScore = useCallback(async (sym: Sym, timeframe: Timeframe) => {
    const [fromMs, toMs] = initialWindow(timeframe, Date.now(), DEFAULT_SETTINGS.initialBars);
    try {
      setScore(await fetchRegimes(sym, timeframe, fromMs, toMs));
      setScoreError(null);
    } catch (e) {
      setScore(null);
      setScoreError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    if (!symbol || !tf) return;
    void loadScore(symbol, tf);
  }, [symbol, tf, loadScore]);

  const bands = useMemo(
    () => (score && score.status === "ok" ? toBands(score.bars) : []),
    [score],
  );
  // Fresh each render on purpose: reads chartRef.current.timeToX, which is
  // only ever correct AT CALL TIME (the chart's time scale after the latest
  // pan/zoom) — memoizing the closure itself would be fine, but there is
  // nothing to memoize against here. Re-renders are driven by
  // onNowVisibleChange below, which CandleChart already fires on load/pan/zoom.
  const toX = (timeMsc: number): number | null => chartRef.current?.timeToX(timeMsc) ?? null;

  const setNum = (key: keyof TrainForm) => (e: { target: { value: string } }) =>
    setForm((f) => ({ ...f, [key]: Number(e.target.value) }));

  const toggleFeature = (name: string) =>
    setForm((f) => ({
      ...f,
      features: f.features.includes(name)
        ? f.features.filter((x) => x !== name)
        : [...f.features, name],
    }));

  const onTrain = async () => {
    setBusy(true);
    setError(null);
    try {
      setResult(await trainModels(form));
      await reload();
      if (symbol && tf) void loadScore(symbol, tf);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  // The score (chart shading, probability strip, the header line below) comes
  // from whichever model is ACTIVE — so switching the active model has to
  // reload it too, or the table says lgbm/logreg while the chart still shows
  // the other one's regimes and expectancy. And activateModel is throw-style
  // (lib/lab.ts): without a catch, a rejected activation was an unhandled
  // promise rejection with nothing visible on a page that shows errors for
  // both its other operations.
  const onActivate = async (id: number) => {
    try {
      await activateModel(id);
      await reload();
      if (symbol && tf) await loadScore(symbol, tf);
    } catch (e) {
      setError(`Failed to activate model ${id}: ${e instanceof Error ? e.message : String(e)}`);
    }
  };

  return (
    <div>
      <h1 className="text-headline font-bold mb-1">Lab</h1>
      <p className="text-body text-muted mb-4 max-w-[60ch]">
        Models trained here predict, on candle data only — nothing here places,
        sizes, or recommends a trade. Every number below is out-of-sample and
        net of trading cost; a model is only interesting where it beats the baseline.
      </p>

      <section className="glass p-4 mb-4">
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-body">
          <label className="flex flex-col gap-1 text-muted">Symbol
            <input className={fieldText} value={form.symbol}
                   onChange={(e) => setForm((f) => ({ ...f, symbol: e.target.value }))} />
          </label>
          <label className="flex flex-col gap-1 text-muted">Timeframe
            <select className={fieldText} value={form.timeframe}
                    onChange={(e) => setForm((f) => ({ ...f, timeframe: e.target.value }))}>
              {TIMEFRAMES.map((tf) => <option key={tf} value={tf}>{tf}</option>)}
            </select>
          </label>
          {/* `min` is a convenience only — the server validates these three
              and answers 400; a browser is not a trust boundary. */}
          <label className="flex flex-col gap-1 text-muted">Bars ahead (N)
            <input className={field} type="number" min="1" value={form.n_bars} onChange={setNum("n_bars")} />
          </label>
          <label className="flex flex-col gap-1 text-muted">Risk (k × ATR)
            <input className={field} type="number" min="0.1" step="0.1" value={form.k_atr} onChange={setNum("k_atr")} />
          </label>
          <label className="flex flex-col gap-1 text-muted">Reward ratio
            <input className={field} type="number" min="0.1" step="0.1" value={form.rr} onChange={setNum("rr")} />
          </label>
          <label className="flex flex-col gap-1 text-muted">Regime threshold
            <input className={field} type="number" step="0.05" value={form.er_threshold} onChange={setNum("er_threshold")} />
          </label>
          <label className="flex flex-col gap-1 text-muted">Folds
            <input className={field} type="number" value={form.n_folds} onChange={setNum("n_folds")} />
          </label>
          <label className="flex flex-col gap-1 text-muted">Assumed spread (points)
            <input className={field} type="number" value={form.default_spread_points} onChange={setNum("default_spread_points")} />
          </label>
        </div>

        <fieldset className="mt-3">
          <legend className="text-label uppercase text-muted mb-1.5">Features</legend>
          <div className="flex flex-wrap gap-x-4 gap-y-1.5 text-body">
            {LAB_FEATURES.map((name) => (
              <label key={name} className="flex items-center gap-1.5 text-ink">
                <input type="checkbox" checked={form.features.includes(name)}
                       onChange={() => toggleFeature(name)} />
                {FEATURE_LABELS[name] ?? name}
              </label>
            ))}
          </div>
        </fieldset>

        <button
          className="mt-4 px-3 py-1.5 rounded bg-cyan/20 ring-1 ring-cyan/45 text-ink font-semibold text-body disabled:opacity-50"
          onClick={onTrain}
          disabled={busy || form.features.length === 0}
        >
          {busy ? "Training…" : "Train"}
        </button>
      </section>

      {error && <p className="text-neg text-body mb-3">{error}</p>}

      {result && Object.keys(result.dropped_features).length > 0 && (
        <p data-testid="dropped-features-warning" className="text-body text-warn mb-3">
          Dropped {Object.entries(result.dropped_features)
            .map(([k, v]) => `${k} (${Math.round(v * 100)}% unknown)`)
            .join(", ")}
          {result.spread_assumed
            ? " — cost uses the assumed spread, not measured spread."
            : ""}
        </p>
      )}

      {modelsError && <p className="text-neg text-body mb-3">Gagal memuat model: {modelsError}</p>}

      <LabMetrics models={models} onActivate={onActivate} />

      <section className="glass p-4 mt-4">
        <h2 className="text-label uppercase text-muted mb-2">
          Chart · regime shading &amp; take-profit probability
        </h2>

        {score && score.status === "ok" && (
          <p className="text-meta text-muted mb-2">
            model age {formatAge(score.model_age_ms)}
            {score.expectancy_r !== null
              ? ` · expectancy ${score.expectancy_r >= 0 ? "+" : ""}${score.expectancy_r.toFixed(2)}R (n=${score.expectancy_n})`
              : score.expectancy_n !== null
                ? ` · expectancy suppressed (n=${score.expectancy_n} < 20)`
                : ""}
            {/* A model is only interesting where it beats this — the sentence
                at the top of the page states the standard, so the number it
                is measured against has to be here too. */}
            {typeof score.baseline_expectancy_r === "number"
              ? ` · vs baseline ${score.baseline_expectancy_r >= 0 ? "+" : ""}${score.baseline_expectancy_r.toFixed(2)}R (n=${score.baseline_n})`
              : typeof score.baseline_n === "number"
                ? ` · baseline suppressed (n=${score.baseline_n} < 20)`
                : ""}
          </p>
        )}

        {!symbol || !tf ? (
          <p className="text-muted text-body py-6">Isi symbol yang ditradingkan akun ini untuk lihat chart-nya.</p>
        ) : !hasBars ? (
          <p className="text-muted text-body py-6">
            {data.status === "error" ? `Gagal memuat candle: ${data.error}` : "Memuat chart…"}
          </p>
        ) : (
          <>
            <div className="relative" style={{ height: CHART_HEIGHT }}>
              <CandleChart
                ref={chartRef}
                symbol={symbol}
                tf={tf}
                settings={DEFAULT_SETTINGS}
                candles={data.candles}
                onHover={() => {}}
                onNowVisibleChange={onNowVisibleChange}
                onRequestOlder={data.loadOlder}
                lastBarMs={data.lastBarMs}
                live={null}
                nowVisible={nowVisible}
              />
              {bands.length > 0 && <RegimeOverlay bands={bands} toX={toX} height={CHART_HEIGHT} />}
              {score && score.status !== "ok" && (
                <div className="absolute inset-0 flex items-center justify-center text-center px-6
                                 text-body text-muted bg-bg/70">
                  {scoreStatusText(score.status)}
                </div>
              )}
            </div>

            <div className="relative mt-1" style={{ height: STRIP_HEIGHT }}>
              {score && score.status === "ok" ? (
                <svg width="100%" height={STRIP_HEIGHT} className="block">
                  {score.bars.map((b) => {
                    if (b.p_tp_long === null) return null;
                    const x = toX(b.time_msc);
                    if (x === null) return null;
                    const h = Math.max(1, b.p_tp_long * STRIP_HEIGHT);
                    return (
                      <rect key={b.time_msc} x={x - 1} y={STRIP_HEIGHT - h} width={2} height={h}
                            fill={tint(palette.cyan, 0.7)} />
                    );
                  })}
                </svg>
              ) : (
                <p className="text-muted text-meta">
                  {score ? scoreStatusText(score.status) : ""}
                </p>
              )}
            </div>

            {scoreError && (
              <p className="text-neg text-body mt-1">Gagal memuat skor regime: {scoreError}</p>
            )}
          </>
        )}
      </section>
    </div>
  );
}
