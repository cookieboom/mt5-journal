// Shapes mirror web/api.py payloads. Money numbers are raw USC; null = unknown.
export interface Header { login: number; currency: string; offset_s: number; }

export interface Report {
  currency: string;
  n_total: number; n_closed: number;
  n_wins: number; n_losses: number; n_breakeven: number;
  win_rate: number | null;
  expectancy: number | null;
  avg_r: number | null; n_with_r: number;
  by_symbol: { label: string; n: number; n_with_r: number; win_rate: number | null; avg_r: number | null }[];
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
  intent: string; position_id: number; kind: string; symbol: string;
  fields: { sl: number | null; tp: number | null; volume: number | null };
}

// A trade action: the URL segment and the command body it carries.
export type ActionKind = "sltp" | "close" | "close-partial" | "add-volume";
export interface CommandBody { sl?: number | null; tp?: number | null; volume?: number | null; }

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
