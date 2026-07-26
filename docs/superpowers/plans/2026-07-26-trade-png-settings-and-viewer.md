# Trade PNG Settings + Interactive Trade Viewer — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the trade PNG customizable via global settings (#2), and add an interactive `/trades/:id/view` viewer with filter-aware prev/next, overlay, optional playback reveal, and manual tag/annotation editing (#3).

**Architecture:** Part 1 threads a `RenderOpts` dataclass through the existing pure-DB mplfinance renderer, persisted as an `app_prefs` key `trade_png` (same pattern as `chart`/`replay` prefs), with the cache PNG filename keyed by an opts signature so charts stay cache-not-data (rule 6). Part 2 is a new React page that **reuses** `CandleChart`, `useChartData` (anchored loader), `clipToCursor` (playback), and the existing `AnnotationForm`/`TagEditor`; navigation re-queries `/api/trades` with the filter query-string to stay in sync with the Trades list.

**Tech Stack:** Python 3.12, sqlite3 (stdlib), mplfinance, FastAPI, pytest · React + TypeScript, lightweight-charts, react-router-dom, vitest.

## Global Constraints

- **Money is USC** (account currency). Never print a bare `$`; format via `money()` with `header.currency`. Prefer **R-multiple** (unit-free) in stats.
- **`NULL` = unknown, `0` = none set** (rule 4). SL/TP/R unknown → render `—`/`unknown`/`n/a`, never `0`. Guard SL/TP drawing on `value is not None and abs(value) > 1e-9` (never `is not None` alone).
- **Charts are cache, reproducible from the DB** (rule 6). The DB must never depend on a rendered file; the PNG filename carries an opts signature.
- **Web never touches the bridge** (M9 boundary). Candles come from `/api/candles`, which serves the DB and enqueues fills; the viewer only reads it.
- **No new dependencies** (rule 8). Reuse existing libraries only.
- **Tests before implementation** for `render/` and any domain logic (rule 7). Paste real pytest/vitest output.
- **Timeframes cross as strings** from `adapter.base.TIMEFRAMES`; no MT5 constants in `render/`.
- Definition of done: pytest + vitest green (output pasted), `journal rebuild` succeeds, `npm run build` clean, `graphify update .` run at end.

## File structure

**Part 1 (backend):**
- Modify `src/journal/render/chart.py` — add `RenderOpts`, `normalize_opts`, `window_for(pad_bars=…)`, thread opts through `render_trade`, opts-signature cache filename.
- Modify `src/journal/store/prefs_store.py` — `TRADE_PNG_KEY` + `get/set_trade_png_prefs`.
- Modify `src/journal/web/app.py` — `/api/trades/png-prefs` GET/PUT; thread opts into `/trades/{id}/chart.png`.
- Tests: `tests/test_chart.py`, `tests/test_prefs_store.py`, `tests/test_api.py` (or `test_web.py`).

**Part 1 (frontend):**
- Create `frontend/src/lib/tradePngPrefs.ts` — settings type, defaults, `normalizeTradePng`, `clampPad`.
- Create `frontend/src/hooks/useTradePngPrefs.ts` — GET-on-mount, debounced PUT, `version` bump.
- Create `frontend/src/components/TradePngPanel.tsx` — collapsible settings panel.
- Modify `frontend/src/pages/TradeDetail.tsx` — mount panel above the `<img>`, cache-bust the image, add "Lihat di chart" button.
- Tests: `frontend/src/lib/tradePngPrefs.test.ts`, `frontend/src/components/TradePngPanel.test.tsx`.

**Part 2 (frontend):**
- Modify `src/journal/web/views.py` + `frontend/src/lib/types.ts` — expose raw `symbol` on the trade-detail payload.
- Create `frontend/src/lib/tradeView.ts` — `tradeLines(trade)`, `navNeighbors(trades, id)`.
- Create `frontend/src/pages/TradeView.tsx` — the viewer page.
- Modify `frontend/src/App.tsx` — register `/trades/:id/view`.
- Tests: `frontend/src/lib/tradeView.test.ts`, `frontend/src/pages/TradeView.test.tsx`, `tests/test_api.py` (symbol field).

---

# Part 1 — Trade PNG Render Settings (#2)

### Task 1: `RenderOpts` + normalize + parametrized `window_for`

**Files:**
- Modify: `src/journal/render/chart.py`
- Test: `tests/test_chart.py`

**Interfaces:**
- Produces: `RenderOpts(theme: str="charles", pad_bars: int=15, tf_override: str|None=None, show_sltp: bool=True, show_markers: bool=True, show_volume: bool=False, show_grid: bool=True)` with `.signature() -> str`; `normalize_opts(raw: dict|None) -> RenderOpts`; `window_for(open_msc, close_msc, tf, pad_bars: int=PAD_BARS)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_chart.py — add
from journal.render.chart import (
    RenderOpts, normalize_opts, window_for, PAD_BARS,
)

def test_normalize_opts_defaults_and_clamps():
    assert normalize_opts(None) == RenderOpts()          # all defaults
    o = normalize_opts({"theme": "bogus", "pad_bars": 999, "tf_override": "M5",
                        "show_sltp": False, "show_volume": True})
    assert o.theme == "charles"        # unknown theme falls back
    assert o.pad_bars == 120           # clamped to [5,120]
    assert o.tf_override == "M5"
    assert o.show_sltp is False and o.show_volume is True
    assert normalize_opts({"pad_bars": 1}).pad_bars == 5
    assert normalize_opts({"tf_override": "ZZ"}).tf_override is None

def test_render_opts_signature_stable_and_sensitive():
    a = RenderOpts()
    assert a.signature() == RenderOpts().signature()          # stable
    assert a.signature() != RenderOpts(pad_bars=30).signature()  # sensitive

def test_window_for_pad_bars_widens_window():
    narrow = window_for(1_000_000, 2_000_000, "M1", pad_bars=5)
    wide = window_for(1_000_000, 2_000_000, "M1", pad_bars=30)
    assert wide[0] < narrow[0] and wide[1] > narrow[1]
    # default keeps PAD_BARS behavior
    assert window_for(1_000_000, 2_000_000, "M1") == window_for(
        1_000_000, 2_000_000, "M1", pad_bars=PAD_BARS)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_chart.py -k "normalize_opts or signature or pad_bars" -v`
Expected: FAIL with `ImportError`/`cannot import name 'RenderOpts'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/journal/render/chart.py
# add near the top imports:
import hashlib

# allowed mplfinance base styles (light default first)
THEMES: frozenset[str] = frozenset({"charles", "nightclouds", "yahoo"})
PAD_MIN, PAD_MAX = 5, 120

@dataclass(frozen=True)
class RenderOpts:
    theme: str = "charles"
    pad_bars: int = PAD_BARS
    tf_override: str | None = None
    show_sltp: bool = True
    show_markers: bool = True
    show_volume: bool = False
    show_grid: bool = True

    def signature(self) -> str:
        raw = (
            f"{self.theme}|{self.pad_bars}|{self.tf_override}|"
            f"{int(self.show_sltp)}{int(self.show_markers)}"
            f"{int(self.show_volume)}{int(self.show_grid)}"
        )
        return hashlib.sha1(raw.encode()).hexdigest()[:8]

def _clamp_pad(v: object) -> int:
    try:
        n = int(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return PAD_BARS
    return max(PAD_MIN, min(PAD_MAX, n))

def _b(v: object, default: bool) -> bool:
    return v if isinstance(v, bool) else default

def normalize_opts(raw: dict | None) -> RenderOpts:
    """Coerce a stored/DB blob (or None) into a valid RenderOpts. Unknown theme
    -> 'charles'; pad_bars clamped to [5,120]; tf_override must be a known
    timeframe else None; booleans keep defaults when absent/wrong-typed."""
    if not isinstance(raw, dict):
        return RenderOpts()
    theme = raw.get("theme")
    tf = raw.get("tf_override")
    return RenderOpts(
        theme=theme if theme in THEMES else "charles",
        pad_bars=_clamp_pad(raw.get("pad_bars")),
        tf_override=tf if tf in TIMEFRAMES else None,
        show_sltp=_b(raw.get("show_sltp"), True),
        show_markers=_b(raw.get("show_markers"), True),
        show_volume=_b(raw.get("show_volume"), False),
        show_grid=_b(raw.get("show_grid"), True),
    )
```

Change `window_for` to take `pad_bars`:

```python
def window_for(
    open_msc: int, close_msc: int, tf: str, pad_bars: int = PAD_BARS,
) -> tuple[int, int]:
    """+/- `pad_bars` bars of context around [open_msc, close_msc] at `tf`
    granularity. Epoch-ms, SERVER time (no zone conversion here)."""
    pad_ms = pad_bars * _TF_SECONDS[tf] * 1000
    return open_msc - pad_ms, close_msc + pad_ms
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_chart.py -k "normalize_opts or signature or pad_bars" -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/journal/render/chart.py tests/test_chart.py
git commit -m "feat(render): RenderOpts + normalize_opts + parametrized window_for"
```

---

### Task 2: `render_trade(*, opts=…)` honors every knob + opts-keyed cache filename

**Files:**
- Modify: `src/journal/render/chart.py:184-334`
- Test: `tests/test_chart.py`

**Interfaces:**
- Consumes: `RenderOpts`, `window_for(pad_bars=…)` (Task 1).
- Produces: `render_trade(conn, position_id, *, opts: RenderOpts = RenderOpts(), cache_dir=…, segment=0) -> ChartResult`. Cache filename: `f"{login}-{position_id}-seg{segment}-{opts.signature()}.png"`. `ChartResult` gains no new required fields (the existing `sl_drawn`/`tp_drawn` already reflect the toggles).

- [ ] **Step 1: Write the failing test**

Reuse whatever fixture `tests/test_chart.py` already uses to seed a closed trade + candles (find the existing `test_render_trade_writes_a_real_png` and copy its setup helper). Add:

```python
def test_render_trade_opts_toggle_sltp_and_cache_key(tmp_path, seeded_conn):
    # `seeded_conn` = the existing fixture/helper that inserts one closed trade
    # with a real sl_initial/tp_initial and candles (mirror the setup in
    # test_render_trade_writes_a_real_png).
    from journal.render.chart import render_trade, RenderOpts
    on = render_trade(seeded_conn, POSITION_ID, opts=RenderOpts(show_sltp=True),
                      cache_dir=tmp_path)
    off = render_trade(seeded_conn, POSITION_ID, opts=RenderOpts(show_sltp=False),
                       cache_dir=tmp_path)
    assert on.sl_drawn is True and on.tp_drawn is True
    assert off.sl_drawn is False and off.tp_drawn is False
    assert on.path != off.path            # opts change -> different cache file
    assert on.path.exists() and off.path.exists()

def test_render_trade_pad_bars_changes_bar_count(tmp_path, seeded_conn):
    from journal.render.chart import render_trade, RenderOpts
    narrow = render_trade(seeded_conn, POSITION_ID, opts=RenderOpts(pad_bars=5),
                          cache_dir=tmp_path)
    wide = render_trade(seeded_conn, POSITION_ID, opts=RenderOpts(pad_bars=15),
                        cache_dir=tmp_path)
    assert wide.n_bars >= narrow.n_bars   # more padding -> at least as many bars
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_chart.py -k "opts_toggle or pad_bars_changes" -v`
Expected: FAIL — `render_trade() got an unexpected keyword argument 'opts'`.

- [ ] **Step 3: Write minimal implementation**

In `render_trade`, replace the signature and the constant-driven body. Key edits:

```python
def render_trade(
    conn: sqlite3.Connection,
    position_id: int,
    *,
    opts: RenderOpts = RenderOpts(),
    cache_dir: str | Path = "cache",
    segment: int = 0,
) -> ChartResult:
    ...
    chosen_tf = opts.tf_override or choose_timeframe(duration_s)
    if chosen_tf not in _TF_SECONDS:
        raise ValueError(f"unknown timeframe {chosen_tf!r}; expected one of {TIMEFRAMES}")

    from_msc, to_msc = window_for(
        trade["open_time_msc"], trade["close_time_msc"], chosen_tf, opts.pad_bars,
    )
    ...
    # markers only when enabled
    addplots = []
    if opts.show_markers:
        addplots = [
            mpf.make_addplot(entry_marker, type="scatter", markersize=100,
                             marker="^" if is_buy else "v", color="blue"),
            mpf.make_addplot(exit_marker, type="scatter", markersize=100,
                             marker="v" if is_buy else "^", color="darkorange"),
        ]
    ...
    # SL/TP: value-guard AND the toggle (rule 4 guard unchanged)
    sl = trade["sl_initial"]; tp = trade["tp_initial"]
    sl_drawn = opts.show_sltp and sl is not None and abs(sl) > _TOL
    tp_drawn = opts.show_sltp and tp is not None and abs(tp) > _TOL
    ...
    out_path = cache_dir / f"{login}-{position_id}-seg{segment}-{opts.signature()}.png"

    style = mpf.make_mpf_style(base_mpf_style=opts.theme,
                               rc={"axes.grid": opts.show_grid})
    plot_kwargs: dict = dict(
        type="candle", style=style,
        addplot=addplots if addplots else None,
        title=title, volume=opts.show_volume,
        savefig=dict(fname=str(out_path), dpi=150),
    )
    if not addplots:
        plot_kwargs.pop("addplot")
    if hlines_prices:
        plot_kwargs["hlines"] = dict(hlines=hlines_prices, colors=hlines_colors,
                                     linestyle="--", linewidths=1)
    mpf.plot(df, **plot_kwargs)
```

Leave `ChartResult` fields as-is. Note: `volume=opts.show_volume` requires the `df` `Volume` column, which is already built.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_chart.py -v`
Expected: PASS (existing tests + the two new ones). If an existing test asserted a filename without the signature suffix, update it to `.endswith(".png")` / match the new pattern.

- [ ] **Step 5: Commit**

```bash
git add src/journal/render/chart.py tests/test_chart.py
git commit -m "feat(render): render_trade honors RenderOpts + opts-keyed cache filename"
```

---

### Task 3: `trade_png` prefs store + routes + endpoint wiring

**Files:**
- Modify: `src/journal/store/prefs_store.py`
- Modify: `src/journal/web/app.py:181-196` (add routes near chart prefs), `:310-321` (chart.png endpoint)
- Test: `tests/test_prefs_store.py`, `tests/test_api.py` (or `tests/test_web.py`)

**Interfaces:**
- Produces: `prefs_store.TRADE_PNG_KEY="trade_png"`, `get_trade_png_prefs(conn) -> Any|None`, `set_trade_png_prefs(conn, prefs) -> int`; routes `GET/PUT /api/trades/png-prefs`; `/trades/{id}/chart.png` renders with `normalize_opts(get_trade_png_prefs(conn))`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_prefs_store.py — add
def test_trade_png_prefs_roundtrip(conn):   # reuse the module's `conn` fixture
    from journal.store import prefs_store
    assert prefs_store.get_trade_png_prefs(conn) is None
    prefs_store.set_trade_png_prefs(conn, {"theme": "nightclouds", "pad_bars": 40})
    assert prefs_store.get_trade_png_prefs(conn) == {"theme": "nightclouds", "pad_bars": 40}
```

```python
# tests/test_api.py — add (reuse the app/client fixture used by other route tests)
def test_trade_png_prefs_route_roundtrip(client):
    assert client.get("/api/trades/png-prefs").json() == {"prefs": None}
    r = client.put("/api/trades/png-prefs", json={"theme": "yahoo", "pad_bars": 20})
    assert r.json()["ok"] is True
    assert client.get("/api/trades/png-prefs").json()["prefs"] == {"theme": "yahoo", "pad_bars": 20}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_prefs_store.py -k trade_png tests/test_api.py -k png_prefs -v`
Expected: FAIL — attribute/route missing (404 on the route).

- [ ] **Step 3: Write minimal implementation**

```python
# src/journal/store/prefs_store.py — add
TRADE_PNG_KEY = "trade_png"

def get_trade_png_prefs(conn: sqlite3.Connection) -> Any | None:
    """Parsed trade-PNG render settings JSON, or None if never saved."""
    raw = get_pref(conn, TRADE_PNG_KEY)
    return json.loads(raw) if raw is not None else None

def set_trade_png_prefs(conn: sqlite3.Connection, prefs: Any) -> int:
    """Persist trade-PNG render settings (serialised to JSON). Returns updated_ms."""
    return set_pref(conn, TRADE_PNG_KEY, json.dumps(prefs), now_ms())
```

```python
# src/journal/web/app.py — add after api_put_chart_prefs (line ~196)
    @app.get("/api/trades/png-prefs")
    def api_get_trade_png_prefs(conn: sqlite3.Connection = Depends(get_conn)):
        """Global trade-PNG render settings, cross-browser. `prefs` null until
        first save. Pure DB (M9 boundary)."""
        return JSONResponse({"prefs": prefs_store.get_trade_png_prefs(conn)})

    @app.put("/api/trades/png-prefs")
    def api_put_trade_png_prefs(
        prefs=Body(...), conn: sqlite3.Connection = Depends(get_conn),
    ):
        """Upsert the trade-PNG settings blob under key 'trade_png'."""
        ts = prefs_store.set_trade_png_prefs(conn, prefs)
        return JSONResponse({"ok": True, "updated_ms": ts})
```

**Route ordering note:** FastAPI matches in declaration order. `/api/trades/png-prefs` MUST be declared BEFORE `/api/trades/{position_id}` (line 117) — otherwise `png-prefs` is captured as a `position_id` and 422s. Place the two new routes above the `@app.get("/api/trades/{position_id}")` handler.

Thread opts into the PNG endpoint:

```python
    @app.get("/trades/{position_id}/chart.png")
    def trade_chart(position_id: int, conn: sqlite3.Connection = Depends(get_conn)):
        from ..render.chart import normalize_opts   # local import keeps mpl lazy
        opts = normalize_opts(prefs_store.get_trade_png_prefs(conn))
        try:
            result = render_trade(conn, position_id, opts=opts, cache_dir=_CACHE_DIR)
        except (TradeNotFoundError, NoCandlesError, ValueError) as e:
            return Response(str(e), status_code=404, media_type="text/plain")
        except RuntimeError as e:
            return Response(str(e), status_code=400, media_type="text/plain")
        return FileResponse(result.path, media_type="image/png")
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_prefs_store.py tests/test_api.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/journal/store/prefs_store.py src/journal/web/app.py tests/test_prefs_store.py tests/test_api.py
git commit -m "feat(web): trade_png prefs store + /api/trades/png-prefs routes + PNG endpoint reads opts"
```

---

### Task 4: Frontend `tradePngPrefs.ts` lib

**Files:**
- Create: `frontend/src/lib/tradePngPrefs.ts`
- Test: `frontend/src/lib/tradePngPrefs.test.ts`

**Interfaces:**
- Produces: `TradePngSettings` (theme, padBars, tfOverride, showSltp, showMarkers, showVolume, showGrid), `DEFAULT_TRADE_PNG`, `PAD_MIN=5`, `PAD_MAX=120`, `THEMES`, `TF_OPTIONS`, `normalizeTradePng(raw): TradePngSettings`, `toApi(s): object` / `fromApi(raw): TradePngSettings` (the DB blob uses snake_case matching the Python keys).

- [ ] **Step 1: Write the failing test**

```ts
// frontend/src/lib/tradePngPrefs.test.ts
import { describe, it, expect } from "vitest";
import { normalizeTradePng, DEFAULT_TRADE_PNG, toApi, fromApi } from "./tradePngPrefs";

describe("tradePngPrefs", () => {
  it("defaults on null/garbage", () => {
    expect(normalizeTradePng(null)).toEqual(DEFAULT_TRADE_PNG);
    expect(normalizeTradePng({ theme: "bogus" }).theme).toBe("charles");
  });
  it("clamps padBars and validates tf", () => {
    expect(normalizeTradePng({ padBars: 999 }).padBars).toBe(120);
    expect(normalizeTradePng({ padBars: 1 }).padBars).toBe(5);
    expect(normalizeTradePng({ tfOverride: "ZZ" }).tfOverride).toBeNull();
    expect(normalizeTradePng({ tfOverride: "M5" }).tfOverride).toBe("M5");
  });
  it("round-trips through the snake_case API shape", () => {
    const s = { ...DEFAULT_TRADE_PNG, theme: "nightclouds" as const, padBars: 40 };
    expect(fromApi(toApi(s))).toEqual(s);
    expect(toApi(s)).toMatchObject({ theme: "nightclouds", pad_bars: 40 });
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/lib/tradePngPrefs.test.ts`
Expected: FAIL — module not found.

- [ ] **Step 3: Write minimal implementation**

```ts
// frontend/src/lib/tradePngPrefs.ts
import { TIMEFRAMES, type Timeframe } from "./candles";

export type PngTheme = "charles" | "nightclouds" | "yahoo";
export const THEMES: PngTheme[] = ["charles", "nightclouds", "yahoo"];
export const TF_OPTIONS: (Timeframe | null)[] = [null, ...TIMEFRAMES];
export const PAD_MIN = 5, PAD_MAX = 120;

export interface TradePngSettings {
  theme: PngTheme;
  padBars: number;
  tfOverride: Timeframe | null;
  showSltp: boolean;
  showMarkers: boolean;
  showVolume: boolean;
  showGrid: boolean;
}

export const DEFAULT_TRADE_PNG: TradePngSettings = {
  theme: "charles", padBars: 15, tfOverride: null,
  showSltp: true, showMarkers: true, showVolume: false, showGrid: true,
};

const clampPad = (v: unknown): number => {
  const n = typeof v === "number" && Number.isFinite(v) ? Math.round(v) : DEFAULT_TRADE_PNG.padBars;
  return Math.min(PAD_MAX, Math.max(PAD_MIN, n));
};
const bool = (v: unknown, d: boolean) => (typeof v === "boolean" ? v : d);

// Accepts either camelCase (UI) or snake_case (DB blob) keys.
export function normalizeTradePng(raw: unknown): TradePngSettings {
  if (raw === null || typeof raw !== "object") return { ...DEFAULT_TRADE_PNG };
  const p = raw as Record<string, unknown>;
  const theme = p.theme;
  const tf = (p.tfOverride ?? p.tf_override) as unknown;
  return {
    theme: THEMES.includes(theme as PngTheme) ? (theme as PngTheme) : "charles",
    padBars: clampPad(p.padBars ?? p.pad_bars),
    tfOverride: (TIMEFRAMES as string[]).includes(tf as string) ? (tf as Timeframe) : null,
    showSltp: bool(p.showSltp ?? p.show_sltp, true),
    showMarkers: bool(p.showMarkers ?? p.show_markers, true),
    showVolume: bool(p.showVolume ?? p.show_volume, false),
    showGrid: bool(p.showGrid ?? p.show_grid, true),
  };
}

// DB blob uses snake_case matching the Python RenderOpts fields.
export function toApi(s: TradePngSettings): Record<string, unknown> {
  return {
    theme: s.theme, pad_bars: s.padBars, tf_override: s.tfOverride,
    show_sltp: s.showSltp, show_markers: s.showMarkers,
    show_volume: s.showVolume, show_grid: s.showGrid,
  };
}
export const fromApi = (raw: unknown): TradePngSettings => normalizeTradePng(raw);
```

- [ ] **Step 4: Run test**

Run: `cd frontend && npx vitest run src/lib/tradePngPrefs.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/tradePngPrefs.ts frontend/src/lib/tradePngPrefs.test.ts
git commit -m "feat(fe): tradePngPrefs lib (normalize/clamp + camel<->snake API mapping)"
```

---

### Task 5: `useTradePngPrefs` hook + `TradePngPanel` + wire into TradeDetail

**Files:**
- Create: `frontend/src/hooks/useTradePngPrefs.ts`
- Create: `frontend/src/components/TradePngPanel.tsx`
- Modify: `frontend/src/pages/TradeDetail.tsx:62-74`
- Test: `frontend/src/components/TradePngPanel.test.tsx`

**Interfaces:**
- Consumes: `tradePngPrefs.ts` (Task 4), routes `/api/trades/png-prefs` (Task 3).
- Produces: `useTradePngPrefs(): { settings, update(next), version }` where `version` bumps to the PUT's `updated_ms` on save (used as the `<img>` cache-buster). `TradePngPanel({ settings, onChange })`.

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/src/components/TradePngPanel.test.tsx
import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import TradePngPanel from "./TradePngPanel";
import { DEFAULT_TRADE_PNG } from "../lib/tradePngPrefs";

describe("TradePngPanel", () => {
  it("emits a clamped padBars and a theme change", () => {
    const onChange = vi.fn();
    render(<TradePngPanel settings={DEFAULT_TRADE_PNG} onChange={onChange} />);
    fireEvent.click(screen.getByRole("button", { name: /render settings/i }));
    fireEvent.change(screen.getByLabelText(/context bars/i), { target: { value: "999" } });
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ padBars: 120 }));
    fireEvent.change(screen.getByLabelText(/theme/i), { target: { value: "nightclouds" } });
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ theme: "nightclouds" }));
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/components/TradePngPanel.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Write minimal implementation**

```ts
// frontend/src/hooks/useTradePngPrefs.ts
import { useCallback, useEffect, useRef, useState } from "react";
import { DEFAULT_TRADE_PNG, fromApi, toApi, type TradePngSettings } from "../lib/tradePngPrefs";

const DEBOUNCE_MS = 400;

// DB is authoritative (the server renders the PNG from it). GET on mount, then
// debounced PUT on change; `version` (last updated_ms) busts the <img> cache.
export function useTradePngPrefs(): {
  settings: TradePngSettings; update: (n: TradePngSettings) => void; version: number;
} {
  const [settings, setSettings] = useState<TradePngSettings>(DEFAULT_TRADE_PNG);
  const [version, setVersion] = useState(0);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    let alive = true;
    fetch("/api/trades/png-prefs")
      .then((r) => (r.ok ? r.json() : null))
      .then((b: { prefs: unknown } | null) => {
        if (!alive || !b || b.prefs == null) return;
        setSettings(fromApi(b.prefs));
        setVersion((v) => v + 1);   // reflect the loaded value in the img key
      })
      .catch(() => { /* offline/dev — keep defaults */ });
    return () => { alive = false; };
  }, []);

  const update = useCallback((next: TradePngSettings) => {
    setSettings(next);
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => {
      fetch("/api/trades/png-prefs", {
        method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(toApi(next)),
      })
        .then((r) => (r.ok ? r.json() : null))
        .then((b: { updated_ms: number } | null) => { if (b) setVersion(b.updated_ms); })
        .catch(() => { /* offline/dev */ });
    }, DEBOUNCE_MS);
  }, []);

  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);
  return { settings, update, version };
}
```

```tsx
// frontend/src/components/TradePngPanel.tsx
import { useState } from "react";
import {
  THEMES, TF_OPTIONS, PAD_MIN, PAD_MAX, type TradePngSettings,
} from "../lib/tradePngPrefs";

export default function TradePngPanel(
  { settings, onChange }: { settings: TradePngSettings; onChange: (s: TradePngSettings) => void },
) {
  const [open, setOpen] = useState(false);
  const set = <K extends keyof TradePngSettings>(k: K, v: TradePngSettings[K]) =>
    onChange({ ...settings, [k]: v });
  const clampPad = (raw: string) => {
    const n = Number(raw);
    onChange({ ...settings, padBars: Math.min(PAD_MAX, Math.max(PAD_MIN, Math.round(n || PAD_MIN))) });
  };
  return (
    <div className="mb-2 text-[12px]">
      <button className="text-cyan hover:underline" onClick={() => setOpen((o) => !o)}>
        {open ? "▾" : "▸"} Render settings
      </button>
      {open && (
        <div className="glass mt-2 p-3 grid grid-cols-2 gap-2">
          <label className="flex flex-col gap-1">Theme
            <select aria-label="theme" className="bg-transparent border border-white/10 rounded px-1 py-0.5"
              value={settings.theme} onChange={(e) => set("theme", e.target.value as TradePngSettings["theme"])}>
              {THEMES.map((t) => <option key={t} value={t}>{t}</option>)}
            </select>
          </label>
          <label className="flex flex-col gap-1">Context bars ({PAD_MIN}–{PAD_MAX})
            <input aria-label="context bars" type="number" min={PAD_MIN} max={PAD_MAX}
              className="bg-transparent border border-white/10 rounded px-1 py-0.5"
              value={settings.padBars} onChange={(e) => clampPad(e.target.value)} />
          </label>
          <label className="flex flex-col gap-1">Timeframe
            <select aria-label="timeframe" className="bg-transparent border border-white/10 rounded px-1 py-0.5"
              value={settings.tfOverride ?? ""} onChange={(e) => set("tfOverride", (e.target.value || null) as TradePngSettings["tfOverride"])}>
              {TF_OPTIONS.map((t) => <option key={t ?? "auto"} value={t ?? ""}>{t ?? "Auto"}</option>)}
            </select>
          </label>
          <fieldset className="col-span-2 flex flex-wrap gap-3">
            {([["showSltp","SL/TP"],["showMarkers","Markers"],["showVolume","Volume"],["showGrid","Grid"]] as const).map(
              ([k, lbl]) => (
                <label key={k} className="flex items-center gap-1">
                  <input type="checkbox" checked={settings[k]} onChange={(e) => set(k, e.target.checked)} /> {lbl}
                </label>
              ))}
          </fieldset>
          <p className="col-span-2 text-muted">Berlaku untuk semua gambar trade.</p>
        </div>
      )}
    </div>
  );
}
```

Wire into `TradeDetail.tsx` — replace the Chart card body (lines ~62-74). Add near the top of the component: `const png = useTradePngPrefs();` (import both). Then:

```tsx
        <div className="glass p-4">
          <h2 className="text-[13px] font-semibold uppercase tracking-wider text-muted mb-2">Chart</h2>
          {chartable ? (
            <>
              <TradePngPanel settings={png.settings} onChange={png.update} />
              {chartFailed ? (
                <p className="text-[12px] text-muted">Chart belum tersedia — jalankan <code>uv run journal candles</code> lalu buka lagi.</p>
              ) : (
                <img className="w-full rounded" src={`/trades/${trade.position_id}/chart.png?v=${png.version}`}
                  alt={`chart trade ${trade.position_id}`} onError={() => setChartFailed(true)} />
              )}
              <a className="inline-block mt-2 text-[12px] text-cyan hover:underline"
                 href={`/trades/${trade.position_id}/view`}>Lihat di chart interaktif →</a>
            </>
          ) : (
            <p className="text-[12px] text-muted">Hanya trade closed yang bisa di-chart.</p>
          )}
        </div>
```

Also reset `chartFailed` when `png.version` changes so a re-render after a settings change re-attempts the image: change line 24 to `useEffect(() => setChartFailed(false), [id, png.version]);`.

- [ ] **Step 4: Run tests + build**

Run: `cd frontend && npx vitest run src/components/TradePngPanel.test.tsx && npm run build`
Expected: PASS, build clean.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/hooks/useTradePngPrefs.ts frontend/src/components/TradePngPanel.tsx frontend/src/components/TradePngPanel.test.tsx frontend/src/pages/TradeDetail.tsx
git commit -m "feat(fe): TradePngPanel + useTradePngPrefs; wire settings + cache-bust + view link into TradeDetail"
```

---

# Part 2 — Interactive Trade Viewer (#3)

### Task 6: Expose raw `symbol` on the trade-detail payload

The interactive chart fetches `/api/candles?symbol=…` with the **raw** symbol (e.g. `XAUUSDc`), but `TradeFull` currently exposes only `symbol_base`. Add `symbol`.

**Files:**
- Modify: `src/journal/web/views.py` (`trade_detail_context` — add `symbol` to the trade dict)
- Modify: `frontend/src/lib/types.ts:129-147` (`TradeFull.symbol: string`)
- Test: `tests/test_api.py`

**Interfaces:**
- Produces: `trade_detail_payload(...)["trade"]["symbol"]` = the raw broker symbol.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_api.py — add (reuse the fixture that seeds one closed XAUUSDc trade)
def test_trade_detail_exposes_raw_symbol(client):
    body = client.get(f"/api/trades/{POSITION_ID}").json()
    assert body["trade"]["symbol"] == "XAUUSDc"        # raw, suffixed
    assert body["trade"]["symbol_base"] == "XAUUSD"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_api.py -k raw_symbol -v`
Expected: FAIL — `KeyError: 'symbol'` (or assertion error).

- [ ] **Step 3: Write minimal implementation**

In `views.trade_detail_context`, find where the `trade` dict is assembled and add the raw column (the `trades` row already has `symbol`). Add `"symbol": row["symbol"],` next to `"symbol_base": row["symbol_base"],`.

```ts
// frontend/src/lib/types.ts — TradeFull, add after position_id
  symbol: string;        // raw, e.g. "XAUUSDc" — for the candle feed
```

- [ ] **Step 4: Run test**

Run: `uv run pytest tests/test_api.py -k raw_symbol -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/journal/web/views.py frontend/src/lib/types.ts tests/test_api.py
git commit -m "feat(web): expose raw symbol on trade-detail payload (for the interactive viewer)"
```

---

### Task 7: `tradeView.ts` helpers — overlay lines + prev/next neighbors

**Files:**
- Create: `frontend/src/lib/tradeView.ts`
- Test: `frontend/src/lib/tradeView.test.ts`

**Interfaces:**
- Consumes: `TradeFull`, `TradeRow`, `LINE_COLORS` from `candles.ts`.
- Produces: `tradeLines(t: TradeFull): {price:number;color:string;title:string}[]` (entry, exit, SL, TP — rule-4 guarded); `navNeighbors(trades: TradeRow[], id: number): { prevId: number|null; nextId: number|null; index: number }` (list is newest-open-first; "prev" = older trade = next index, "next" = newer = previous index — see mapping in code).

- [ ] **Step 1: Write the failing test**

```ts
// frontend/src/lib/tradeView.test.ts
import { describe, it, expect } from "vitest";
import { tradeLines, navNeighbors } from "./tradeView";
import type { TradeFull, TradeRow } from "./types";

const base: TradeFull = {
  position_id: 2, symbol: "XAUUSDc", symbol_base: "XAUUSD", direction: "buy",
  status: "closed", open_time_msc: 10, close_time_msc: 20, duration_s: 10,
  volume: 0.1, open_price: 4000, close_price: 4010, sl_initial: 3990,
  tp_initial: null, net_profit: 100, r_multiple: 1, mae_r: -0.2, mfe_r: 1.1, magic: null,
};

describe("tradeLines", () => {
  it("draws entry/exit/SL, skips null TP (rule 4)", () => {
    const titles = tradeLines(base).map((l) => l.title);
    expect(titles).toEqual(expect.arrayContaining(["entry", "exit", "SL"]));
    expect(titles).not.toContain("TP");
  });
  it("skips a 0.0 SL (none set, not a real price)", () => {
    expect(tradeLines({ ...base, sl_initial: 0 }).map((l) => l.title)).not.toContain("SL");
  });
});

describe("navNeighbors", () => {
  const rows = [3, 2, 1].map((position_id) => ({ position_id } as TradeRow)); // newest-first
  it("maps older=prev, newer=next", () => {
    expect(navNeighbors(rows, 2)).toEqual({ index: 1, prevId: 1, nextId: 3 });
    expect(navNeighbors(rows, 3)).toEqual({ index: 0, prevId: 2, nextId: null });
    expect(navNeighbors(rows, 1)).toEqual({ index: 2, prevId: null, nextId: 2 });
  });
  it("treats an unknown id as a singleton", () => {
    expect(navNeighbors(rows, 99)).toEqual({ index: -1, prevId: null, nextId: null });
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/lib/tradeView.test.ts`
Expected: FAIL — module not found.

- [ ] **Step 3: Write minimal implementation**

```ts
// frontend/src/lib/tradeView.ts
import { LINE_COLORS } from "./candles";
import type { TradeFull, TradeRow } from "./types";

const real = (v: number | null): v is number => v !== null && Math.abs(v) > 1e-9;

// Overlay price lines for the interactive viewer (mirrors liveLines/replayLines).
export function tradeLines(t: TradeFull): { price: number; color: string; title: string }[] {
  const out: { price: number; color: string; title: string }[] = [];
  if (real(t.open_price)) out.push({ price: t.open_price!, color: LINE_COLORS.entry, title: "entry" });
  if (real(t.close_price)) out.push({ price: t.close_price!, color: "#f59e0b", title: "exit" });
  if (real(t.sl_initial)) out.push({ price: t.sl_initial!, color: LINE_COLORS.sl, title: "SL" });
  if (real(t.tp_initial)) out.push({ price: t.tp_initial!, color: LINE_COLORS.tp, title: "TP" });
  return out;
}

// The list is newest-open-first (open_time_msc DESC). Visually "next" = newer
// trade (earlier index), "prev" = older trade (later index).
export function navNeighbors(
  trades: TradeRow[], id: number,
): { prevId: number | null; nextId: number | null; index: number } {
  const index = trades.findIndex((t) => t.position_id === id);
  if (index === -1) return { prevId: null, nextId: null, index: -1 };
  return {
    index,
    nextId: index > 0 ? trades[index - 1].position_id : null,
    prevId: index < trades.length - 1 ? trades[index + 1].position_id : null,
  };
}
```

- [ ] **Step 4: Run test**

Run: `cd frontend && npx vitest run src/lib/tradeView.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/tradeView.ts frontend/src/lib/tradeView.test.ts
git commit -m "feat(fe): tradeView helpers (overlay lines + filter-aware nav neighbors)"
```

---

### Task 8: `TradeView.tsx` page — chart + stats panel + route

**Files:**
- Create: `frontend/src/pages/TradeView.tsx`
- Modify: `frontend/src/App.tsx` (add route)
- Test: `frontend/src/pages/TradeView.test.tsx`

**Interfaces:**
- Consumes: `useApi`, `TradeDetailData`/`TradeFull`, `useChartData`, `CandleChart`, `useChartPrefs`, `tradeLines` (Task 7), `choose`-equivalent TF (compute client-side, below), `AnnotationForm`, `TagEditor`, `money`/`rmult`/`price`/`wib`/`dur`.
- Produces: the `/trades/:id/view` page. TF is derived client-side by `pickTf(duration_s)` (mirror of the Python ladder). Anchors `useChartData` at `open_time_msc` and forward-loads to `close_time_msc + pad`.

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/src/pages/TradeView.test.tsx
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { describe, it, expect, vi, beforeEach } from "vitest";
import TradeView from "./TradeView";

// Minimal fetch stub: trade detail + candles + prefs + trades list.
beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn((url: string) => {
    if (url.startsWith("/api/trades/2?") || url === "/api/trades/2")
      return Promise.resolve({ ok: true, json: () => Promise.resolve({
        header: { offset_s: 0, currency: "USC" },
        trade: { position_id: 2, symbol: "XAUUSDc", symbol_base: "XAUUSD", direction: "buy",
          status: "closed", open_time_msc: 10_000, close_time_msc: 20_000, duration_s: 10,
          volume: 0.1, open_price: 4000, close_price: 4010, sl_initial: null, tp_initial: null,
          net_profit: 100, r_multiple: 1, mae_r: null, mfe_r: null, magic: null },
        annotation: null, tags: [], session: "London", is_ea: false, chartable: true,
      }) });
    if (url.startsWith("/api/candles"))
      return Promise.resolve({ ok: true, json: () => Promise.resolve({
        symbol: "XAUUSDc", timeframe: "M1",
        candles: [{ time_msc: 10_000, o: 4000, h: 4012, l: 3999, c: 4010, v: 5 }],
        missing: [], pending: false }) });
    if (url.startsWith("/api/trades"))
      return Promise.resolve({ ok: true, json: () => Promise.resolve({
        header: {}, trades: [{ position_id: 2 }], tags: {}, symbols: [], max_abs_net: 1, filters: {} }) });
    return Promise.resolve({ ok: true, json: () => Promise.resolve({ prefs: null }) });
  }));
});

it("shows the trade's R and net in the stats panel", async () => {
  render(<MemoryRouter initialEntries={["/trades/2/view"]}>
    <Routes><Route path="/trades/:id/view" element={<TradeView />} /></Routes>
  </MemoryRouter>);
  expect(await screen.findByText(/R-multiple/i)).toBeInTheDocument();
  expect(await screen.findByText("XAUUSD")).toBeInTheDocument();
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/pages/TradeView.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Write minimal implementation**

Add a client-side TF ladder to `tradeView.ts` (keep it beside the other helpers; mirrors `render/chart.py`):

```ts
// frontend/src/lib/tradeView.ts — append
import { timeframeMs, type Timeframe, TIMEFRAMES } from "./candles";
const MAX_TRADE_BARS = 60;
export function pickTf(durationS: number | null): Timeframe {
  const d = (durationS ?? 0) * 1000;
  for (const tf of TIMEFRAMES) if (d <= timeframeMs(tf) * MAX_TRADE_BARS) return tf;
  return TIMEFRAMES[TIMEFRAMES.length - 1];
}
```

```tsx
// frontend/src/pages/TradeView.tsx
import { useEffect, useMemo, useState } from "react";
import { useParams, useSearchParams, useNavigate } from "react-router-dom";
import { useApi } from "../lib/api";
import type { TradeDetailData, TradesData } from "../lib/types";
import { useChartData } from "../hooks/useChartData";
import { useChartPrefs } from "../hooks/useChartPrefs";
import CandleChart from "../components/CandleChart";
import AnnotationForm from "../components/AnnotationForm";
import TagEditor from "../components/TagEditor";
import { money, rmult, price, wib, dur } from "../lib/format";
import { tradeLines, navNeighbors, pickTf } from "../lib/tradeView";
import { timeframeMs } from "../lib/candles";

function Fact({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex justify-between gap-4 py-1 border-b border-white/5 text-[13px]">
      <span className="text-muted">{label}</span><span className="num text-right">{children}</span>
    </div>
  );
}
const dash = <span className="text-muted">—</span>;

export default function TradeView() {
  const { id } = useParams();
  const nav = useNavigate();
  const [params] = useSearchParams();
  const { settings } = useChartPrefs();
  const { data, reload } = useApi<TradeDetailData>(`/api/trades/${id}`);

  // Filter-aware neighbor list (same query the Trades page used).
  const listQ = params.toString();
  const { data: list } = useApi<TradesData>(`/api/trades${listQ ? `?${listQ}` : ""}`);
  const neighbors = useMemo(
    () => (list ? navNeighbors(list.trades, Number(id)) : { prevId: null, nextId: null, index: -1 }),
    [list, id],
  );

  const t = data?.trade;
  const tf = t ? pickTf(t.duration_s) : "M5";
  const anchor = t?.open_time_msc;
  const chart = useChartData(t?.symbol ?? "XAUUSDc", tf, 60, 3000, anchor);
  // Forward-load past the exit so context after the trade is visible.
  useEffect(() => {
    if (t?.close_time_msc != null) chart.loadUpTo(t.close_time_msc + timeframeMs(tf) * 15);
  }, [t?.close_time_msc, tf, chart.loadUpTo]);

  const overlay = useMemo(() => (t ? tradeLines(t) : undefined), [t]);
  const goto = (pid: number | null) => { if (pid != null) nav(`/trades/${pid}/view${listQ ? `?${listQ}` : ""}`); };

  if (!data || !t) return <div className="text-muted p-6">Memuat…</div>;
  const pnl = t.net_profit ?? 0;

  return (
    <div className="flex gap-3 h-[calc(100vh-2rem)]">
      <div className="relative flex-1 min-h-0 flex flex-col">
        <h1 className="text-[16px] font-bold mb-2">{t.symbol_base}{" "}
          <span className="uppercase">{t.direction}</span>
          <span className="text-muted num text-[12px] ml-2">#{t.position_id}</span></h1>
        <div className="flex-1 min-h-0">
          {chart.candles.length ? (
            <CandleChart symbol={t.symbol} tf={tf} settings={settings}
              candles={chart.candles} overlayLines={overlay} lastBarMs={chart.lastBarMs}
              onHover={() => {}} onNowVisibleChange={() => {}} onRequestOlder={chart.loadOlder}
              live={null} nowVisible={false} />
          ) : (
            <div className="glass h-full flex items-center justify-center text-muted text-sm">
              {chart.status === "gaveup"
                ? <span>Belum ada data ter-cache — jalankan <code>journal live</code>.</span>
                : <span>⌛ Memuat data {t.symbol} {tf}…</span>}
            </div>
          )}
        </div>
        {/* bottom-center prev/next */}
        <div className="flex justify-center gap-3 mt-2">
          <button className="glass px-3 py-1 disabled:opacity-30" disabled={neighbors.prevId == null}
            onClick={() => goto(neighbors.prevId)}>← lebih lama</button>
          <button className="glass px-3 py-1 disabled:opacity-30" disabled={neighbors.nextId == null}
            onClick={() => goto(neighbors.nextId)}>lebih baru →</button>
        </div>
      </div>

      <aside className="w-[280px] shrink-0 overflow-y-auto flex flex-col gap-3">
        <div className="glass p-3">
          <Fact label="R-multiple">{t.r_multiple == null ? dash : rmult(t.r_multiple)}</Fact>
          <Fact label="Net"><span className={pnl > 0 ? "text-pos" : pnl < 0 ? "text-neg" : ""}>
            {money(t.net_profit, data.header.currency, { sign: true })}</span></Fact>
          <Fact label="Volume">{t.volume}</Fact>
          <Fact label="MAE (R)">{t.mae_r == null ? dash : rmult(t.mae_r)}</Fact>
          <Fact label="MFE (R)">{t.mfe_r == null ? dash : rmult(t.mfe_r)}</Fact>
          <Fact label="Entry">{price(t.open_price)}</Fact>
          <Fact label="Exit">{price(t.close_price)}</Fact>
          <Fact label="SL awal">{t.sl_initial == null ? dash : price(t.sl_initial)}</Fact>
          <Fact label="TP awal">{t.tp_initial == null ? dash : price(t.tp_initial)}</Fact>
          <Fact label="Durasi">{dur(t.duration_s)}</Fact>
          <Fact label="Dibuka">{wib(t.open_time_msc, data.header.offset_s)}</Fact>
          <Fact label="Ditutup">{wib(t.close_time_msc, data.header.offset_s)}</Fact>
          <Fact label="Session">{data.session}</Fact>
        </div>
        <AnnotationForm positionId={t.position_id} annotation={data.annotation} onSaved={reload} />
        <TagEditor positionId={t.position_id} tags={data.tags} onChanged={reload} />
        <a className="text-[12px] text-cyan hover:underline" href={`/trades/${t.position_id}`}>← detail trade</a>
      </aside>
    </div>
  );
}
```

Register the route:

```tsx
// frontend/src/App.tsx — add inside <Routes>, before or after the /trades/:id route
<Route path="/trades/:id/view" element={<TradeView />} />
```
(Import `TradeView` at the top: `import TradeView from "./pages/TradeView";`.)

> **Implementer note:** confirm `CandleChart`'s prop names against `frontend/src/components/CandleChart.tsx` and the `useChartData` return shape against `frontend/src/hooks/useChartData.ts` (both used verbatim in `Chart.tsx:53,131-145`). If a prop is optional there, drop the no-op handlers.

- [ ] **Step 2b: Run test to verify it fails, then implement, then:**

- [ ] **Step 4: Run test + build**

Run: `cd frontend && npx vitest run src/pages/TradeView.test.tsx && npm run build`
Expected: PASS, build clean.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/TradeView.tsx frontend/src/App.tsx frontend/src/lib/tradeView.ts frontend/src/pages/TradeView.test.tsx
git commit -m "feat(fe): TradeView interactive viewer — chart + stats panel + tag/annotation edit + route"
```

---

### Task 9: Keyboard prev/next + verify filter-carry end-to-end

**Files:**
- Modify: `frontend/src/pages/TradeView.tsx`
- Modify: `frontend/src/pages/Trades.tsx` (carry active filters into the row link)
- Test: `frontend/src/pages/TradeView.test.tsx`

**Interfaces:**
- Consumes: `neighbors`/`goto` (Task 8).
- Produces: `←`/`→` key handlers on the viewer; Trades-list rows link to `/trades/:id?<filters>` so the whole chain stays filter-aware (TradeDetail's "Lihat di chart" link already forwards its own query-string — extend it in this task to append `params`).

- [ ] **Step 1: Write the failing test**

```tsx
// TradeView.test.tsx — add (same beforeEach fetch stub; list returns [3,2,1])
import { fireEvent } from "@testing-library/react";
it("arrow keys navigate to neighbors", async () => {
  // stub the list to [3,2,1] for this case, render at /trades/2/view, then:
  // fireEvent.keyDown(window, { key: "ArrowLeft" }) -> navigates to /trades/1/view
  // (assert via a spy on useNavigate or by rendering a location probe).
});
```

Practical assertion: render a small `<LocationProbe/>` route element that renders `useLocation().pathname`, mount both routes, press the key, and assert the probe text. Keep the list stub returning `[{position_id:3},{position_id:2},{position_id:1}]`.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/pages/TradeView.test.tsx`
Expected: FAIL — no key handler yet.

- [ ] **Step 3: Write minimal implementation**

```tsx
// TradeView.tsx — add near the other effects
useEffect(() => {
  const onKey = (e: KeyboardEvent) => {
    if (e.key === "ArrowLeft") goto(neighbors.prevId);
    else if (e.key === "ArrowRight") goto(neighbors.nextId);
  };
  window.addEventListener("keydown", onKey);
  return () => window.removeEventListener("keydown", onKey);
}, [neighbors.prevId, neighbors.nextId, listQ]);
```

```tsx
// Trades.tsx — where each row links to `/trades/${position_id}`, append the
// active filter query so TradeDetail -> TradeView inherits it. Build once:
//   const q = new URLSearchParams(); if (symbol) q.set("symbol", symbol); ...
//   href={`/trades/${r.position_id}${q.toString() ? `?${q}` : ""}`}
```
Update TradeDetail's "Lihat di chart" link (Task 5) to forward its own query-string: `href={`/trades/${trade.position_id}/view${window.location.search}`}`.

- [ ] **Step 4: Run test + build**

Run: `cd frontend && npx vitest run src/pages/TradeView.test.tsx && npm run build`
Expected: PASS, build clean.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/TradeView.tsx frontend/src/pages/Trades.tsx frontend/src/pages/TradeDetail.tsx frontend/src/pages/TradeView.test.tsx
git commit -m "feat(fe): keyboard nav + filter-carry chain (Trades -> detail -> viewer)"
```

---

### Task 10: Optional playback reveal (cursor + step/play)

Secondary feature: reveal the real bars progressively from a few bars before entry, exit marker implied by the overlay. Reuses `clipToCursor` from `lib/replay.ts` — no evaluator, no fills.

**Files:**
- Modify: `frontend/src/pages/TradeView.tsx`
- Test: `frontend/src/pages/TradeView.test.tsx`

**Interfaces:**
- Consumes: `clipToCursor(candles, cursorMsc)` from `../lib/replay` (used in `Chart.tsx:87`).
- Produces: a Play/Step/Reset control row; default (no playback) shows the full window.

- [ ] **Step 1: Write the failing test**

```tsx
// TradeView.test.tsx — add
it("play reveals bars up to a moving cursor", async () => {
  // stub candles with 3 bars at t=10_000,10_060_000,10_120_000.
  // render, click "Putar ulang" -> only bars <= cursor are shown.
  // Assert the chart receives fewer candles than the full set right after start.
});
```

Since `CandleChart` is heavy, assert on a lightweight `data-testid="bar-count"` element you add to the panel: `<span data-testid="bar-count">{shown.length}</span>`.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/pages/TradeView.test.tsx -t "play reveals"`
Expected: FAIL.

- [ ] **Step 3: Write minimal implementation**

```tsx
// TradeView.tsx — add state + controls
import { clipToCursor } from "../lib/replay";
// ...
const [cursor, setCursor] = useState<number | null>(null);   // null = full window
const [playing, setPlaying] = useState(false);
const startMs = t.open_time_msc - timeframeMs(tf) * 10;       // a few bars before entry
const shown = cursor == null ? chart.candles : clipToCursor(chart.candles, cursor);

useEffect(() => {
  if (!playing || cursor == null) return;
  const iv = setInterval(() => {
    setCursor((c) => {
      const next = (c ?? startMs) + timeframeMs(tf);
      const last = chart.lastBarMs ?? next;
      if (next >= last) { setPlaying(false); return null; } // reached the end -> full view
      return next;
    });
  }, 600);
  return () => clearInterval(iv);
}, [playing, cursor, tf, chart.lastBarMs, startMs]);

// pass `shown` (not chart.candles) to CandleChart; add near prev/next:
// <button onClick={() => { setCursor(startMs); setPlaying(true); }}>Putar ulang</button>
// <button onClick={() => setCursor((c) => (c ?? startMs) + timeframeMs(tf))}>Step ▸</button>
// <button onClick={() => { setCursor(null); setPlaying(false); }}>Reset</button>
// <span data-testid="bar-count">{shown.length}</span>
```

- [ ] **Step 4: Run test + build + full suites**

Run:
```bash
cd frontend && npx vitest run && npm run build
cd .. && uv run pytest && uv run journal rebuild
```
Expected: all green; rebuild succeeds.

- [ ] **Step 5: Commit + graphify**

```bash
git add frontend/src/pages/TradeView.tsx frontend/src/pages/TradeView.test.tsx
git commit -m "feat(fe): optional playback reveal in TradeView (cursor + step/play, no evaluator)"
graphify update .
```

---

## Self-review notes (author)

- **Spec coverage:** #2 theme/pad/tf/toggles (T1–T2), global `app_prefs` persistence (T3), panel on trade page (T5); #3 dedicated route (T8), filter-aware prev/next + keyboard (T7–T9), overlay lines + full-window default (T8), optional playback reveal via cursor only (T10), stats panel incl. R-first + USC + `—` for null (T8), manual tag/annotation edit via existing components (T8). Raw-symbol gap for the candle feed closed by T6.
- **Type consistency:** `RenderOpts` / `normalize_opts` (py) ↔ `TradePngSettings` / `normalizeTradePng` + `toApi`/`fromApi` snake_case mapping (ts); `navNeighbors` older=prev / newer=next mapping tested against a newest-first list; `useChartData`/`CandleChart` used with the exact prop set from `Chart.tsx`.
- **Cache/rule-6:** PNG filename carries `opts.signature()`; DB never depends on a file; browser cache busted by `?v=version`.
- **Rule 4:** SL/TP/R null → `—`/`n/a`, both in the PNG (value-guard + toggle) and the viewer panel + `tradeLines`.
