// Pure parse helper for trade-command inputs (SL/TP/volume fields on the
// live position card). This protects the trade-command money path:
//   blank/whitespace-only -> null  (leave unchanged)
//   "0"                   -> 0     (clear the field, e.g. remove SL/TP)
//   invalid / non-finite  -> NaN   (sentinel; callers MUST check and refuse
//                                   to submit — JSON.stringify(NaN) === "null"
//                                   and the server would silently read a typo
//                                   as "leave unchanged" instead of rejecting it)
export function optNum(s: string): number | null {
  const t = s.trim();
  if (t === "") return null;
  const n = Number(t);
  return Number.isFinite(n) ? n : NaN;
}
