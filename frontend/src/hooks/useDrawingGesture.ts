import { useEffect, useRef, useState } from "react";
import {
  DRAW_IDLE, drawReducer, hitTest, moveDrawing, newDrawing,
  type Anchor, type DrawState, type Drawing, type Projected, type Tool,
} from "../lib/drawings";

export interface DrawingGestureOpts {
  node: HTMLElement | null;
  enabled: boolean;
  tool: Tool;
  items: Drawing[];
  projected: Projected[];
  toAnchor: (x: number, y: number) => Anchor | null;
  // True when something with a stronger claim owns this press — an SL/TP price
  // line, or the second down of a measure double-click-hold. The gesture then
  // stands aside instead of consuming the event.
  reserved: (x: number, y: number, e: PointerEvent) => boolean;
  onAdd: (d: Drawing) => void;
  onUpdate: (d: Drawing) => void;
  onDelete: (id: string) => void;
  onToolDone: () => void;
  suppressPan: (off: boolean) => void;
  // Called when a NEW object finishes drawing, or an EXISTING one finishes an
  // actual drag (not a plain select-click) — see the comment at both call
  // sites in onUp for why a no-move grab/release must NOT call this.
  clearMeasureSeed: () => void;
}

// Owns every pointer/key listener for drawing. Listens in the CAPTURE phase and
// stops propagation only for presses it actually consumes, so CandleChart's own
// measure and SL/TP handlers keep working untouched for everything else.
export function useDrawingGesture(o: DrawingGestureOpts): {
  state: DrawState;
  draft: Drawing | null;
  selectedId: string | null;
} {
  // stateRef is the single source of truth; React's state exists only to
  // trigger re-renders. Every state read below goes through stateRef (never
  // the React `state` binding, which lags a render behind at event time).
  const stateRef = useRef<DrawState>(DRAW_IDLE);
  const [state, setReactState] = useState<DrawState>(DRAW_IDLE);
  const opts = useRef(o);
  opts.current = o;

  // Resolves an updater synchronously against stateRef and always hands React
  // a plain value, never a function. That's what keeps every transition here
  // side-effect-free from React's point of view: StrictMode double-invokes an
  // updater FUNCTION passed straight to its setState (by design, to surface
  // impure updaters) — passing a plain value sidesteps that, so callers do
  // side effects (onAdd/onUpdate/onDelete/onToolDone/suppressPan) around this
  // call, in the handler, never inside a function given to it.
  const setState = (next: DrawState | ((s: DrawState) => DrawState)) => {
    const resolved = typeof next === "function"
      ? (next as (s: DrawState) => DrawState)(stateRef.current)
      : next;
    stateRef.current = resolved;
    setReactState(resolved);
  };

  useEffect(() => {
    const node = o.node;
    if (!node || !o.enabled) return;

    const rel = (e: PointerEvent) => {
      const r = node.getBoundingClientRect();
      return { x: e.clientX - r.left, y: e.clientY - r.top };
    };

    const onDown = (e: PointerEvent) => {
      const { tool, projected, toAnchor, reserved, suppressPan } = opts.current;
      const { x, y } = rel(e);

      if (tool !== "cursor") {
        const at = toAnchor(x, y);
        if (!at) return;
        suppressPan(true);
        setState(drawReducer(DRAW_IDLE, {
          t: "begin", draft: newDrawing(tool, crypto.randomUUID(), at),
        }));
        e.preventDefault();
        e.stopPropagation();
        return;
      }

      if (reserved(x, y, e)) return;         // SL/TP or measure owns this press

      const hit = hitTest(projected, { x, y });
      if (!hit) { setState(DRAW_IDLE); return; }
      const at = toAnchor(x, y);
      if (!at) return;
      suppressPan(true);
      setState(drawReducer(DRAW_IDLE, { t: "grab", id: hit.id, handle: hit.handle, at }));
      e.preventDefault();
      e.stopPropagation();
    };

    const onMove = (e: PointerEvent) => {
      setState((s) => {
        if (s.phase !== "drawing" && s.phase !== "dragging") return s;
        const at = opts.current.toAnchor(rel(e).x, rel(e).y);
        return at ? drawReducer(s, { t: "move", at }) : s;
      });
    };

    // Reads the state snapshot once, runs side effects against it, then
    // commits — never inside the setState call itself (see the note above).
    const onUp = () => {
      const s = stateRef.current;
      const { items, onAdd, onUpdate, onToolDone, suppressPan, clearMeasureSeed } = opts.current;
      if (s.phase === "drawing") {
        suppressPan(false);
        onToolDone();
        // A press with no drag leaves both anchors coincident — that is a
        // stray click, not an object.
        if (isDegenerate(s.draft)) { setState(DRAW_IDLE); return; }
        onAdd(s.draft);
        // A NEW drawing's release lands exactly where the user is most likely
        // to press next (to nudge the endpoint they just placed) — CandleChart
        // records every pointerup as a potential double-click-hold seed
        // regardless of source, so without this a "draw it, then nudge it"
        // flow gets misread as the second half of a measure double-click.
        // Deliberately NOT called for a no-move grab/release below — that
        // exact shape (press+release, then a second nearby press) is what the
        // measure-priority test relies on to trigger a real double-click-hold.
        clearMeasureSeed();
        setState(drawReducer(s, { t: "commit" }));
        return;
      }
      if (s.phase === "dragging") {
        suppressPan(false);
        const target = items.find((d) => d.id === s.id);
        if (target && s.at) {
          onUpdate(moveDrawing(target, s.handle, s.from, s.at));
          // Same reasoning as the draw-commit branch above: an actual drag
          // (not a plain select-click, which leaves `s.at` unset) ends where
          // the user may want to grab again next.
          clearMeasureSeed();
        }
        setState(drawReducer(s, { t: "commit" }));
      }
    };

    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        opts.current.suppressPan(false);
        setState(DRAW_IDLE);
        return;
      }
      if (e.key === "Delete" || e.key === "Backspace") {
        // Never eat a Delete/Backspace meant for a text field elsewhere on the
        // page (lot-size input, replay-config dialog, Task 10's own text-tool
        // input) — onDelete is the persisting path, so a false hit here is
        // real data loss, not a UI glitch.
        if (isEditableTarget(e.target)) return;
        const s = stateRef.current;
        if (s.phase !== "selected") return;
        opts.current.onDelete(s.id);
        setState(DRAW_IDLE);
      }
    };

    const onCancel = () => { opts.current.suppressPan(false); setState(DRAW_IDLE); };

    node.addEventListener("pointerdown", onDown, true);   // capture
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    window.addEventListener("keydown", onKey);
    window.addEventListener("pointercancel", onCancel);
    return () => {
      node.removeEventListener("pointerdown", onDown, true);
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("pointercancel", onCancel);
      // Don't leave pan/zoom suppressed if this effect tears down mid-drag
      // (enabled/node changing) while the chart stays mounted.
      opts.current.suppressPan(false);
    };
  }, [o.node, o.enabled]);

  const draft = state.phase === "drawing" ? state.draft : null;
  const selectedId = state.phase === "selected" ? state.id
    : state.phase === "dragging" ? state.id : null;
  return { state, draft, selectedId };
}

function isDegenerate(d: Drawing): boolean {
  if (d.kind === "hline") return false;
  if (d.kind === "text") return d.text.length === 0;
  return Math.abs(d.a.timeMs - d.b.timeMs) < 1
    && Math.abs(d.a.price - d.b.price) < 1e-9;
}

function isEditableTarget(t: EventTarget | null): boolean {
  if (!(t instanceof HTMLElement)) return false;
  return t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable;
}
