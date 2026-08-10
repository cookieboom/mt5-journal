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
  // Mirrors `items` synchronously (updated both on every render AND inline by
  // `apply`, below) so mutation handlers can read the latest value without
  // going through a setState updater function — StrictMode double-invokes an
  // updater function passed straight to setState, which is exactly the hazard
  // useDrawingGesture's own comments spend six lines avoiding.
  const itemsRef = useRef<Drawing[]>(items);
  itemsRef.current = items;
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
    // Reset BEFORE the enabled guard: disabling (e.g. a replay session still
    // starting — see Chart.tsx's drawingsReady) must clear whatever the
    // previous key showed, not leave it on screen (and editable) under the
    // new, wrong key.
    dirty.current = false;
    setItems([]);                       // never show one symbol's drawings on another
    if (!enabled) return;
    let alive = true;
    fetch(url(symbol, sessionId))
      .then((r) => (r.ok ? r.json() : null))
      .then((body: { drawings: unknown } | null) => {
        if (!alive || !body || dirty.current) return;
        setItems(parseDrawings(body.drawings));
      })
      .catch(() => { /* offline — start empty, mutations still work locally */ });
    return () => { alive = false; };
  }, [symbol, sessionId, enabled]);

  const doPut = useCallback((to: string, next: Drawing[], keepalive = false) => {
    void fetch(to, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ v: BLOB_VERSION, items: next }),
      keepalive,
    }).then((r) => {
      if (!r.ok) console.warn(`drawings PUT to ${to} failed with status ${r.status}`);
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

  // The side effect (schedule) runs in the handler body, against a ref, never
  // inside the setItems updater — a plain-value setItems call can't be
  // double-invoked by StrictMode, so there is nothing to guard against here.
  const apply = useCallback((fn: (prev: Drawing[]) => Drawing[]) => {
    dirty.current = true;
    const next = fn(itemsRef.current);
    itemsRef.current = next;
    setItems(next);
    schedule(next);
  }, [schedule]);

  const add = useCallback((d: Drawing) => apply((prev) => [...prev, d]), [apply]);
  const update = useCallback(
    (d: Drawing) => apply((prev) => prev.map((x) => (x.id === d.id ? d : x))), [apply],
  );
  const remove = useCallback(
    (id: string) => apply((prev) => prev.filter((x) => x.id !== id)), [apply],
  );
  const clear = useCallback(() => apply(() => []), [apply]);

  // A pending debounced write must survive unmount (navigating away from
  // /chart inside the 400ms window is the common case, not an edge case) —
  // flush it immediately instead of dropping it on the floor. keepalive lets
  // the request outlive a real tab close, same guarantee a real navigation
  // needs less of but doesn't hurt.
  useEffect(() => () => {
    if (timer.current) {
      clearTimeout(timer.current);
      if (pending.current) doPut(pending.current.to, pending.current.next, true);
    }
  }, [doPut]);

  return { items, add, update, remove, clear };
}
