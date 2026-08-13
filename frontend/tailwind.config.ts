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
    },
  },
  plugins: [],
} satisfies Config;
