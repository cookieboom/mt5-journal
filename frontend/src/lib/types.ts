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
  series: { close_time_msc: number; equity: number }[];
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
  id: number; position_id: number; kind: string; status: string;
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
export type LiveCandle = { forming: Candle | null; beat_msc: number | null; live: boolean };

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
