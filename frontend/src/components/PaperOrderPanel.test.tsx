import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import PaperOrderPanel from "./PaperOrderPanel";

const placeOrder = vi.fn();
vi.mock("../lib/paperApi", () => ({ placeOrder: (...a: unknown[]) => placeOrder(...a) }));

beforeEach(() => placeOrder.mockReset().mockResolvedValue({ id: 1, status: "open" }));
afterEach(() => vi.clearAllMocks());

describe("PaperOrderPanel", () => {
  it("sends exactly one sizing field — lots, or a share of equity, never both", async () => {
    render(<PaperOrderPanel accountId={1} symbol="XAUUSDc" lastPrice={4030}
      onPlaced={vi.fn()} />);
    fireEvent.change(screen.getByLabelText("sl"), { target: { value: "4025" } });
    fireEvent.click(screen.getByRole("button", { name: /^beli/i }));
    await waitFor(() => expect(placeOrder).toHaveBeenCalled());
    const body = placeOrder.mock.calls[0][1];
    expect(body.risk_pct == null !== (body.volume == null)).toBe(true);
  });

  it("switches sizing mode to risk and then sends risk_pct alone", async () => {
    render(<PaperOrderPanel accountId={1} symbol="XAUUSDc" lastPrice={4030}
      onPlaced={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: /risiko/i }));
    fireEvent.change(screen.getByLabelText("risk-pct"), { target: { value: "1" } });
    fireEvent.change(screen.getByLabelText("sl"), { target: { value: "4025" } });
    fireEvent.click(screen.getByRole("button", { name: /^beli/i }));
    await waitFor(() => expect(placeOrder).toHaveBeenCalled());
    const body = placeOrder.mock.calls[0][1];
    expect(body.risk_pct).toBe(1);
    expect(body.volume).toBeNull();
  });

  it("shows the server's refusal instead of pretending the order landed", async () => {
    // The real client never throws on a refusal: `postJson` resolves
    // `{ok:false, error}` for a 400. Treating that as success is how a refused
    // order silently reads as a filled one — so the refusal is driven through
    // the envelope, which is the only shape `placeOrder` can actually produce.
    placeOrder.mockResolvedValue({ ok: false, error: "Butuh margin 900.00 USC" });
    const onPlaced = vi.fn();
    render(<PaperOrderPanel accountId={1} symbol="XAUUSDc" lastPrice={4030}
      onPlaced={onPlaced} />);
    fireEvent.change(screen.getByLabelText("sl"), { target: { value: "4025" } });
    fireEvent.click(screen.getByRole("button", { name: /^beli/i }));
    await waitFor(() => expect(screen.getByRole("alert").textContent).toMatch(/margin/));
    expect(onPlaced).not.toHaveBeenCalled();
  });

  it("needs a limit price before a pending order can be sent", () => {
    render(<PaperOrderPanel accountId={1} symbol="XAUUSDc" lastPrice={4030}
      onPlaced={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: /limit/i }));
    expect(screen.getByRole("button", { name: /^beli/i })).toBeDisabled();
  });
});
