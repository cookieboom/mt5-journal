export default function TradeSparkbar({
  net, maxAbsNet,
}: { net: number | null; maxAbsNet: number }) {
  const w = net !== null && maxAbsNet > 0 ? Math.min(100, (Math.abs(net) / maxAbsNet) * 100) : 0;
  const win = net !== null && net > 0;
  const loss = net !== null && net < 0;
  return (
    <span className="inline-flex w-24 h-2 rounded bg-white/[0.06] overflow-hidden" aria-hidden="true">
      <span className="w-1/2 flex justify-end">
        <span className="h-full rounded-l bg-neg/70" style={{ width: `${loss ? w : 0}%` }} />
      </span>
      <span className="w-1/2 flex justify-start">
        <span className="h-full rounded-r bg-pos/70" style={{ width: `${win ? w : 0}%` }} />
      </span>
    </span>
  );
}
