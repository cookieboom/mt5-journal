// Which indicator scripts are attached to the chart, with their inputs. One
// layout for live and replay alike, so training sees exactly the live setup.
// DB-only (like drawings): GET once, PUT debounced.
import { useCallback, useEffect, useRef, useState } from "react";
import { EMPTY_LAYOUT, indicatorsApi, normalizeLayout, type IndicatorLayout } from "../lib/indicators";

const DEBOUNCE_MS = 400;

export function useIndicatorLayout(): {
  layout: IndicatorLayout;
  setLayout: (next: IndicatorLayout) => void;
} {
  const [layout, setState] = useState<IndicatorLayout>(EMPTY_LAYOUT);
  const dirty = useRef(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pending = useRef<IndicatorLayout | null>(null);

  useEffect(() => {
    let alive = true;
    void indicatorsApi.getLayout().then((r) => {
      // A slow GET must not clobber an edit made while it was in flight.
      if (alive && r.ok && r.data && !dirty.current) setState(normalizeLayout(r.data.layout));
    });
    return () => { alive = false; };
  }, []);

  const flush = useCallback(() => {
    if (timer.current) { clearTimeout(timer.current); timer.current = null; }
    if (pending.current) { void indicatorsApi.putLayout(pending.current); pending.current = null; }
  }, []);
  useEffect(() => flush, [flush]);   // leaving the page still saves

  const setLayout = useCallback((next: IndicatorLayout) => {
    dirty.current = true;
    setState(next);
    pending.current = next;
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(flush, DEBOUNCE_MS);
  }, [flush]);

  return { layout, setLayout };
}
