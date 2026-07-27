import type { LiveStatus } from "../lib/types";

export default function LiveDot({ status }: { status: LiveStatus | null }) {
  const live = !!status?.live;
  const ageS = status?.age_ms != null ? Math.round(status.age_ms / 1000) : null;
  if (!live) {
    return (
      <span className="text-[11px] text-neg bg-neg/10 ring-1 ring-neg/25 px-2.5 py-1 rounded-full flex items-center gap-1.5">
        <span className="w-1.5 h-1.5 rounded-full bg-neg" />
        tak live — jalankan <code>journal live</code>
      </span>
    );
  }
  return (
    <span className="text-[11px] text-cyan bg-cyan/10 ring-1 ring-cyan/25 px-2.5 py-1 rounded-full flex items-center gap-1.5">
      <span className="w-1.5 h-1.5 rounded-full bg-cyan shadow-[0_0_8px_#22d3ee]" />
      live{ageS != null ? ` · ${ageS}s` : ""}
    </span>
  );
}
