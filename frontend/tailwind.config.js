/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./src/**/*.{js,ts,jsx,tsx,mdx}"],
  theme: {
    extend: {
      colors: {
        terminal: {
          bg:      "#050508",
          panel:   "#0d0d14",
          border:  "#1a1a2e",
          accent:  "#00ff88",
          red:     "#ff4466",
          yellow:  "#ffcc00",
          muted:   "#4a4a6a",
        },
      },
      fontFamily: {
        mono: ["'JetBrains Mono'", "Menlo", "monospace"],
      },
    },
  },
  plugins: [],
};
