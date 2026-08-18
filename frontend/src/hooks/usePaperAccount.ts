import { useCallback, useEffect, useState } from "react";
import { getAccount } from "../lib/paperApi";
import type { PaperAccountView } from "../lib/types";

/** The selected paper account, polled at the same 2500 ms as /api/live. A null
 *  id fetches NOTHING: an unselected account has no cost.
 *
 *  Not `useApi` — its `path` is a required string with no skip mode, so routing a
 *  null id through it would fetch `/api/paper/accounts/null` every 2.5 seconds
 *  forever while paper is switched off. */
export function usePaperAccount(accountId: number | null, pollMs = 2500) {
  const [view, setView] = useState<PaperAccountView | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    if (accountId == null) { setView(null); return; }
    try {
      setView(await getAccount(accountId));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [accountId]);

  useEffect(() => {
    if (accountId == null) { setView(null); return; }
    void refresh();
    const t = setInterval(() => void refresh(), pollMs);
    return () => clearInterval(t);
  }, [accountId, pollMs, refresh]);

  return { view, error, refresh };
}
