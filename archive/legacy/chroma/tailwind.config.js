/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./src/**/*.{ts,tsx,html}"],
  theme: {
    extend: {
      colors: {
        bg: "var(--bg)",
        surface: "var(--surface)",
        border: "var(--border)",
        fg: "var(--fg)",
        "fg-dim": "var(--fg-dim)",
        accent: "var(--accent)",
        "accent-muted": "var(--accent-muted)",
      },
      fontFamily: {
        display: ['"SF Pro Display"', "-apple-system", "system-ui", "sans-serif"],
        body: ['"SF Pro Text"', "-apple-system", "system-ui", "sans-serif"],
        mono: ['"SF Mono"', "ui-monospace", "monospace"],
      },
    },
  },
  plugins: [],
};
