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
}

// Owns every pointer/key listener for drawing. Listens in the CAPTURE phase and
// stops propagation only for presses it actually consumes, so CandleChart's own
// measure and SL/TP handlers keep working untouched for everything else.
export function useDrawingGesture(o: DrawingGestureOpts): {
  state: DrawState;
  draft: Drawing | null;
  selectedId: string | null;
} {
  const [state, setState] = useState<DrawState>(DRAW_IDLE);
  const opts = useRef(o);
  opts.current = o;

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

    const onUp = () => {
      setState((s) => {
        const { items, onAdd, onUpdate, onToolDone, suppressPan } = opts.current;
        if (s.phase === "drawing") {
          suppressPan(false);
          onToolDone();
          // A press with no drag leaves both anchors coincident — that is a
          // stray click, not an object.
          if (isDegenerate(s.draft)) return DRAW_IDLE;
          onAdd(s.draft);
          return drawReducer(s, { t: "commit" });
        }
        if (s.phase === "dragging") {
          suppressPan(false);
          const target = items.find((d) => d.id === s.id);
          if (target && s.at) onUpdate(moveDrawing(target, s.handle, s.from, s.at));
          return drawReducer(s, { t: "commit" });
        }
        return s;
      });
    };

    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        opts.current.suppressPan(false);
        setState(DRAW_IDLE);
        return;
      }
      if (e.key === "Delete" || e.key === "Backspace") {
        setState((s) => {
          if (s.phase !== "selected") return s;
          opts.current.onDelete(s.id);
          return DRAW_IDLE;
        });
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
