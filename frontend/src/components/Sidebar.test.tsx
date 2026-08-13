// Below 768px the rail is not rendered, so MobileNav is the only way between
// routes. A future change that drops it — or drops a route from it — leaves the
// journal unreachable from a phone with no other test noticing.
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, it, expect } from "vitest";
import Sidebar, { MobileNav } from "./Sidebar";

const ROUTES = [
  "Dashboard", "Live", "Chart", "Trades", "Report",
  "Weekly", "Commands", "Storage", "Lab",
];

describe("MobileNav", () => {
  it("carries every route the rail carries", () => {
    const rail = render(<MemoryRouter><Sidebar /></MemoryRouter>);
    const railLabels = Array.from(rail.container.querySelectorAll("a")).map((a) => a.textContent);
    rail.unmount();

    render(<MemoryRouter><MobileNav /></MemoryRouter>);
    const barLabels = screen.getAllByRole("link").map((a) => a.textContent);

    expect(railLabels).toEqual(ROUTES);
    expect(barLabels).toEqual(ROUTES);
  });

  it("is a labelled navigation landmark", () => {
    render(<MemoryRouter><MobileNav /></MemoryRouter>);
    expect(screen.getByRole("navigation", { name: "Navigasi utama" })).toBeInTheDocument();
  });

  it("marks the current route so the scrolled bar can re-centre on it", () => {
    render(<MemoryRouter initialEntries={["/lab"]}><MobileNav /></MemoryRouter>);
    expect(screen.getByRole("link", { current: "page" })).toHaveTextContent("Lab");
  });
});
