import { useState } from "react";
import type { CandleCompleteness } from "../../lib/storageApi";

export interface CoverageVisualizerProps {
  completeness?: CandleCompleteness | null;
  loading?: boolean;
}

interface HoverTooltipInfo {
  type: "covered" | "gap";
  from_ms: number;
  to_ms: number;
  duration_hours?: number;
}

function formatDate(ms: number): string {
  if (!ms || ms <= 0) return "N/A";
  const d = new Date(ms);
  return d.toISOString().replace("T", " ").slice(0, 19) + " UTC";
}

function formatDuration(from_ms: number, to_ms: number): string {
  const ms = to_ms - from_ms;
  if (ms <= 0) return "0m";
  const minutes = Math.floor(ms / (1000 * 60));
  const hours = Math.floor(minutes / 60);
  const days = Math.floor(hours / 24);

  if (days > 0) {
    const remHours = hours % 24;
    return `${days}d ${remHours}h`;
  }
  if (hours > 0) {
    const remMins = minutes % 60;
    return `${hours}h ${remMins}m`;
  }
  return `${minutes}m`;
}

export default function CoverageVisualizer({ completeness, loading }: CoverageVisualizerProps) {
  const [hoveredInfo, setHoveredInfo] = useState<HoverTooltipInfo | null>(null);

  if (loading) {
    return (
      <div className="glass p-5 rounded-xl border border-panel-border space-y-4 animate-pulse">
        <div className="flex items-center justify-between">
          <div className="h-5 bg-white/10 rounded w-1/4"></div>
          <div className="h-5 bg-white/10 rounded w-16"></div>
        </div>
        <div className="h-8 bg-white/10 rounded-lg w-full"></div>
        <div className="flex justify-between">
          <div className="h-3 bg-white/5 rounded w-1/3"></div>
          <div className="h-3 bg-white/5 rounded w-1/4"></div>
        </div>
      </div>
    );
  }

  if (!completeness || completeness.total_bars === 0 || completeness.from_ms === 0) {
    return (
      <div className="glass p-6 rounded-xl border border-panel-border text-center space-y-2">
        <div className="text-sm font-semibold text-muted">Data Coverage Timeline</div>
        <p className="text-xs text-muted/70">
          No candle history available for this symbol and timeframe.
        </p>
      </div>
    );
  }

  const {
    symbol,
    timeframe,
    total_bars,
    from_ms,
    to_ms,
    coverage_percent,
    covered_ranges = [],
    gaps = [],
  } = completeness;

  const totalSpanMs = Math.max(to_ms - from_ms, 1);

  // Helper for badge color based on coverage %
  const getBadgeClass = (pct: number) => {
    if (pct >= 98) return "bg-emerald-500/10 text-emerald-400 border-emerald-500/30";
    if (pct >= 85) return "bg-amber-500/10 text-amber-400 border-amber-500/30";
    return "bg-rose-500/10 text-rose-400 border-rose-500/30";
  };

  return (
    <div className="glass p-5 rounded-xl border border-panel-border space-y-4">
      {/* Header Info */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2.5">
          <div className="flex items-center gap-1.5 font-bold text-ink text-base">
            <span>{symbol}</span>
            <span className="text-xs font-mono px-2 py-0.5 rounded bg-cyan/10 text-cyan border border-cyan/20">
              {timeframe}
            </span>
          </div>
          <span className="text-xs text-muted font-mono">
            ({total_bars.toLocaleString()} bars)
          </span>
        </div>

        <div className="flex items-center gap-3">
          {/* Coverage Badge */}
          <div
            className={`px-3 py-1 rounded-full border text-xs font-semibold font-mono flex items-center gap-1.5 ${getBadgeClass(
              coverage_percent
            )}`}
          >
            <span
              className={`w-2 h-2 rounded-full ${
                coverage_percent >= 98
                  ? "bg-emerald-400"
                  : coverage_percent >= 85
                  ? "bg-amber-400"
                  : "bg-rose-400 animate-pulse"
              }`}
            />
            <span>{coverage_percent.toFixed(2)}% Covered</span>
          </div>
        </div>
      </div>

      {/* Interactive Timeline Bar */}
      <div className="space-y-1.5">
        <div className="relative h-8 w-full bg-slate-900/80 rounded-lg overflow-hidden border border-panel-border/80 flex items-center shadow-inner">
          {/* Base Background representing gaps */}
          <div className="absolute inset-0 bg-rose-500/20" />

          {/* Render Gaps (explicit blocks) */}
          {gaps.map((gap, idx) => {
            const leftPct = Math.max(0, ((gap.from_ms - from_ms) / totalSpanMs) * 100);
            const widthPct = Math.max(0.15, ((gap.to_ms - gap.from_ms) / totalSpanMs) * 100);

            return (
              <div
                key={`gap-${idx}-${gap.from_ms}`}
                style={{ left: `${leftPct}%`, width: `${widthPct}%` }}
                className="absolute top-0 bottom-0 bg-rose-500/80 hover:bg-rose-400 transition-colors cursor-pointer z-10"
                onMouseEnter={() =>
                  setHoveredInfo({
                    type: "gap",
                    from_ms: gap.from_ms,
                    to_ms: gap.to_ms,
                    duration_hours: gap.duration_hours,
                  })
                }
                onMouseLeave={() => setHoveredInfo(null)}
              />
            );
          })}

          {/* Render Covered Ranges (emerald blocks) */}
          {covered_ranges.map((range, idx) => {
            const leftPct = Math.max(0, ((range.from_ms - from_ms) / totalSpanMs) * 100);
            const widthPct = Math.max(0.15, ((range.to_ms - range.from_ms) / totalSpanMs) * 100);

            return (
              <div
                key={`cov-${idx}-${range.from_ms}`}
                style={{ left: `${leftPct}%`, width: `${widthPct}%` }}
                className="absolute top-0 bottom-0 bg-emerald-500/80 hover:bg-emerald-400 transition-colors cursor-pointer z-20"
                onMouseEnter={() =>
                  setHoveredInfo({
                    type: "covered",
                    from_ms: range.from_ms,
                    to_ms: range.to_ms,
                  })
                }
                onMouseLeave={() => setHoveredInfo(null)}
              />
            );
          })}
        </div>

        {/* Hover Tooltip / Status Banner */}
        <div className="h-6 flex items-center justify-between text-xs font-mono">
          {hoveredInfo ? (
            <div className="flex items-center gap-2 text-ink bg-slate-800/90 px-2.5 py-0.5 rounded border border-panel-border">
              <span
                className={`w-2 h-2 rounded-full ${
                  hoveredInfo.type === "covered" ? "bg-emerald-400" : "bg-rose-400"
                }`}
              />
              <span className="font-semibold uppercase text-[10px] tracking-wider">
                {hoveredInfo.type === "covered" ? "Covered Segment" : "Data Gap"}
              </span>
              <span className="text-muted">•</span>
              <span>
                {formatDate(hoveredInfo.from_ms)} → {formatDate(hoveredInfo.to_ms)}
              </span>
              <span className="text-muted">•</span>
              <span className="text-cyan">
                ({formatDuration(hoveredInfo.from_ms, hoveredInfo.to_ms)})
              </span>
            </div>
          ) : (
            <span className="text-muted/60 text-[11px] italic">
              Hover over timeline blocks to inspect range details
            </span>
          )}
        </div>
      </div>

      {/* Timeline Range Footer & Legend */}
      <div className="pt-2 border-t border-panel-border/50 flex flex-wrap items-center justify-between gap-3 text-xs text-muted">
        <div className="flex items-center gap-4">
          <div className="flex items-center gap-1.5">
            <span className="w-3 h-3 rounded-sm bg-emerald-500/80 border border-emerald-400/50 inline-block" />
            <span>Covered Data</span>
          </div>
          <div className="flex items-center gap-1.5">
            <span className="w-3 h-3 rounded-sm bg-rose-500/80 border border-rose-400/50 inline-block" />
            <span>Gap Range</span>
          </div>
        </div>

        <div className="font-mono text-[11px] text-muted flex items-center gap-2">
          <span>{formatDate(from_ms)}</span>
          <span>→</span>
          <span>{formatDate(to_ms)}</span>
        </div>
      </div>
    </div>
  );
}

export { CoverageVisualizer };
