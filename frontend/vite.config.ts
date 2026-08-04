import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// Served by FastAPI at the site root (Phase 5 cutover; Jinja retired).
export default defineConfig({
  base: "/",
  plugins: [react()],
  server: {
    port: 5173,
    // The trade PNG is the ONLY non-/api backend path the SPA fetches
    // (app.py: /trades/{position_id}/chart.png). Matched as a REGEX, not as a
    // "/trades" prefix: /trades, /trades/:id and /trades/:id/view are SPA
    // routes, and proxying those would send a dev hard-refresh to FastAPI's
    // catch-all, which serves the BUILT dist index.html — stale bundle, no HMR.
    proxy: {
      "/api": "http://localhost:8000",
      "^/trades/\\d+/chart\\.png": "http://localhost:8000",
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
  },
});
