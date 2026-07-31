import { render, screen, fireEvent } from "@testing-library/react";
import { it, expect, vi } from "vitest";
import SltpConfirmDialog from "./SltpConfirmDialog";

it("pre-fills the dragged price and confirms with it unchanged", () => {
  const onConfirm = vi.fn();
  render(<SltpConfirmDialog positionId={5} kind="sl" price={1900.5}
    onConfirm={onConfirm} onCancel={() => {}} />);

  const input = screen.getByLabelText(/SL/i) as HTMLInputElement;
  expect(input.value).toBe("1900.5");

  fireEvent.click(screen.getByText(/konfirmasi/i));
  expect(onConfirm).toHaveBeenCalledWith(1900.5);
});

it("sends the edited value, not the original drag value", () => {
  const onConfirm = vi.fn();
  render(<SltpConfirmDialog positionId={5} kind="tp" price={1950}
    onConfirm={onConfirm} onCancel={() => {}} />);

  const input = screen.getByLabelText(/TP/i) as HTMLInputElement;
  fireEvent.change(input, { target: { value: "1955.25" } });
  fireEvent.click(screen.getByText(/konfirmasi/i));

  expect(onConfirm).toHaveBeenCalledWith(1955.25);
});

it("shows removal copy and disables the price field when removing", () => {
  render(<SltpConfirmDialog positionId={5} kind="sl" price={0} removing
    onConfirm={() => {}} onCancel={() => {}} />);

  expect(screen.getByText(/tanpa stop-loss/i)).toBeTruthy();
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
