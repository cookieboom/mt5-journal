import { useCallback, useEffect, useRef, useState } from "react";
import { BLOB_VERSION, parseDrawings, type Drawing } from "../lib/drawings";

const DEBOUNCE_MS = 400;

function url(symbol: string, sessionId: number | null): string {
  const q = new URLSearchParams({ symbol });
  if (sessionId !== null) q.set("session_id", String(sessionId));
  return `/api/drawings?${q.toString()}`;
}

// Drawings live only in the DB — unlike chart prefs there is no localStorage
// mirror, because a drawing belongs to the symbol (and to a replay session),
// not to the browser that happened to make it.
export function useDrawings(symbol: string, sessionId: number | null, enabled: boolean): {
  items: Drawing[];
  add: (d: Drawing) => void;
  update: (d: Drawing) => void;
  remove: (id: string) => void;
  clear: () => void;
} {
  const [items, setItems] = useState<Drawing[]>([]);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const target = useRef(url(symbol, sessionId));
  target.current = url(symbol, sessionId);

  useEffect(() => {
    if (!enabled) return;
    let alive = true;
    setItems([]);                       // never show one symbol's drawings on another
    fetch(url(symbol, sessionId))
      .then((r) => (r.ok ? r.json() : null))
      .then((body: { drawings: unknown } | null) => {
        if (!alive || !body) return;
        setItems(parseDrawings(body.drawings));
      })
      .catch(() => { /* offline — start empty, mutations still work locally */ });
    return () => { alive = false; };
  }, [symbol, sessionId, enabled]);

  // One debounced PUT per burst of edits. A dropped PUT loses at most the last
  // edit; in-memory state stays correct until reload.
  const schedule = useCallback((next: Drawing[]) => {
    if (timer.current) clearTimeout(timer.current);
    const to = target.current;
    timer.current = setTimeout(() => {
      void fetch(to, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ v: BLOB_VERSION, items: next }),
      }).catch(() => { /* offline — annotations only */ });
    }, DEBOUNCE_MS);
  }, []);

  const apply = useCallback((fn: (prev: Drawing[]) => Drawing[]) => {
    setItems((prev) => {
      const next = fn(prev);
      schedule(next);
      return next;
    });
  }, [schedule]);

  const add = useCallback((d: Drawing) => apply((prev) => [...prev, d]), [apply]);
  const update = useCallback(
    (d: Drawing) => apply((prev) => prev.map((x) => (x.id === d.id ? d : x))), [apply],
  );
  const remove = useCallback(
    (id: string) => apply((prev) => prev.filter((x) => x.id !== id)), [apply],
  );
  const clear = useCallback(() => apply(() => []), [apply]);

  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);

  return { items, add, update, remove, clear };
}
