import { render, screen, fireEvent } from "@testing-library/react";
import { it, expect, vi } from "vitest";
import SltpConfirmDialog from "./SltpConfirmDialog";

it("pre-fills the dragged price and confirms with it unchanged", () => {
  const onConfirm = vi.fn();
  render(<SltpConfirmDialog positionId={5} kind="sl" price={1900.5}
    onConfirm={onConfirm} onCancel={() => {}} />);

  const input = screen.getByLabelText("SL") as HTMLInputElement;
  // Pre-fill is rounded to 5 decimals (matches ghostTitle's convention) —
  // the raw drag value never had this many trailing zeros, but the
  // resulting numeric value round-trips to the same price.
  expect(input.value).toBe("1900.50000");

  fireEvent.click(screen.getByText(/konfirmasi/i));
  expect(onConfirm).toHaveBeenCalledWith(1900.5);
});

it("rounds an unrounded drag price to 5 decimals for the pre-fill", () => {
  const onConfirm = vi.fn();
  render(<SltpConfirmDialog positionId={5} kind="sl" price={2403.7561278343201}
    onConfirm={onConfirm} onCancel={() => {}} />);

  const input = screen.getByLabelText("SL") as HTMLInputElement;
  expect(input.value).toBe("2403.75613");
});

it("sends the edited value, not the original drag value", () => {
  const onConfirm = vi.fn();
  render(<SltpConfirmDialog positionId={5} kind="tp" price={1950}
    onConfirm={onConfirm} onCancel={() => {}} />);

  const input = screen.getByLabelText("TP") as HTMLInputElement;
  fireEvent.change(input, { target: { value: "1955.25" } });
  fireEvent.click(screen.getByText(/konfirmasi/i));

  expect(onConfirm).toHaveBeenCalledWith(1955.25);
});

it("shows removal copy and omits the price field when removing", () => {
  render(<SltpConfirmDialog positionId={5} kind="sl" price={0} removing
    onConfirm={() => {}} onCancel={() => {}} />);

  expect(screen.getByText(/tanpa stop-loss/i)).toBeTruthy();
  expect(screen.queryByLabelText("SL")).toBeNull();
});

it("is a named dialog, not a div that looks like one", () => {
  render(<SltpConfirmDialog positionId={5} kind="sl" price={1900}
    onConfirm={() => {}} onCancel={() => {}} />);

  // The name matters as much as the role: this gates a command queued against
  // the live account, and "dialog" alone tells a screen reader nothing.
  expect(screen.getByRole("dialog", { name: "Atur SL" })).toBeTruthy();
});

it("calls onCancel and never onConfirm when Batal is clicked", () => {
  const onConfirm = vi.fn();
  const onCancel = vi.fn();
  render(<SltpConfirmDialog positionId={5} kind="sl" price={1900}
    onConfirm={onConfirm} onCancel={onCancel} />);

  fireEvent.click(screen.getByText(/batal/i));
  expect(onCancel).toHaveBeenCalled();
  expect(onConfirm).not.toHaveBeenCalled();
});
