import { formatAge } from "../lib/lab";
import type { LabScore, LabScoreStatus } from "../lib/types";

const STALE_MS = 30 * 86_400_000;

const LABEL: Record<string, string> = {
  trend_up: "Trend up",
  trend_down: "Trend down",
  range: "Range",
};

// "stale_features"/"no_bars" must read as distinct prompts (retrain vs. fill),
// not interchangeable "something's wrong" text — same convention as
// pages/Lab.tsx's scoreStatusText (CLAUDE.md rule 9 / task brief).
const STATUS_TEXT: Record<LabScoreStatus, string> = {
  ok: "",
  no_model: "No model trained for this symbol and timeframe.",
  artifact_missing: "Model file missing — retrain from /lab.",
  no_bars: "Not enough candle data cached to score — fill from journal live.",
  stale_features: "Model needs retraining — features changed. Retrain from /lab.",
};

function pct(value: number | null): string {
  return value === null ? "—" : `${Math.round(value * 100)}%`;
}

// Real Tailwind utilities, not bespoke class names — this codebase has no
// lab-badge/-regime/-probs/-provenance/muted/stale CSS anywhere, so those
// names rendered as plain unstyled text with every status looking identical.
// Reuses the same good/bad/neutral pill vocabulary StalenessBadge.tsx already
// established for this exact page: cyan = a real current reading, neg = a
// stale/degraded one needing action, muted = nothing to show. On the one page
// with order buttons, a degraded reading must look different, not just read
// different (CLAUDE.md rule 9).
const MUTED = "text-[11px] text-muted px-2.5 py-1.5 rounded-lg bg-white/5";
const OK = "text-[11px] text-ink px-2.5 py-1.5 rounded-lg bg-cyan/10 ring-1 ring-cyan/25";
const STALE = "text-[11px] text-ink px-2.5 py-1.5 rounded-lg bg-neg/10 ring-1 ring-neg/25";

export default function LabBadge({ score }: { score: LabScore | null }) {
  if (!score) return <div className={MUTED} data-status="loading">Lab —</div>;
  if (score.status !== "ok" || score.bars.length === 0) {
    return (
      <div className={MUTED} data-status={score.status}>
        {STATUS_TEXT[score.status] || "Lab —"}
      </div>
    );
  }

  const latest = score.bars[score.bars.length - 1];
  const stale = (score.model_age_ms ?? 0) > STALE_MS;
  const expectancy = score.expectancy_r;
  const n = typeof score.expectancy_n === "number" ? score.expectancy_n : null;

  return (
    <div className={stale ? STALE : OK} data-status={stale ? "stale" : "ok"}>
      <div className="font-semibold">{LABEL[latest.regime] ?? latest.regime}</div>
      <div className="flex gap-3 mt-0.5">
        <span>long {pct(latest.p_tp_long)}</span>
        <span>short {pct(latest.p_tp_short)}</span>
      </div>
      {/* Age and out-of-sample expectancy are not optional decoration: a
          probability rendered beside an order button without them is a
          recommendation, which this tool does not make (CLAUDE.md rule 9). */}
      <div className="text-muted mt-0.5">
        <span className={stale ? "text-neg font-semibold" : ""}>
          {formatAge(score.model_age_ms)}{stale ? " · stale" : ""}
        </span>
        {" · "}
        <span>
          out-of-sample{" "}
          {expectancy === null
            ? n !== null ? `— (n=${n} < 20)` : "—"
            : `${expectancy >= 0 ? "+" : ""}${expectancy.toFixed(2)}R${n !== null ? ` (n=${n})` : ""}`}
        </span>
        {score.pooled && <span> · pooled model</span>}
      </div>
    </div>
  );
}
