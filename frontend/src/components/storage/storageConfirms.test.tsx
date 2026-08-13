import { render, screen, fireEvent } from "@testing-library/react";
import { it, expect } from "vitest";
import MaintenancePanel from "./MaintenancePanel";
import PrunePanel from "./PrunePanel";

// Both panels used to hand-roll a `fixed inset-0` div: no Escape, no focus
// trap, no role. They gate a VACUUM, a full trade rebuild and an irreversible
// candle delete, so the assertion worth keeping is that the confirmation is a
// real, named dialog — the shared Modal — and not a styled overlay.

it("confirms maintenance actions in a named dialog", () => {
  render(<MaintenancePanel />);

  fireEvent.click(screen.getByText("Vacuum & optimalkan DB"));

  expect(screen.getByRole("dialog", { name: "Vacuum & optimalkan database" })).toBeTruthy();
});

it("confirms a prune in a named dialog", () => {
  render(<PrunePanel />);

  fireEvent.click(screen.getByText("Prune candle lama"));

  expect(screen.getByRole("dialog", { name: "Konfirmasi prune candle" })).toBeTruthy();
});

it("closes the prune dialog on Batal without pruning", () => {
  render(<PrunePanel />);

  fireEvent.click(screen.getByText("Prune candle lama"));
  fireEvent.click(screen.getByText("Batal"));

  expect(screen.queryByRole("dialog")).toBeNull();
});
