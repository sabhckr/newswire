"use client";

import { useRouter } from "next/navigation";
import { useTheme } from "next-themes";
import { useCallback, useEffect, useRef, useState } from "react";

import { ConfirmProvider } from "@/components/ui/confirm";
import { useSession } from "@/lib/session";

import { CommandPalette } from "./command-palette";
import { Header } from "./header";
import { NAV } from "./nav";
import { Sidebar } from "./sidebar";

const COLLAPSE_KEY = "newswire.sidebar.collapsed";

function isTyping(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null;
  if (!el) return false;
  return el.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName);
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const { session } = useSession();
  const { theme, setTheme } = useTheme();
  const [collapsed, setCollapsed] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const pendingG = useRef<number>(0);
  const themeSynced = useRef(false);

  useEffect(() => {
    try {
      setCollapsed(localStorage.getItem(COLLAPSE_KEY) === "1");
    } catch {
      /* storage unavailable */
    }
  }, []);

  // Apply the account's saved theme once per sign-in (localStorage keeps it between visits).
  useEffect(() => {
    const pref = session.user.theme_preference;
    if (!themeSynced.current && pref && pref !== theme) setTheme(pref);
    themeSynced.current = true;
  }, [session.user.theme_preference, theme, setTheme]);

  const toggle = useCallback(() => {
    setCollapsed((c) => {
      try {
        localStorage.setItem(COLLAPSE_KEY, c ? "0" : "1");
      } catch {
        /* ignore */
      }
      return !c;
    });
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setSearchOpen((o) => !o);
        return;
      }
      if (e.metaKey || e.ctrlKey || e.altKey || isTyping(e.target)) return;
      if (e.key === "/") {
        e.preventDefault();
        setSearchOpen(true);
        return;
      }
      const now = Date.now();
      if (e.key === "g") {
        pendingG.current = now;
        return;
      }
      if (now - pendingG.current < 1200) {
        const item = NAV.find((n) => n.key === e.key);
        pendingG.current = 0;
        if (item) {
          e.preventDefault();
          router.push(item.href);
        }
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [router]);

  return (
    <ConfirmProvider>
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:fixed focus:left-2 focus:top-2 focus:z-[100] focus:rounded focus:bg-accent focus:px-3 focus:py-2 focus:text-accent-fg"
      >
        Skip to content
      </a>
      <div className="flex min-h-screen">
        <Sidebar collapsed={collapsed} onToggle={toggle} />
        <div className="flex min-w-0 flex-1 flex-col">
          <Header onSearch={() => setSearchOpen(true)} />
          <main id="main" className="min-w-0 flex-1 px-3 py-4 sm:px-6 sm:py-5">
            {children}
          </main>
        </div>
      </div>
      <CommandPalette open={searchOpen} onOpenChange={setSearchOpen} />
    </ConfirmProvider>
  );
}
