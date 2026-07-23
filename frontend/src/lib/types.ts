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
  position_id: number; symbol: string; volume: number; profit: number | null;
}
export interface Live {
  positions: LivePosition[]; count: number;
  total_floating: number; total_volume: number;
  age_s: number | null; stale: boolean; empty: boolean;
}

export interface DashboardData {
  header: Header; report: Report; live: Live; equity: Equity;
}
