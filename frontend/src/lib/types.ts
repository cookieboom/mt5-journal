// Shapes mirror web/api.py payloads. Money numbers are raw USC; null = unknown.
export interface Header { login: number; currency: string; offset_s: number; }

export interface Bucket {
  label: string;
  n: number;
  win_rate: number | null;
  expectancy: number | null;
  n_with_r: number;
  avg_r: number | null;
}
export interface Report {
  currency: string;
  n_total: number; n_closed: number;
  n_wins: number; n_losses: number; n_breakeven: number;
  win_rate: number | null;
  avg_win: number | null;
  avg_loss: number | null;
  profit_factor: number | null;
  expectancy: number | null;
  avg_r: number | null; n_with_r: number;
  n_with_mae: number;
  n_with_mae_r: number; avg_mae_r: number | null;
  n_with_mfe_r: number; avg_mfe_r: number | null;
  // Sequence block: close-time order, never §9-gated (facts, not averages).
  // max_drawdown is POSITIVE money; 0 = never drew down, null = nothing to read.
  n_sequenced: number;
  max_drawdown: number | null;
  max_win_streak: number;
  max_loss_streak: number;
  by_session: Bucket[];
  by_source: Bucket[];
  by_symbol: Bucket[];
}
export interface ChartTrade {
  position_id: number;
  symbol_base: string;
  close_time_msc: number;
  net_profit: number;
  r_multiple: number | null;
  mae_r: number | null;
  mfe_r: number | null;
}
export interface ReportData {
  header: Header;
  report: Report;
  series: ChartTrade[];
}

export interface EquitySvg {
  empty: boolean; viewbox: string; points: string; area: string; baseline_y: number;
}
export interface Equity {
  n: number; n_with_r: number;
  equity_last: number | null; r_last: number | null;
  series: { close_time_msc: number; equity: number; position_id: number; symbol_base: string }[];
  equity_svg: EquitySvg; r_svg: EquitySvg;
}

export interface LivePosition {
  position_id: number;
  symbol: string;
  symbol_base: string;
  direction: "buy" | "sell";
  volume: number;
  open_price: number | null;
  price_current: number | null;
  sl: number | null;
  tp: number | null;
  profit: number | null;
  observed_msc: number;
}
export interface Live {
  positions: LivePosition[];
  count: number;
  total_floating: number;
  total_volume: number;
  age_s: number | null;
  stale: boolean;
  empty: boolean;
}

export interface DashboardData {
  header: Header; report: Report; live: Live; equity: Equity;
}

export interface LiveData { header: Header; live: Live; }

export interface CommandRow {
  // null for an "open": no position exists yet to attach an id to (rule 4 —
  // NULL means unknown, never coerce to 0).
  id: number; position_id: number | null; kind: string; status: string;
  // "open" only — symbol/direction/price_ref have no position row to read
  // them from, so they live on the command itself. NULL for every other kind.
  symbol: string | null; direction: string | null; price_ref: number | null;
  sl: number | null; tp: number | null; volume: number | null;
  requested_msc: number; retcode: number | null; retcode_name: string | null;
  result_volume: number | null; result_price: number | null;
  broker_comment: string | null; error: string | null;
}
export interface CommandsData { header: Header; commands: CommandRow[]; }

export interface PreviewResult {
  // null for an "open": no position exists yet to attach an id to.
  intent: string; position_id: number | null; kind: string; symbol: string;
  fields: { sl: number | null; tp: number | null; volume: number | null };
}

// A trade action: the URL segment and the command body it carries.
export type ActionKind = "sltp" | "close" | "close-partial" | "add-volume" | "open";
export interface CommandBody {
  sl?: number | null; tp?: number | null; volume?: number | null;
  // "open" only: no position exists yet, so the command carries what would
  // otherwise come from the position row.
  symbol?: string; entry?: number | null;
  risk_mode?: RiskPrefs["mode"]; risk_value?: number;
}

export interface TradeRow {
  position_id: number;
  symbol_base: string;
  direction: "buy" | "sell";
  status: "closed" | "open" | "partially_open";
  open_time_msc: number;
  close_time_msc: number | null;
  duration_s: number | null;
  net_profit: number | null;
  r_multiple: number | null;
  magic: number | null;
}
export interface TradesData {
  header: Header;
  trades: TradeRow[];
  tags: Record<string, [string, string][]>;  // keyed by String(position_id)
  symbols: string[];
  max_abs_net: number;
  filters: { symbol: string; status: string; source: string };
}

export interface Annotation {
  setup: string | null;
  confidence: number | null;
  emotion: string | null;
  followed_plan: number | null;  // 0 | 1 | null
  notes: string | null;
}
export interface TradeFull {
  position_id: number;
  symbol: string;        // raw, e.g. "XAUUSDc" — for the candle feed
  symbol_base: string;
  direction: "buy" | "sell";
  status: "closed" | "open" | "partially_open";
  open_time_msc: number;
  close_time_msc: number | null;
  duration_s: number | null;
  volume: number;
  open_price: number | null;
  close_price: number | null;
  sl_initial: number | null;
  tp_initial: number | null;
  net_profit: number | null;
  r_multiple: number | null;
  mae_r: number | null;
  mfe_r: number | null;
  magic: number | null;
}
export interface TradeDetailData {
  header: Header;
  trade: TradeFull;
  annotation: Annotation | null;
  tags: [string, string][];
  session: string;
  is_ea: boolean;
  chartable: boolean;
}

export interface TradeNote {
  position_id: number;
  symbol_base: string;
  net_profit: number;
  setup: string | null;
  confidence: number | null;
  emotion: string | null;
  followed_plan: number | null;  // 0 | 1 | null
  notes: string | null;
  tags: string[];
}
export interface WeeklyResult {
  account_login: number;
  currency: string;
  iso_year: number;
  iso_week: number;
  start_msc: number;
  end_msc: number;
  n_closed: number;
  n_wins: number;
  n_losses: number;
  n_breakeven: number;
  net_total: number;
  win_rate: number | null;
  avg_win: number | null;
  avg_loss: number | null;
  profit_factor: number | null;
  expectancy: number | null;
  by_session: Bucket[];
  by_source: Bucket[];
  notes: TradeNote[];
}
export interface WeeklyData {
  header: Header;
  result: WeeklyResult;
  weeks: [number, number][];
  start_ms: number;
}

// Phase B chart feed — mirrors web/api.candles_payload. time_msc is epoch ms,
// broker SERVER time (UTC). Divide by 1000 for lightweight-charts (UNIX seconds).
export interface Candle {
  time_msc: number;
  o: number; h: number; l: number; c: number; v: number;
}
export interface CandlesResponse {
  symbol: string;
  timeframe: string;
  candles: Candle[];
  missing: [number, number][];  // [lo_ms, hi_ms] ranges NOT yet cached
  pending: boolean;             // a fill was enqueued for journal live to drain
}

// A candle the crosshair is hovering (or the latest bar when idle). Same shape
// as Candle; named separately so the info panel's intent reads clearly.
export type HoverBar = Candle;

export interface PriceLineSpec {
  price: number;
  color: string;
  title: string;
}

export type LiveStatus = { live: boolean; beat_msc: number | null; age_ms: number | null };

// /api/candles/live response — the single realtime forming bar for a symbol+tf.
// `forming_updated_msc` is when the SERVER last refreshed the forming row, not
// when the price last moved — a quiet bucket is restamped without any OHLC
// changing. It is what `staleEntryReason` reads to tell a quiet feed from a
// dead one, on the same 15 s window the server's own open guard uses.
export type LiveCandle = {
  forming: Candle | null;
  forming_updated_msc: number | null;
  beat_msc: number | null;
  live: boolean;
};

// Server-derived sizing. `error` non-null always means `volume` is null: the
// server never returns a number the confirm step would then refuse.
export interface SizeResult {
  volume: number | null;
  risk_usc: number | null;      // USC (account currency), never "$"
  risk_pct: number | null;      // of accounts.balance
  distance: number | null;      // |entry - sl| in price units
  rr: number | null;            // |tp - entry| / distance; null when no TP
  direction: "buy" | "sell" | null;
  error: string | null;
}

export interface RiskPrefs {
  mode: "pct" | "usc";
  value: number;
}

// A not-yet-existing order drawn on the chart. `entry` is the live/cursor price
// the human is sizing against; sl/tp are null until dragged (rule 4: null is
// "not set", and 0 would be a price).
export interface PlannedOrder {
  entry: number;
  sl: number | null;
  tp: number | null;
  direction: "buy" | "sell" | null;
}

// Lab: regime + entry-timing models (mirrors web/lab_api.py). Everything here
// is candle-derived and reports its own out-of-sample score + age (CLAUDE.md
// rule 9) — nothing in this feature sizes or places an order.
export type LabStage = "regime" | "timing";
export type LabKind = "logreg" | "lgbm";
export type Regime = "trend_up" | "trend_down" | "range";

export interface LabCalibrationBucket {
  bucket: number;
  predicted: number;
  realised: number;
  n: number;
}

// A timing-stage fold (and the aggregate across folds) carries every metric —
// win_rate/expectancy_r/auc/baseline_expectancy_r/calibration are real
// measurements for a binary "did TP hit first" classifier.
export interface LabTimingFoldMetrics {
  n: number;
  n_taken: number;
  win_rate: number | null;
  expectancy_r: number | null;
  auc: number | null;
  baseline_expectancy_r: number | null;
  calibration: LabCalibrationBucket[];
}
export interface LabTimingMetrics extends LabTimingFoldMetrics {
  folds: LabTimingFoldMetrics[];
}

// A regime-stage fold has none of those four: the backend
// (`lab_api._scrub_regime_metrics`) strips them because the 3-class classifier is scored
// through the same binary helper fed a constant probability, which would
// make auc always exactly 0.5 and expectancy_r always exactly 0.0. `win_rate`
// is relabelled `accuracy` because that's what it actually measures here.
// Omitting the fields (not typing them optional) is the point: it is a
// compile error to read `.auc` off a regime model's metrics.
export interface LabRegimeFoldMetrics {
  n: number;
  n_taken: number;
  accuracy: number | null;
  confusion: Record<Regime, Record<Regime, number>>;
}
export interface LabRegimeMetrics {
  n: number;
  n_taken: number;
  accuracy: number | null;
  folds: LabRegimeFoldMetrics[];
}

interface LabModelBase {
  id: number;
  created_ms: number;
  symbol: string;
  timeframe: string;
  kind: LabKind;
  pooled: boolean;
  active: boolean;
  n_rows: number;
  train_from_ms: number;
  train_to_ms: number;
  config: Record<string, unknown>;
}

export type LabModel =
  | (LabModelBase & { stage: "regime"; regime: null; metrics: LabRegimeMetrics })
  | (LabModelBase & { stage: "timing"; regime: Regime | null; metrics: LabTimingMetrics });

export interface LabBarScore {
  time_msc: number;
  regime: Regime;
  regime_proba: Record<Regime, number>;
  p_tp_long: number | null;
  p_tp_short: number | null;
}

// Every failure a status, never a thrown HTTP error (lab/score.py's own
// framing): "stale_features" means the model was fit on a feature schema
// this data no longer has (retrain); "no_bars" means not enough candle data
// (fill). Keep this a closed union — widening either to `string` loses the
// distinction the UI exists to show.
export type LabScoreStatus =
  | "ok" | "no_model" | "artifact_missing" | "no_bars" | "stale_features";

export interface LabScore {
  symbol: string;
  timeframe: string;
  status: LabScoreStatus;
  model_age_ms: number | null;
  // Suppressed to null below n=20 (CLAUDE.md §8); expectancy_n is the raw
  // count so the UI can render "n = 14, suppressed" instead of a bare dash.
  expectancy_r: number | null;
  expectancy_n: number | null;
  // The same model's random-entry baseline over the same rows, suppressed
  // below its own n=20. Expectancy is only readable against it — see
  // docs/lab-models.md § Reading the metrics table.
  baseline_expectancy_r: number | null;
  baseline_n: number | null;
  pooled: boolean;
  bars: LabBarScore[];
}

// Paper trading. Money is USC — `header.currency` is the label to print, and
// every one of these numbers can be null, meaning UNKNOWN (never 0).
export interface PaperAccount {
  id: number; name: string; initial_balance: number; balance: number;
  leverage: number; stopout_pct: number; status: "active" | "archived";
  created_at_msc: number; archived_at_msc: number | null;
}

export interface PaperHeader {
  currency: string; balance: number;
  equity: number | null; margin: number | null; free_margin: number | null;
  margin_level: number | null; floating: number | null;
  leverage: number; stopout_pct: number;
}

export interface PaperPosition {
  id: number; account_id: number; symbol: string; symbol_base: string;
  direction: "buy" | "sell"; order_kind: "market" | "limit" | "stop";
  request_price: number | null; volume: number; sl: number; tp: number;
  sl_initial: number | null; expires_msc: number | null;
  status: "pending" | "open" | "closed" | "cancelled" | "expired";
  requested_msc: number; entry_msc: number | null; entry_price: number | null;
  exit_msc: number | null; exit_price: number | null;
  exit_reason: "tp" | "sl" | "manual" | "stopout" | "reverse" | null;
  net_profit: number | null; r_multiple: number | null;
  mae_r: number | null; mfe_r: number | null; parent_id: number | null;
  floating?: number | null;
}

export interface PaperSummary {
  n: number; win_rate: number | null; avg_r: number | null; total_r: number;
  avg_mae_r: number | null; avg_mfe_r: number | null;
}

export interface PaperAccountView {
  account: PaperAccount; header: PaperHeader;
  open: PaperPosition[]; pending: PaperPosition[]; closed: PaperPosition[];
  summary: PaperSummary; max_drawdown: number | null;
  equity_curve: { exit_msc: number; balance: number; position_id: number;
                  symbol_base: string }[];
}
