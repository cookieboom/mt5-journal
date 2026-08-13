import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { it, expect } from "vitest";
import AppShell from "./AppShell";

const at = (path: string) =>
  render(
    <MemoryRouter initialEntries={[path]}>
      <AppShell><p>isi</p></AppShell>
    </MemoryRouter>,
  );

it("names the route in the document title", () => {
  at("/report");
  expect(document.title).toBe("Report · mt5-journal");
});

it("titles a nested route by its section, not by falling through", () => {
  at("/trades/42");
  expect(document.title).toBe("Trades · mt5-journal");
});

it("does not let the / entry claim every other route", () => {
  at("/lab");
  expect(document.title).toBe("Lab · mt5-journal");
});

it("puts a skip link ahead of the nav, pointing at main", () => {
  at("/");
  const skip = screen.getByRole("link", { name: /lewati ke konten/i });
  expect(skip.getAttribute("href")).toBe("#main");
  // Ahead of the nav in DOM order is the whole point — nine nav items would
  // otherwise sit between the tab key and the content on every route.
  const nav = screen.getAllByRole("navigation")[0];
  expect(skip.compareDocumentPosition(nav) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
});
