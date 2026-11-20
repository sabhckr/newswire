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
        // Bangla glyphs fall through the Latin fonts to the first installed Bengali font
        // (Noto/Hind/SolaimanLipi where installed, Kohinoor on macOS, Nirmala UI on Windows).
        sans: [
          "Inter", "ui-sans-serif", "system-ui", "-apple-system", "Segoe UI", "Roboto", "Helvetica Neue", "Arial",
          "Noto Sans Bengali", "Hind Siliguri", "SolaimanLipi", "Kohinoor Bangla", "Bangla Sangam MN", "Nirmala UI", "Vrinda",
          "sans-serif",
        ],
        serif: [
          "Source Serif 4", "Source Serif Pro", "Georgia", "Cambria", "Times New Roman",
          "Noto Serif Bengali", "SolaimanLipi", "Kohinoor Bangla", "Bangla Sangam MN", "Nirmala UI", "Vrinda",
          "serif",
        ],
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "Consolas", "monospace"],
      },
      fontSize: { "2xs": ["11px", "16px"], xs: ["12px", "16px"], sm: ["13px", "20px"], base: ["14px", "22px"] },
      borderRadius: { DEFAULT: "6px", md: "6px", lg: "8px", xl: "10px" },
      boxShadow: { pop: "0 8px 30px -12px rgb(0 0 0 / 0.35)" },
      keyframes: {
        "fade-in": { from: { opacity: "0" }, to: { opacity: "1" } },
        // `translate`, not `transform`: dialogs are centred with a transform (-translate-x-1/2), and
        // animating `transform` replaced it, so a dialog opened half its width to the right and
        // then jumped into place.
        "slide-in": { from: { translate: "0 4px", opacity: "0" }, to: { translate: "0 0", opacity: "1" } },
      },
      animation: { "fade-in": "fade-in 120ms ease-out", "slide-in": "slide-in 140ms ease-out" },
    },
  },
  plugins: [],
} satisfies Config;
