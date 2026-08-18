import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import PaperPositions from "./PaperPositions";
import type { PaperAccountView, PaperPosition } from "../lib/types";

function pos(over: Partial<PaperPosition> = {}): PaperPosition {
  return {
    id: 1, account_id: 1, symbol: "XAUUSDc", symbol_base: "XAUUSD",
    direction: "buy", order_kind: "market", request_price: null, volume: 0.1,
    sl: 4025, tp: 0, sl_initial: 4025, expires_msc: null, status: "open",
    requested_msc: 1, entry_msc: 1, entry_price: 4030.5, exit_msc: null,
    exit_price: null, exit_reason: null, net_profit: null, r_multiple: null,
    mae_r: null, mfe_r: null, parent_id: null, floating: -5, ...over,
  };
}

const view = (over: Partial<PaperAccountView> = {}): PaperAccountView => ({
  account: { id: 1, name: "X", initial_balance: 1e6, balance: 1e6, leverage: 500,
             stopout_pct: 20, status: "active", created_at_msc: 1,
             archived_at_msc: null },
  header: { currency: "USC", balance: 1e6, equity: 1e6, margin: 0,
            free_margin: 1e6, margin_level: null, floating: 0, leverage: 500,
            stopout_pct: 20 },
  open: [], pending: [], closed: [],
  summary: { n: 0, win_rate: null, avg_r: null, total_r: 0, avg_mae_r: null,
             avg_mfe_r: null },
  max_drawdown: null, equity_curve: [], ...over,
});

describe("PaperPositions", () => {
  it("marks a position that belongs to another symbol than the chart's", () => {
    render(<PaperPositions view={view({ open: [pos({ symbol: "BTCUSDc", symbol_base: "BTCUSD" })] })}
      chartSymbol="XAUUSDc" onClose={vi.fn()} onPartial={vi.fn()}
      onReverse={vi.fn()} onCancel={vi.fn()} onCloseAll={vi.fn()} />);
    expect(screen.getByTitle(/simbol lain/i)).toBeTruthy();
  });

  it("closes the position it was asked about, by id", () => {
    const onClose = vi.fn();
    render(<PaperPositions view={view({ open: [pos({ id: 7 })] })}
      chartSymbol="XAUUSDc" onClose={onClose} onPartial={vi.fn()}
      onReverse={vi.fn()} onCancel={vi.fn()} onCloseAll={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: /^tutup$/i }));
    expect(onClose).toHaveBeenCalledWith(7);
  });

  it("offers nothing to press when the account is flat", () => {
    render(<PaperPositions view={view()} chartSymbol="XAUUSDc" onClose={vi.fn()}
      onPartial={vi.fn()} onReverse={vi.fn()} onCancel={vi.fn()}
      onCloseAll={vi.fn()} />);
    expect(screen.queryByRole("button", { name: /tutup semua/i })).toBeNull();
  });
});
