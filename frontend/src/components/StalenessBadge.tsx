import { Live } from "../lib/types";

export default function StalenessBadge({ live }: { live: Live }) {
  if (live.empty)
    return <span className="text-[11px] text-muted px-2.5 py-1 rounded-full bg-white/5">
      tak ada posisi — atau <code>journal live</code> belum jalan
    </span>;
  const stale = live.stale;
  return (
    <span className={"text-[11px] px-2.5 py-1 rounded-full flex items-center gap-1.5 " +
      (stale ? "text-neg bg-neg/10 ring-1 ring-neg/25" : "text-cyan bg-cyan/10 ring-1 ring-cyan/25")}>
      <span className={"w-1.5 h-1.5 rounded-full " + (stale ? "bg-neg" : "bg-cyan shadow-[0_0_8px_#22d3ee]")} />
      {stale ? `basi · ${live.age_s}s — journal live mungkin mati` : `live · ${live.age_s}s`}
    </span>
  );
}
