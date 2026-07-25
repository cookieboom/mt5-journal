import { describe, it, expect } from "vitest";
import {
  DEFAULT_SETTINGS, loadChartSettings, saveChartSettings, parseSelection,
  normalizeSettings, reconcilePrefs, clampBars,
} from "./chartPrefs";

function fakeStore(): Storage {
  const m = new Map<string, string>();
  return {
    getItem: (k) => (m.has(k) ? m.get(k)! : null),
    setItem: (k, v) => void m.set(k, v),
    removeItem: (k) => void m.delete(k),
    clear: () => m.clear(),
    key: () => null,
    get length() { return m.size; },
  } as Storage;
}

describe("chartPrefs", () => {
  it("loads defaults when nothing stored", () => {
    expect(loadChartSettings(fakeStore())).toEqual(DEFAULT_SETTINGS);
  });

  it("round-trips saved settings (legacy shape migrates through load)", () => {
    const s = fakeStore();
    saveChartSettings({ theme: "light", grid: false } as unknown as ReturnType<typeof loadChartSettings>, s);
    expect(loadChartSettings(s)).toEqual(normalizeSettings({ theme: "light", grid: false }));
  });

  it("falls back to defaults on corrupt json", () => {
    const s = fakeStore();
    s.setItem("mt5j.chart.settings", "{not json");
    expect(loadChartSettings(s)).toEqual(DEFAULT_SETTINGS);
  });

  it("parseSelection returns defaults for absent/invalid params", () => {
    expect(parseSelection(new URLSearchParams(""))).toEqual({ symbol: "XAUUSDc", tf: "M5" });
    expect(parseSelection(new URLSearchParams("symbol=NOPE&tf=X9")))
      .toEqual({ symbol: "XAUUSDc", tf: "M5" });
  });

  it("parseSelection honours valid params", () => {
    expect(parseSelection(new URLSearchParams("symbol=BTCUSDc&tf=H1")))
      .toEqual({ symbol: "BTCUSDc", tf: "H1" });
  });
});

describe("chartPrefs v1", () => {
  it("migrates a legacy {theme,grid} object (no version) to full v1 defaults", () => {
    const s = normalizeSettings({ theme: "light", grid: false });
    expect(s.version).toBe(1);
    expect(s.theme).toBe("light");
    expect(s.grid).toBe(false);
    expect(s.colors).toEqual(DEFAULT_SETTINGS.colors);   // filled from defaults
    expect(s.chartType).toBe(DEFAULT_SETTINGS.chartType);
    expect(s.initialBars).toBe(DEFAULT_SETTINGS.initialBars);
  });

  it("clamps initialBars and maxBars into bounds", () => {
    const a = normalizeSettings({ version: 1, initialBars: 5, maxBars: 999999 });
    expect(a.initialBars).toBe(100);   // floor
    expect(a.maxBars).toBe(10000);     // ceil
    const b = normalizeSettings({ version: 1, initialBars: 900, maxBars: 200 });
    expect(b.maxBars).toBeGreaterThanOrEqual(b.initialBars); // maxBars >= initialBars
  });

  it("falls back to defaults for garbage input", () => {
    expect(normalizeSettings(null)).toEqual(DEFAULT_SETTINGS);
    expect(normalizeSettings("nope")).toEqual(DEFAULT_SETTINGS);
  });

  it("load/save roundtrips through a Storage", () => {
    const store = fakeStore();
    const custom = { ...DEFAULT_SETTINGS, theme: "light" as const };
    saveChartSettings(custom, store);
    expect(loadChartSettings(store)).toEqual(custom);
  });

  it("reconcile: DB present wins and is normalized", () => {
    const local = { ...DEFAULT_SETTINGS, theme: "light" as const };
    const r = reconcilePrefs(local, { version: 1, theme: "dark", initialBars: 5 }, true);
    expect(r.settings.theme).toBe("dark");
    expect(r.settings.initialBars).toBe(100);  // normalized/clamped
    expect(r.shouldImport).toBe(false);
  });

  it("reconcile: DB absent + local existed -> import local", () => {
    const local = { ...DEFAULT_SETTINGS, grid: false };
    const r = reconcilePrefs(local, null, true);
    expect(r.settings).toEqual(local);
    expect(r.shouldImport).toBe(true);
  });

  it("reconcile: DB absent + no local -> defaults, no import", () => {
    const r = reconcilePrefs(DEFAULT_SETTINGS, null, false);
    expect(r.shouldImport).toBe(false);
  });

  it("clampBars clamps at consumption (e.g. emptied number inputs -> 0)", () => {
    expect(clampBars({ initialBars: 0, maxBars: 0 }))
      .toEqual({ initialBars: 100, maxBars: 500 });
    expect(clampBars({ initialBars: 900, maxBars: 200 }))
      .toEqual({ initialBars: 900, maxBars: 900 }); // maxBars raised to initialBars
    expect(clampBars({ initialBars: 300, maxBars: 3000 }))
      .toEqual({ initialBars: 300, maxBars: 3000 }); // in-range pair passes through unchanged
  });

  it("parseSelection: URL wins, else saved default, else hard default", () => {
    expect(parseSelection(new URLSearchParams("symbol=BTCUSDc&tf=H1")))
      .toEqual({ symbol: "BTCUSDc", tf: "H1" });
    expect(parseSelection(new URLSearchParams(""), { symbol: "EURUSDc", tf: "H4" }))
      .toEqual({ symbol: "EURUSDc", tf: "H4" });
    expect(parseSelection(new URLSearchParams("")))
      .toEqual({ symbol: "XAUUSDc", tf: "M5" });
  });
});
