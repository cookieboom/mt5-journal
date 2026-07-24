import { describe, it, expect } from "vitest";
import {
  DEFAULT_SETTINGS, loadChartSettings, saveChartSettings, parseSelection,
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

  it("round-trips saved settings", () => {
    const s = fakeStore();
    saveChartSettings({ theme: "light", grid: false }, s);
    expect(loadChartSettings(s)).toEqual({ theme: "light", grid: false });
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
