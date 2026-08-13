import { render, screen } from "@testing-library/react";
import { it, expect, vi, afterEach } from "vitest";
import App from "./App";

afterEach(() => vi.unstubAllGlobals());

// The routes are lazy now, so a bad import path in App.tsx no longer breaks the
// build — it breaks at click time, on that one route. One route resolved end to
// end is the cheapest thing that fails if the split is wrong.
it("resolves a lazy route chunk into the shell", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ header: { offset_s: 0 }, commands: [] }),
    }),
  );
  window.history.pushState({}, "", "/commands");

  render(<App />);

  // Nothing from the page is in the entry bundle, so the shell paints first…
  expect(screen.getAllByRole("navigation").length).toBeGreaterThan(0);
  // …and the page only exists once its chunk has arrived.
  expect(
    await screen.findByRole("heading", { name: /log perintah/i }),
  ).toBeInTheDocument();
});
