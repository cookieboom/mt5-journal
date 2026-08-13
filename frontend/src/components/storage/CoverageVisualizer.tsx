import { useState } from "react";
import type { CandleCompleteness } from "../../lib/storageApi";
import { wib } from "../../lib/format";

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

// WIB at display time, like every other timestamp in the app (PRODUCT.md).
function formatDate(ms: number): string {
  if (!ms || ms <= 0) return "—";
  return wib(ms);
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
      <div className="glass p-5 space-y-4 animate-pulse">
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
      <div className="glass p-6 text-center space-y-2">
        <div className="text-title font-semibold text-muted">Timeline coverage data</div>
        <p className="text-body text-muted/70">
          Belum ada candle untuk symbol dan timeframe ini.
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

  // The rest of the app already speaks coverage in one palette (CoverageRibbon,
  // DataHealthPanel): cyan is covered, rose is a hole that could be filled,
  // amber is degraded-but-not-wrong. Green is an outcome and has no business
  // here — a full range is not a profit (DESIGN.md § Colors).
  const getBadgeClass = (pct: number) => {
    if (pct >= 98) return "bg-cyan/10 text-cyan border-cyan/30";
    if (pct >= 85) return "bg-warn/10 text-warn border-warn/30";
    return "bg-neg/10 text-neg border-neg/30";
  };

  return (
    <div className="glass p-5 space-y-4">
      {/* Header Info */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2.5">
          <div className="flex items-center gap-1.5 font-bold text-ink text-title">
            <span>{symbol}</span>
            <span className="text-body font-mono px-2 py-0.5 rounded bg-cyan/10 text-cyan border border-cyan/20">
              {timeframe}
            </span>
          </div>
          <span className="text-body text-muted font-mono num">
            ({total_bars.toLocaleString()} bar)
          </span>
        </div>

        <div className="flex items-center gap-3">
          {/* Coverage Badge */}
          <div
            className={`px-3 py-1 rounded-full border text-body font-semibold font-mono flex items-center gap-1.5 ${getBadgeClass(
              coverage_percent
            )}`}
          >
            <span
              className={`w-2 h-2 rounded-full ${
                coverage_percent >= 98
                  ? "bg-cyan"
                  : coverage_percent >= 85
                  ? "bg-warn"
                  : "bg-neg"
              }`}
            />
            <span className="num">{coverage_percent.toFixed(2)}% tercover</span>
          </div>
        </div>
      </div>

      {/* Interactive Timeline Bar */}
      <div className="space-y-1.5">
        <div className="relative h-8 w-full bg-bg/80 rounded-lg overflow-hidden border border-panel-border/80 flex items-center shadow-inner">
          {/* Base Background representing gaps */}
          <div className="absolute inset-0 bg-neg/20" />

          {/* Render Gaps (explicit blocks) */}
          {gaps.map((gap, idx) => {
            const leftPct = Math.max(0, ((gap.from_ms - from_ms) / totalSpanMs) * 100);
            const widthPct = Math.max(0.15, ((gap.to_ms - gap.from_ms) / totalSpanMs) * 100);

            return (
              <div
                key={`gap-${idx}-${gap.from_ms}`}
                style={{ left: `${leftPct}%`, width: `${widthPct}%` }}
                className="absolute top-0 bottom-0 bg-neg/80 hover:bg-neg transition-colors cursor-pointer z-10"
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

          {/* Render Covered Ranges */}
          {covered_ranges.map((range, idx) => {
            const leftPct = Math.max(0, ((range.from_ms - from_ms) / totalSpanMs) * 100);
            const widthPct = Math.max(0.15, ((range.to_ms - range.from_ms) / totalSpanMs) * 100);

            return (
              <div
                key={`cov-${idx}-${range.from_ms}`}
                style={{ left: `${leftPct}%`, width: `${widthPct}%` }}
                className="absolute top-0 bottom-0 bg-cyan/80 hover:bg-cyan transition-colors cursor-pointer z-20"
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
        <div className="h-6 flex items-center justify-between text-body font-mono">
          {hoveredInfo ? (
            <div className="flex items-center gap-2 text-ink bg-bg/90 px-2.5 py-0.5 rounded border border-panel-border">
              <span
                className={`w-2 h-2 rounded-full ${
                  hoveredInfo.type === "covered" ? "bg-cyan" : "bg-neg"
                }`}
              />
              <span className="font-semibold uppercase text-label">
                {hoveredInfo.type === "covered" ? "segmen tercover" : "gap data"}
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
            <span className="text-muted/60 text-meta">
              Arahkan kursor ke blok timeline untuk lihat rentangnya
            </span>
          )}
        </div>
      </div>

      {/* Timeline Range Footer & Legend */}
      <div className="pt-2 border-t border-panel-border/50 flex flex-wrap items-center justify-between gap-3 text-body text-muted">
        <div className="flex items-center gap-4">
          <div className="flex items-center gap-1.5">
            <span className="w-3 h-3 rounded-sm bg-cyan/80 border border-cyan/50 inline-block" />
            <span>data tercover</span>
          </div>
          <div className="flex items-center gap-1.5">
            <span className="w-3 h-3 rounded-sm bg-neg/80 border border-neg/50 inline-block" />
            <span>rentang gap</span>
          </div>
        </div>

        <div className="font-mono text-meta text-muted flex items-center gap-2">
          <span>{formatDate(from_ms)}</span>
          <span>→</span>
          <span>{formatDate(to_ms)}</span>
        </div>
      </div>
    </div>
  );
}

export { CoverageVisualizer };
