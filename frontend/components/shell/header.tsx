"use client";

import * as D from "@radix-ui/react-dialog";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Activity,
  Bell,
  CheckCheck,
  FileText,
  LogOut,
  Menu,
  Monitor,
  Moon,
  Plus,
  Rss,
  Search,
  Server,
  Settings,
  Sun,
  Target,
  User,
  X,
} from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useTheme } from "next-themes";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Dropdown, DropdownContent, DropdownItem, DropdownLabel, DropdownSeparator, DropdownTrigger } from "@/components/ui/dropdown";
import { Kbd } from "@/components/ui/page";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Tip } from "@/components/ui/tooltip";
import { api, errorMessage } from "@/lib/api";
import { PERMS, useSession } from "@/lib/session";
import type { ArticleOut, NotificationItem, Overview } from "@/lib/types";
import { browserTimeZone, cn, relativeTime } from "@/lib/utils";

import { Brand, NavLinks } from "./sidebar";

export function useOverview() {
  return useQuery({
    queryKey: ["overview"],
    queryFn: () => api<Overview>("/analytics/overview", { query: { tz: browserTimeZone() } }),
    refetchInterval: 30_000,
  });
}

function StatusPill() {
  const { data, isError } = useOverview();
  const worker = data?.status.worker.state;
  const tone = isError ? "danger" : worker === "running" ? (data?.status.failed_sources ? "warning" : "success") : worker ? "danger" : "neutral";
  const dot = { success: "bg-success", warning: "bg-warning", danger: "bg-danger", neutral: "bg-muted" }[tone];
  const label = isError
    ? "API unreachable"
    : !data
      ? "Checking…"
      : worker === "running"
        ? data.status.failed_sources
          ? `${data.status.failed_sources} source${data.status.failed_sources === 1 ? "" : "s"} failing`
          : "Collecting"
        : worker === "stale"
          ? "Worker stalled"
          : "Worker offline";
  const detail = data
    ? `Worker: ${worker?.replace("_", " ")}. Last collection ${relativeTime(data.status.last_collection_at)}; next poll ${relativeTime(data.status.next_poll_at)}. ${data.status.active_sources} active sources.`
    : "";
  return (
    <Tip content={detail}>
      <Link
        href="/system"
        className="hidden h-8 items-center gap-2 rounded border border-border px-2.5 text-xs text-muted hover:bg-surface-2 hover:text-text md:inline-flex"
      >
        <span className={cn("h-2 w-2 rounded-full", dot, worker === "running" && "animate-pulse")} aria-hidden />
        <span>{label}</span>
        {data?.status.last_collection_at ? (
          <span className="hidden text-muted/80 xl:inline">· {relativeTime(data.status.last_collection_at)}</span>
        ) : null}
      </Link>
    </Tip>
  );
}

function Notifications() {
  const qc = useQueryClient();
  const router = useRouter();
  const { data } = useQuery({
    queryKey: ["notifications"],
    queryFn: () => api<{ unread: number; items: NotificationItem[] }>("/notifications", { query: { limit: 30 } }),
    refetchInterval: 30_000,
  });
  const readAll = useMutation({
    mutationFn: () => api("/notifications/read-all", { method: "POST" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["notifications"] }),
  });
  const open = async (n: NotificationItem) => {
    if (!n.is_read) {
      await api(`/notifications/${n.id}/read`, { method: "POST" }).catch(() => undefined);
      qc.invalidateQueries({ queryKey: ["notifications"] });
    }
    if (n.link) router.push(n.link);
  };
  const unread = data?.unread ?? 0;
  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button variant="ghost" size="icon" aria-label={`Notifications${unread ? `, ${unread} unread` : ""}`} className="relative">
          <Bell className="h-4 w-4" />
          {unread ? (
            <span className="absolute right-1 top-1 min-w-[16px] rounded-full bg-danger px-1 text-[10px] font-semibold leading-4 text-white">
              {unread > 99 ? "99+" : unread}
            </span>
          ) : null}
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-[min(380px,calc(100vw-1rem))]">
        <div className="flex items-center justify-between border-b border-border px-3 py-2">
          <span className="text-sm font-semibold">Notifications</span>
          <Button size="sm" variant="ghost" onClick={() => readAll.mutate()} disabled={!unread}>
            <CheckCheck className="h-3.5 w-3.5" /> Mark all read
          </Button>
        </div>
        <div className="scroll-thin max-h-[60vh] overflow-y-auto">
          {!data?.items.length ? (
            <p className="px-3 py-8 text-center text-sm text-muted">Nothing new. Matches for high-priority headlines and publishing results appear here.</p>
          ) : (
            data.items.map((n) => (
              <button
                key={n.id}
                type="button"
                onClick={() => open(n)}
                className="flex w-full gap-2 border-b border-border/60 px-3 py-2 text-left last:border-0 hover:bg-surface-2"
              >
                <span className={cn("mt-1.5 h-2 w-2 shrink-0 rounded-full", n.is_read ? "bg-transparent" : "bg-accent")} aria-hidden />
                <span className="min-w-0 flex-1">
                  <span className={cn("block truncate text-sm", !n.is_read && "font-medium")}>{n.title}</span>
                  {n.body ? <span className="line-clamp-2 block text-xs text-muted">{n.body}</span> : null}
                  <span className="block text-2xs text-muted">{relativeTime(n.created_at)}</span>
                </span>
              </button>
            ))
          )}
        </div>
      </PopoverContent>
    </Popover>
  );
}

function QuickCreate() {
  const router = useRouter();
  const { can } = useSession();
  const qc = useQueryClient();
  const newArticle = useMutation({
    mutationFn: () => api<ArticleOut>("/articles", { body: { headline: "Untitled article", body_html: "" } }),
    onSuccess: (a) => {
      qc.invalidateQueries({ queryKey: ["articles"] });
      router.push(`/articles/${a.id}`);
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const items = [
    can(PERMS.assignmentsWrite) && { label: "New headline", icon: Target, onSelect: () => router.push("/headlines/new") },
    can(PERMS.sourcesWrite) && { label: "New source", icon: Rss, onSelect: () => router.push("/sources?new=1") },
    can(PERMS.articlesWrite) && { label: "New article", icon: FileText, onSelect: () => newArticle.mutate() },
  ].filter(Boolean) as { label: string; icon: typeof Target; onSelect: () => void }[];
  if (!items.length) return null;
  return (
    <Dropdown>
      <DropdownTrigger asChild>
        <Button variant="primary" size="sm" className="h-8" aria-label="Create">
          <Plus className="h-4 w-4" />
          <span className="hidden sm:inline">New</span>
        </Button>
      </DropdownTrigger>
      <DropdownContent>
        {items.map((i) => (
          <DropdownItem key={i.label} icon={i.icon} onSelect={i.onSelect}>
            {i.label}
          </DropdownItem>
        ))}
      </DropdownContent>
    </Dropdown>
  );
}

export function ThemeToggle() {
  const { theme, setTheme } = useTheme();
  const qc = useQueryClient();
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);
  const order = ["light", "dark", "system"] as const;
  const current = (mounted ? theme : "system") as (typeof order)[number];
  const next = order[(order.indexOf(current) + 1) % order.length];
  const Icon = current === "light" ? Sun : current === "dark" ? Moon : Monitor;
  const change = () => {
    setTheme(next);
    api("/auth/me", { method: "PATCH", body: { theme_preference: next } })
      .then(() => qc.invalidateQueries({ queryKey: ["me"] }))
      .catch(() => undefined);
  };
  return (
    <Tip content={`Theme: ${current}. Switch to ${next}.`}>
      <Button variant="ghost" size="icon" onClick={change} aria-label={`Theme: ${current}. Switch to ${next}`}>
        <Icon className="h-4 w-4" />
      </Button>
    </Tip>
  );
}

function UserMenu() {
  const { session } = useSession();
  const router = useRouter();
  const qc = useQueryClient();
  const signOut = async () => {
    try {
      await api("/auth/logout", { method: "POST" });
    } catch {
      /* already signed out */
    }
    qc.clear();
    router.replace("/login");
  };
  const initials = session.user.display_name
    .split(/\s+/)
    .map((p) => p[0])
    .slice(0, 2)
    .join("")
    .toUpperCase();
  return (
    <Dropdown>
      <DropdownTrigger asChild>
        <button
          type="button"
          className="flex h-8 w-8 items-center justify-center rounded-full border border-border bg-surface-2 text-xs font-semibold"
          aria-label={`Account: ${session.user.display_name}`}
        >
          {initials || <User className="h-4 w-4" />}
        </button>
      </DropdownTrigger>
      <DropdownContent className="w-60">
        <DropdownLabel>
          <span className="block truncate text-sm font-medium text-text">{session.user.display_name}</span>
          <span className="block truncate">{session.user.email}</span>
          <span className="mt-0.5 block capitalize">{session.user.role_names.join(", ")}</span>
        </DropdownLabel>
        <DropdownSeparator />
        <DropdownItem icon={Settings} onSelect={() => router.push("/settings")}>
          Settings
        </DropdownItem>
        <DropdownItem icon={Server} onSelect={() => router.push("/system")}>
          System status
        </DropdownItem>
        <DropdownItem icon={Activity} onSelect={() => router.push("/activity")}>
          Activity logs
        </DropdownItem>
        <DropdownSeparator />
        <DropdownItem icon={LogOut} onSelect={signOut}>
          Sign out
        </DropdownItem>
      </DropdownContent>
    </Dropdown>
  );
}

export function Header({ onSearch }: { onSearch: () => void }) {
  const [mobileOpen, setMobileOpen] = useState(false);
  return (
    <header className="sticky top-0 z-30 flex h-12 items-center gap-2 border-b border-border bg-surface/95 px-3 backdrop-blur supports-[backdrop-filter]:bg-surface/80 sm:px-4">
      <D.Root open={mobileOpen} onOpenChange={setMobileOpen}>
        <D.Trigger asChild>
          <Button variant="ghost" size="icon" className="lg:hidden" aria-label="Open navigation">
            <Menu className="h-4 w-4" />
          </Button>
        </D.Trigger>
        <D.Portal>
          <D.Overlay className="fixed inset-0 z-50 bg-black/50 lg:hidden" />
          <D.Content className="fixed inset-y-0 left-0 z-50 flex w-[260px] flex-col border-r border-border bg-surface p-3 animate-fade-in lg:hidden">
            <D.Title className="sr-only">Navigation</D.Title>
            <D.Description className="sr-only">Main navigation</D.Description>
            <div className="mb-3 flex items-center justify-between">
              <Brand />
              <D.Close asChild>
                <Button variant="ghost" size="icon" aria-label="Close navigation">
                  <X className="h-4 w-4" />
                </Button>
              </D.Close>
            </div>
            <NavLinks onNavigate={() => setMobileOpen(false)} />
          </D.Content>
        </D.Portal>
      </D.Root>
      <div className="lg:hidden">
        <Brand collapsed />
      </div>
      <button
        type="button"
        onClick={onSearch}
        className="flex h-8 min-w-0 flex-1 items-center gap-2 rounded border border-border bg-bg px-2.5 text-sm text-muted hover:border-accent/40 sm:max-w-md"
        aria-label="Search (Ctrl+K)"
      >
        <Search className="h-4 w-4 shrink-0" />
        <span className="truncate">Search…</span>
        <span className="ml-auto hidden sm:inline">
          <Kbd>⌘K</Kbd>
        </span>
      </button>
      <div className="ml-auto flex items-center gap-1.5">
        <StatusPill />
        <QuickCreate />
        <Notifications />
        <ThemeToggle />
        <UserMenu />
      </div>
    </header>
  );
}
