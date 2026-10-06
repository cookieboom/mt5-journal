import type { usePaperAccount } from "../hooks/usePaperAccount";
import { cancelPending, closeAll, closePosition, reversePosition } from "../lib/paperApi";
import PaperAccountBar from "./PaperAccountBar";
import PaperOrderPanel from "./PaperOrderPanel";
import PaperPositions from "./PaperPositions";

/** The chart's side column in paper mode: the virtual account, its order
 *  ticket and its positions. Every action refreshes the account view. */
export default function PaperSidePanel({ paper, symbol, lastPrice, live, onAccounts }: {
  paper: ReturnType<typeof usePaperAccount>;
  symbol: string;
  lastPrice: number | null;
  live: boolean;
  onAccounts: () => void;
}) {
  const view = paper.view;
  return (
    <>
      {view ? (
        <>
          <PaperAccountBar header={view.header} name={view.account.name} live={live} />
          <PaperOrderPanel accountId={view.account.id} symbol={symbol}
                           lastPrice={lastPrice} onPlaced={paper.refresh} />
          <PaperPositions
            view={view}
            chartSymbol={symbol}
            onClose={(id) => void closePosition(id).then(paper.refresh)}
            onPartial={(id) => {
              const held = view.open.find((p) => p.id === id)?.volume ?? 0;
              const half = Math.round((held / 2) * 100) / 100;
              if (half > 0) void closePosition(id, half).then(paper.refresh);
            }}
            onReverse={(id) => void reversePosition(id).then(paper.refresh)}
            onCancel={(id) => void cancelPending(id).then(paper.refresh)}
            onCloseAll={() => void closeAll(view.account.id).then(paper.refresh)}
          />
        </>
      ) : (
        <div className="glass p-3 text-body text-muted">
          {paper.error ?? "Belum ada akun paper dipilih."}
        </div>
      )}
      <button className="glass px-3 py-1 text-body text-muted hover:text-ink" onClick={onAccounts}>
        Akun paper…
      </button>
    </>
  );
}
