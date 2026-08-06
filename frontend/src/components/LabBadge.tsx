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

export default function LabBadge({ score }: { score: LabScore | null }) {
  if (!score) return <div className="lab-badge muted">Lab —</div>;
  if (score.status !== "ok" || score.bars.length === 0) {
    return <div className="lab-badge muted">{STATUS_TEXT[score.status] || "Lab —"}</div>;
  }

  const latest = score.bars[score.bars.length - 1];
  const stale = (score.model_age_ms ?? 0) > STALE_MS;
  const expectancy = score.expectancy_r;
  const n = typeof score.expectancy_n === "number" ? score.expectancy_n : null;

  return (
    <div className={`lab-badge${stale ? " stale" : ""}`}>
      <div className="lab-badge-regime">{LABEL[latest.regime] ?? latest.regime}</div>
      <div className="lab-badge-probs">
        <span>long {pct(latest.p_tp_long)}</span>
        <span>short {pct(latest.p_tp_short)}</span>
      </div>
      {/* Age and out-of-sample expectancy are not optional decoration: a
          probability rendered beside an order button without them is a
          recommendation, which this tool does not make (CLAUDE.md rule 9). */}
      <div className="lab-badge-provenance">
        <span>{formatAge(score.model_age_ms)}{stale ? " · stale" : ""}</span>
        <span>
          out-of-sample{" "}
          {expectancy === null
            ? n !== null ? `— (n=${n} < 20)` : "—"
            : `${expectancy >= 0 ? "+" : ""}${expectancy.toFixed(2)}R${n !== null ? ` (n=${n})` : ""}`}
        </span>
        {score.pooled && <span>pooled model</span>}
      </div>
    </div>
  );
}
