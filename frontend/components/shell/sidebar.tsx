"use client";

import { PanelLeftClose, PanelLeftOpen } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { Tip } from "@/components/ui/tooltip";
import { APP_NAME, APP_SHORT_NAME, APP_TAGLINE, LOGO_SQUARE, LOGO_WIDE } from "@/lib/brand";
import { cn } from "@/lib/utils";

import { isActive, NAV } from "./nav";

export function Brand({ collapsed }: { collapsed?: boolean }) {
  // Plain <img>: the logo is a small static file, and it must also show before anyone signs in.
  return (
    <Link href="/" className="flex min-w-0 items-center gap-2 px-1" aria-label={`${APP_NAME} home`}>
      {collapsed ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img src={LOGO_SQUARE} alt="" width={32} height={32} className="h-8 w-8 shrink-0 rounded" />
      ) : (
        <>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={LOGO_WIDE} alt={APP_SHORT_NAME} width={77} height={36} className="h-9 w-auto shrink-0 rounded" />
          <span className="text-[10px] font-semibold uppercase leading-[1.15] tracking-wide text-muted">{APP_TAGLINE}</span>
        </>
      )}
    </Link>
  );
}

export function NavLinks({ collapsed, onNavigate }: { collapsed?: boolean; onNavigate?: () => void }) {
  const pathname = usePathname();
  return (
    <nav aria-label="Main" className="flex flex-col gap-0.5">
      {NAV.map((item) => {
        const active = isActive(pathname, item.href);
        const link = (
          <Link
            key={item.href}
            href={item.href}
            onClick={onNavigate}
            aria-current={active ? "page" : undefined}
            className={cn(
              "flex h-8 items-center gap-2.5 rounded px-2 text-sm transition-colors",
              active ? "bg-accent/10 font-medium text-accent" : "text-muted hover:bg-surface-2 hover:text-text",
              collapsed && "justify-center px-0",
            )}
          >
            <item.icon className="h-4 w-4 shrink-0" />
            {!collapsed ? <span className="truncate">{item.label}</span> : <span className="sr-only">{item.label}</span>}
          </Link>
        );
        return collapsed ? (
          <Tip key={item.href} content={item.label} side="right">
            {link}
          </Tip>
        ) : (
          link
        );
      })}
    </nav>
  );
}

export function Sidebar({ collapsed, onToggle }: { collapsed: boolean; onToggle: () => void }) {
  return (
    <aside
      className={cn(
        "sticky top-0 hidden h-screen shrink-0 flex-col border-r border-border bg-surface transition-[width] duration-150 lg:flex",
        collapsed ? "w-[56px]" : "w-[224px]",
      )}
    >
      <div className={cn("flex h-12 items-center border-b border-border px-3", collapsed && "justify-center px-0")}>
        <Brand collapsed={collapsed} />
      </div>
      <div className="scroll-thin flex-1 overflow-y-auto p-2">
        <NavLinks collapsed={collapsed} />
      </div>
      <div className={cn("border-t border-border p-2", collapsed && "flex justify-center")}>
        <button
          type="button"
          onClick={onToggle}
          className="flex h-8 w-full items-center gap-2 rounded px-2 text-xs text-muted hover:bg-surface-2 hover:text-text"
          aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
        >
          {collapsed ? <PanelLeftOpen className="mx-auto h-4 w-4" /> : <PanelLeftClose className="h-4 w-4" />}
          {!collapsed ? "Collapse" : null}
        </button>
      </div>
    </aside>
  );
}
