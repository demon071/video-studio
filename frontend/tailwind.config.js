/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        // Editor chrome (CapCut-like dark surface scale)
        surface: {
          0: "#0b0d10",
          1: "#12151a",
          2: "#181c23",
          3: "#1f242c",
          4: "#272d37",
        },
        border: {
          DEFAULT: "#2b323d",
          strong: "#3a4351",
        },
        brand: {
          DEFAULT: "#22d3ee",
          hover: "#06b6d4",
          muted: "#0e7490",
        },
        accent: {
          DEFAULT: "#f472b6",
        },
        track: {
          video: "#2b6cb0",
          subtitle: "#b7791f",
          dubbed: "#2f855a",
          audio: "#6b46c1",
        },
        playhead: "#ef4444",
      },
      fontFamily: {
        sans: ["Inter", "Segoe UI", "system-ui", "sans-serif"],
        mono: ["JetBrains Mono", "Cascadia Code", "Consolas", "monospace"],
      },
      spacing: {
        timeline: "17rem",
        sidebar: "21rem",
      },
      boxShadow: {
        panel: "0 1px 0 0 rgba(255,255,255,0.04) inset, 0 8px 24px -12px rgba(0,0,0,0.8)",
      },
      keyframes: {
        "pulse-rec": {
          "0%, 100%": { opacity: "1" },
          "50%": { opacity: "0.35" },
        },
      },
      animation: {
        "pulse-rec": "pulse-rec 1.4s ease-in-out infinite",
      },
    },
  },
  plugins: [],
};
