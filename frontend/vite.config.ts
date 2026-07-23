import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Served by FastAPI under /app during the transition, so assets resolve there.
export default defineConfig({
  base: "/app/",
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/api": "http://localhost:8000" },
  },
});
