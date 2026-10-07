import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import IndicatorPanel from "./IndicatorPanel";
import type { IndicatorLayout, IndicatorResult, ScriptInfo } from "../lib/indicators";

const scripts: ScriptInfo[] = [
  { id: "lib:ema", name: "EMA", source: "length = input(20, min=1, title='Length')\nplot(ema(close, length))", readonly: true, updated_ms: null },
  { id: "user:1", name: "Mine", source: "plot(close)", readonly: false, updated_ms: 1 },
];
const layout: IndicatorLayout = { version: 1, items: [{ id: "a", script: "lib:ema", inputs: {}, visible: true }] };
const result: IndicatorResult = {
  times: [1000, 2000],
  plots: [{ title: "EMA", color: "cyan", pane: "price", style: "line", width: 2, values: [1.5, 2.25] }],
  hlines: [], signals: [], lookback: 80, warm_from_msc: 5000, forming_msc: null, pending: false,
};

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({
    ok: true, inputs: [{ name: "length", kind: "int", default: 20, min: 1, max: null, step: null, title: "Length" }],
    plots: [], hlines: [], signals: [], lookback: {},
  }), { status: 200 })));
});

function setup(over: Partial<Parameters<typeof IndicatorPanel>[0]> = {}) {
  const onLayout = vi.fn();
  render(<IndicatorPanel layout={layout} onLayout={onLayout} scripts={scripts} onScriptSaved={() => {}}
                         results={{ a: result }} errors={{}} hoverMs={null} firstShownMs={1000} {...over} />);
  return onLayout;
}

it("shows the latest value, or the hovered bar's", () => {
  setup();
  expect(screen.getByText("2.25")).toBeTruthy();
});

it("shows the hovered bar's value", () => {
  setup({ hoverMs: 1000 });
  expect(screen.getByText("1.5")).toBeTruthy();
});

it("warns when warm-up history is short", () => {
  setup();
  expect(screen.getByText(/warm-up kurang/)).toBeTruthy();
});

it("adds, hides and removes", () => {
  const onLayout = setup();
  fireEvent.change(screen.getByLabelText("Tambah indikator"), { target: { value: "user:1" } });
  expect(onLayout.mock.calls[0][0].items.map((i: { script: string }) => i.script)).toEqual(["lib:ema", "user:1"]);
  fireEvent.click(screen.getByLabelText("Tampilkan EMA"));
  expect(onLayout.mock.calls[1][0].items[0].visible).toBe(false);
  fireEvent.click(screen.getByLabelText("Hapus EMA"));
  expect(onLayout.mock.calls[2][0].items).toEqual([]);
});

it("edits an input from the server-provided spec", async () => {
  const onLayout = setup();
  fireEvent.click(screen.getByText("EMA", { selector: "button" }));
  const field = await screen.findByLabelText("Length");
  fireEvent.change(field, { target: { value: "50" } });
  expect(onLayout.mock.calls[0][0].items[0].inputs).toEqual({ length: 50 });
});

it("reports a per-indicator error", () => {
  setup({ errors: { a: "line 1:1: boom" } });
  expect(screen.getByText("line 1:1: boom")).toBeTruthy();
});
