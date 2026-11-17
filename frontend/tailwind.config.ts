import type { Config } from "tailwindcss";

const token = (name: string) => `rgb(var(--${name}) / <alpha-value>)`;

export default {
  darkMode: "class",
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}", "./lib/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        bg: token("bg"),
        surface: token("surface"),
        "surface-2": token("surface-2"),
        border: token("border"),
        text: token("text"),
        muted: token("muted"),
        accent: { DEFAULT: token("accent"), fg: token("accent-fg") },
        success: token("success"),
        warning: token("warning"),
        danger: token("danger"),
        info: token("info"),
      },
      fontFamily: {
        sans: ["Inter", "ui-sans-serif", "system-ui", "-apple-system", "Segoe UI", "Roboto", "Helvetica Neue", "Arial", "sans-serif"],
        serif: ["Source Serif 4", "Source Serif Pro", "Georgia", "Cambria", "Times New Roman", "serif"],
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "Consolas", "monospace"],
      },
      fontSize: { "2xs": ["11px", "16px"], xs: ["12px", "16px"], sm: ["13px", "20px"], base: ["14px", "22px"] },
      borderRadius: { DEFAULT: "6px", md: "6px", lg: "8px", xl: "10px" },
      boxShadow: { pop: "0 8px 30px -12px rgb(0 0 0 / 0.35)" },
      keyframes: {
        "fade-in": { from: { opacity: "0" }, to: { opacity: "1" } },
        "slide-in": { from: { transform: "translateY(4px)", opacity: "0" }, to: { transform: "none", opacity: "1" } },
      },
      animation: { "fade-in": "fade-in 120ms ease-out", "slide-in": "slide-in 140ms ease-out" },
    },
  },
  plugins: [],
} satisfies Config;
