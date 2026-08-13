import type { Config } from "tailwindcss";
import { palette, shadow } from "./src/lib/theme";

// Classes and canvas colours read the same tokens — see src/lib/theme.ts.
export default {
  darkMode: "class",
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: { ...palette },
      boxShadow: { ...shadow },
      fontFamily: {
        sans: ["Inter", "system-ui", "sans-serif"],
        mono: ["'SF Mono'", "ui-monospace", "monospace"],
      },
      // The one authored movement in the interface. A right-anchored floating
      // layer that simply exists is disorienting at 85vw on a phone; sliding it
      // in from the edge says where it came from and where ✕ sends it back.
      // Entrance only — an exit would need the panel to outlive its own unmount,
      // and 240ms of "it left" is not worth that state.
      keyframes: {
        "sheet-in": {
          from: { transform: "translateX(100%)" },
          to: { transform: "translateX(0)" },
        },
      },
      animation: {
        "sheet-in": "sheet-in 240ms cubic-bezier(0.16, 1, 0.3, 1)",
      },
    },
  },
  plugins: [],
} satisfies Config;
