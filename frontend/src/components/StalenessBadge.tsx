import { Live } from "../lib/types";
import StatusPill from "./StatusPill";

export default function StalenessBadge({ live }: { live: Live }) {
  if (live.empty)
    return (
      <StatusPill tone="absent">
        tak ada posisi — atau <code>journal live</code> belum jalan
      </StatusPill>
    );
  return (
    <StatusPill tone={live.stale ? "wrong" : "now"}>
      {live.stale ? `basi · ${live.age_s}s — journal live mungkin mati` : `live · ${live.age_s}s`}
    </StatusPill>
  );
}
