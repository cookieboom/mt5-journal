import type { EvalPause } from "../hooks/useCompetitiveReplay";

/** Full-chart overlay between competitive scenarios: the finished scenario's
 *  result in R, or a "finding a new scenario" note after a skip. */
export default function ScenarioEvalOverlay({ pause, round }: { pause: EvalPause; round: number }) {
  return (
    <div className="absolute inset-0 bg-black/80 flex flex-col items-center justify-center z-50 text-center">
      {pause.isSkip ? (
        <h2 className="text-display font-bold text-muted">Mencari Skenario Baru...</h2>
      ) : (
        <>
          <h2 className="text-display font-bold mb-2">Evaluasi Skenario {round}</h2>
          <div className={`text-4xl font-bold ${pause.pnl >= 0 ? "text-up" : "text-down"}`}>
            {pause.pnl > 0 ? "+" : ""}{pause.pnl.toFixed(2)}R
          </div>
          <div className="text-muted mt-4">Bersiap untuk skenario berikutnya...</div>
        </>
      )}
    </div>
  );
}
