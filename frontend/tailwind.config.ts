import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./src/pages/**/*.{ts,tsx}", "./src/components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        brand: {
          navy: "#152a6e",
          blue: "#1d4ed8",
          cyan: "#06b6d4",
          purple: "#7c3aed",
        },
      },
    },
  },
  plugins: [],
};

export default config;
