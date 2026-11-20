"use client";

import * as D from "@radix-ui/react-dialog";
import { useQuery } from "@tanstack/react-query";
import { Command } from "cmdk";
import { Clock, FileText, Loader2, Newspaper, Rss, Search } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { api } from "@/lib/api";
import type { SearchGroup } from "@/lib/types";
import { relativeTime } from "@/lib/utils";

import { NAV } from "./nav";

// The kinds of result this app has a page for (the server also knows headlines and clusters).
const ICONS: Partial<Record<SearchGroup["type"], React.ComponentType<{ className?: string }>>> = {
  news: Newspaper,
  articles: FileText,
  sources: Rss,
};

function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

export function CommandPalette({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const router = useRouter();
  const [q, setQ] = useState("");
  const term = useDebounced(q.trim(), 250);
  const results = useQuery({
    queryKey: ["search", term],
    queryFn: ({ signal }) => api<{ groups: SearchGroup[] }>("/search", { query: { q: term }, signal }),
    enabled: open && term.length >= 2,
    staleTime: 30_000,
  });
  const history = useQuery({
    queryKey: ["search-history"],
    queryFn: () => api<{ items: string[] }>("/search/history"),
    enabled: open,
  });

  useEffect(() => {
    if (!open) setQ("");
  }, [open]);

  const go = (href: string) => {
    onOpenChange(false);
    router.push(href);
  };
  const groups = results.data?.groups.filter((g) => g.items.length && ICONS[g.type]) ?? [];
  const navMatches = NAV.filter((n) => !q || n.label.toLowerCase().includes(q.toLowerCase()));

  return (
    <D.Root open={open} onOpenChange={onOpenChange}>
      <D.Portal>
        <D.Overlay className="fixed inset-0 z-50 bg-black/50 animate-fade-in" />
        <D.Content className="fixed left-1/2 top-[10vh] z-50 w-[calc(100vw-2rem)] max-w-xl -translate-x-1/2 overflow-hidden rounded-lg border border-border bg-surface shadow-pop animate-slide-in">
          <D.Title className="sr-only">Search Samyabadi</D.Title>
          <D.Description className="sr-only">Search news, articles and sources</D.Description>
          <Command shouldFilter={false} label="Global search">
            <div className="flex items-center gap-2 border-b border-border px-3">
              <Search className="h-4 w-4 text-muted" aria-hidden />
              <Command.Input
                value={q}
                onValueChange={setQ}
                placeholder="Search news, articles, sources…"
                className="h-11 flex-1 bg-transparent text-sm outline-none placeholder:text-muted"
              />
              {results.isFetching ? <Loader2 className="h-4 w-4 animate-spin text-muted" /> : null}
            </div>
            <Command.List className="scroll-thin max-h-[60vh] overflow-y-auto p-1">
              {term.length >= 2 && !results.isFetching && groups.length === 0 ? (
                <Command.Empty className="px-3 py-6 text-center text-sm text-muted">No results for “{term}”.</Command.Empty>
              ) : null}
              {groups.map((g) => {
                const Icon = ICONS[g.type] ?? FileText;
                return (
                  <Command.Group key={g.type} heading={g.label} className="px-1 pb-1 text-2xs font-semibold uppercase tracking-wide text-muted [&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:py-1.5">
                    {g.items.map((it) => (
                      <Command.Item
                        key={`${g.type}-${it.id}`}
                        value={`${g.type}-${it.id}`}
                        onSelect={() => go(it.href)}
                        className="flex cursor-pointer items-center gap-2 rounded px-2 py-1.5 text-sm normal-case tracking-normal text-text data-[selected=true]:bg-surface-2"
                      >
                        <Icon className="h-4 w-4 shrink-0 text-muted" />
                        <span className="min-w-0 flex-1 truncate">{it.title}</span>
                        {it.subtitle ? <span className="hidden max-w-[35%] truncate text-xs text-muted sm:inline">{it.subtitle}</span> : null}
                        {it.at ? <span className="shrink-0 text-2xs text-muted">{relativeTime(it.at)}</span> : null}
                      </Command.Item>
                    ))}
                  </Command.Group>
                );
              })}
              {term.length < 2 && history.data?.items.length ? (
                <Command.Group heading="Recent searches" className="px-1 pb-1 text-2xs font-semibold uppercase tracking-wide text-muted [&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:py-1.5">
                  {history.data.items.slice(0, 6).map((h) => (
                    <Command.Item
                      key={h}
                      value={`history-${h}`}
                      onSelect={() => setQ(h)}
                      className="flex cursor-pointer items-center gap-2 rounded px-2 py-1.5 text-sm normal-case tracking-normal text-text data-[selected=true]:bg-surface-2"
                    >
                      <Clock className="h-4 w-4 text-muted" />
                      {h}
                    </Command.Item>
                  ))}
                </Command.Group>
              ) : null}
              {navMatches.length ? (
                <Command.Group heading="Go to" className="px-1 pb-1 text-2xs font-semibold uppercase tracking-wide text-muted [&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:py-1.5">
                  {navMatches.map((n) => (
                    <Command.Item
                      key={n.href}
                      value={`nav-${n.href}`}
                      onSelect={() => go(n.href)}
                      className="flex cursor-pointer items-center gap-2 rounded px-2 py-1.5 text-sm normal-case tracking-normal text-text data-[selected=true]:bg-surface-2"
                    >
                      <n.icon className="h-4 w-4 text-muted" />
                      <span className="flex-1">{n.label}</span>
                      <span className="font-mono text-2xs text-muted">g {n.key}</span>
                    </Command.Item>
                  ))}
                </Command.Group>
              ) : null}
            </Command.List>
          </Command>
        </D.Content>
      </D.Portal>
    </D.Root>
  );
}
