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

  // Set the moment a local mutation happens, cleared at the start of each new
  // load. Guards against a slow initial GET clobbering an edit the user made
  // while it was still in flight (see the GET .then below).
  const dirty = useRef(false);
  // The write a live debounce timer is targeting, so a target change (symbol
  // or session switch) can flush it instead of silently cancelling it.
  const pending = useRef<{ to: string; next: Drawing[] } | null>(null);

  useEffect(() => {
    if (!enabled) return;
    let alive = true;
    dirty.current = false;
    setItems([]);                       // never show one symbol's drawings on another
    fetch(url(symbol, sessionId))
      .then((r) => (r.ok ? r.json() : null))
      .then((body: { drawings: unknown } | null) => {
        if (!alive || !body || dirty.current) return;
        setItems(parseDrawings(body.drawings));
      })
      .catch(() => { /* offline — start empty, mutations still work locally */ });
    return () => { alive = false; };
  }, [symbol, sessionId, enabled]);

  const doPut = useCallback((to: string, next: Drawing[]) => {
    void fetch(to, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ v: BLOB_VERSION, items: next }),
    }).catch(() => { /* offline — annotations only */ });
  }, []);

  // One debounced PUT per burst of edits. If the target (symbol/session)
  // changes while a write is still pending, that write is flushed to its own
  // (captured) target immediately rather than cancelled — a burst on the NEW
  // target must not silently drop the OLD target's last edit.
  const schedule = useCallback((next: Drawing[]) => {
    const to = target.current;
    if (pending.current && pending.current.to !== to) {
      if (timer.current) clearTimeout(timer.current);
      doPut(pending.current.to, pending.current.next);
    } else if (timer.current) {
      clearTimeout(timer.current);
    }
    pending.current = { to, next };
    timer.current = setTimeout(() => {
      doPut(to, next);
      pending.current = null;
      timer.current = null;
    }, DEBOUNCE_MS);
  }, [doPut]);

  const apply = useCallback((fn: (prev: Drawing[]) => Drawing[]) => {
    dirty.current = true;
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
