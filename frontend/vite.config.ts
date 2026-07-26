import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// Served by FastAPI at the site root (Phase 5 cutover; Jinja retired).
export default defineConfig({
  base: "/",
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/api": "http://localhost:8000" },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
  },
});
