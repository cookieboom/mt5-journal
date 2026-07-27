import { useApi } from "../lib/api";
import type { LiveStatus } from "../lib/types";

export function useLiveStatus(pollMs = 5000) {
  const { data } = useApi<LiveStatus>("/api/live-status", pollMs);
  return { status: data };
}
