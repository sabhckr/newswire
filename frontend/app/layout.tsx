import type { Metadata, Viewport } from "next";

import { APP_NAME, APP_SHORT_NAME } from "@/lib/brand";

import "./globals.css";
import { Providers } from "./providers";

export const metadata: Metadata = {
  // The browser-tab and home-screen icons are app/icon.png and app/apple-icon.png (the logo).
  title: { default: APP_NAME, template: `%s · ${APP_SHORT_NAME}` },
  applicationName: APP_NAME,
  description: "Samyabadi News Automation Tool: news monitoring, research and publishing",
  robots: { index: false, follow: false },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#f6f7f9" },
    { media: "(prefers-color-scheme: dark)", color: "#0d1117" },
  ],
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  // next-themes sets the class before first paint (inline script), so there is no theme flash.
  return (
    <html lang="en" suppressHydrationWarning>
      <body>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
