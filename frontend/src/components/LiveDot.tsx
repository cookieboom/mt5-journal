import type { LiveStatus } from "../lib/types";
import StatusPill from "./StatusPill";

export default function LiveDot({ status }: { status: LiveStatus | null }) {
  const live = !!status?.live;
  const ageS = status?.age_ms != null ? Math.round(status.age_ms / 1000) : null;
  if (!live) {
    return (
      <StatusPill tone="wrong">
        tak live — jalankan <code>journal live</code>
      </StatusPill>
    );
  }
  return <StatusPill tone="now">live{ageS != null ? ` · ${ageS}s` : ""}</StatusPill>;
}
