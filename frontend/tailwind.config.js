/** @type {import('tailwindcss').Config} */
export default {
  darkMode: "class",
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        bg: "#0b0a1a",
        panel: "rgba(255,255,255,0.045)",
        "panel-border": "rgba(255,255,255,0.09)",
        ink: "#e8e6ff",
        muted: "#9a97c4",
        violet: "#a78bfa",
        cyan: "#22d3ee",
        pos: "#34d399",
        neg: "#fb7185",
      },
      fontFamily: {
        sans: ["Inter", "system-ui", "sans-serif"],
        mono: ["'SF Mono'", "ui-monospace", "monospace"],
      },
    },
  },
  plugins: [],
};
