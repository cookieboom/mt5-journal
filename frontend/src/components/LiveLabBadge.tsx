import { useEffect, useState } from "react";
import LabBadge from "./LabBadge";
import { useLabScore } from "../hooks/useLabScore";
import { fetchModels, modelTimeframe } from "../lib/lab";
import { timeframeMs, type Sym, type Timeframe } from "../lib/candles";

// One badge per open symbol on /live. /live has no chart or timeframe
// selector of its own to read a timeframe from, and the page's chart default
// (M5) is NOT what /lab trains by default (H1) — asking for M5 rendered "No
// model trained for this symbol and timeframe" on an account whose H1 model
// was trained and working. The timeframe therefore comes from the symbol's
// own active (else newest) timing model, and the header states which
// timeframe the reading describes.
function ScoredBadge({ symbol, tf }: { symbol: Sym; tf: Timeframe }) {
  const { score, error } = useLabScore(symbol, tf, timeframeMs(tf));
  return error
    ? <div className="text-neg text-meta">Lab: {error}</div>
    : <LabBadge score={score} />;
}

export default function LiveLabBadge({ symbol, fallbackTf }: {
  symbol: Sym;
  fallbackTf: Timeframe;
}) {
  const [tf, setTf] = useState<Timeframe | null>(null);

  useEffect(() => {
    let alive = true;
    fetchModels(symbol)
      .then(({ models }) => { if (alive) setTf(modelTimeframe(models, fallbackTf)); })
      // A failed lookup is not a reason to show nothing: fall back to the
      // page default and let the score request report its own status.
      .catch(() => { if (alive) setTf(fallbackTf); });
    return () => { alive = false; };
  }, [symbol, fallbackTf]);

  return (
    <div>
      <div className="text-label text-muted uppercase mb-1">
        {symbol}{tf ? ` · ${tf}` : ""}
      </div>
      {tf ? <ScoredBadge symbol={symbol} tf={tf} /> : <LabBadge score={null} />}
    </div>
  );
}
