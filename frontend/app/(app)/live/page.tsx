"use client";

import { keepPreviousData, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowUp, Filter, Newspaper, RefreshCw, Rss, X } from "lucide-react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";

import { PlacePicker } from "@/components/live/place-picker";
import { NewsDetailDrawer } from "@/components/live/news-detail";
import { NewsRow } from "@/components/news-row";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Field, Input, Select } from "@/components/ui/input";
import { PageHeader, Pagination } from "@/components/ui/page";
import { EmptyState, ErrorState, SkeletonRows } from "@/components/ui/states";
import { Switch } from "@/components/ui/switch";
import { Time } from "@/components/ui/time";
import { api } from "@/lib/api";
import { PERMS, useSession } from "@/lib/session";
import type { NewsItemOut, Page } from "@/lib/types";
import { flag, formatNumber } from "@/lib/utils";

type NewsPage = Page<NewsItemOut> & { latest_retrieved_at: string | null; server_time: string };

const PAGE_SIZE = 50;
const FILTER_KEYS = ["q", "publisher", "country", "language", "duplicates", "from", "to", "sort"] as const;

function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

export default function LivePage() {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const qc = useQueryClient();
  const { can } = useSession();

  const get = (k: string) => params.get(k) ?? "";
  const page = Math.max(1, Number(params.get("page") ?? 1) || 1);
  const itemId = params.get("item");

  const update = useCallback(
    (patch: Record<string, string | null>, resetPage = true) => {
      const next = new URLSearchParams(params.toString());
      for (const [k, v] of Object.entries(patch)) {
        if (v === null || v === "") next.delete(k);
        else next.set(k, v);
      }
      if (resetPage && !("page" in patch)) next.delete("page");
      const s = next.toString();
      router.replace(s ? `${pathname}?${s}` : pathname, { scroll: false });
    },
    [params, pathname, router],
  );

  // Search box is local state, pushed to the URL after a short pause.
  const [qInput, setQInput] = useState(get("q"));
  const debouncedQ = useDebounced(qInput, 350);
  useEffect(() => {
    if (debouncedQ.trim() !== get("q")) update({ q: debouncedQ.trim() || null });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debouncedQ]);

  const filters = useMemo(() => {
    const f: Record<string, string> = {};
    for (const k of FILTER_KEYS) {
      const v = params.get(k);
      if (v) f[k] = v;
    }
    return f;
  }, [params]);
  const activeFilterCount = Object.keys(filters).filter((k) => k !== "sort").length;
  const [showFilters, setShowFilters] = useState(activeFilterCount > 0);

  const list = useQuery({
    queryKey: ["news", "live", filters, page],
    queryFn: ({ signal }) => api<NewsPage>("/news", { query: { ...filters, page, page_size: PAGE_SIZE }, signal }),
    placeholderData: keepPreviousData,
  });

  const publishers = useQuery({
    queryKey: ["news-publishers"],
    queryFn: () => api<{ publisher: string; sources: number }[]>("/news/facets/publishers"),
    staleTime: 300_000,
  });

  // Poll for new arrivals since the list was loaded.
  const [autoRefresh, setAutoRefresh] = useState(false);
  const since = list.data?.server_time;
  const newCount = useQuery({
    queryKey: ["news-new-count", since],
    queryFn: () => api<{ count: number; server_time: string }>("/news/new-count", { query: { since } }),
    enabled: !!since,
    refetchInterval: 20_000,
  });
  const fresh = newCount.data?.count ?? 0;

  const showNew = useCallback(() => {
    if (page !== 1) update({ page: null });
    qc.invalidateQueries({ queryKey: ["news", "live"] });
    window.scrollTo({ top: 0, behavior: "smooth" });
  }, [page, qc, update]);

  useEffect(() => {
    if (autoRefresh && fresh > 0 && page === 1) qc.invalidateQueries({ queryKey: ["news", "live"] });
  }, [autoRefresh, fresh, page, qc]);

  const clearFilters = () => {
    setQInput("");
    const patch: Record<string, null> = {};
    for (const k of FILTER_KEYS) patch[k] = null;
    update(patch);
  };

  const sort = get("sort") || "latest";

  return (
    <div className="mx-auto max-w-6xl">
      <PageHeader
        title="Live News"
        description={
          list.data ? (
            <>
              {formatNumber(list.data.total)} reports{activeFilterCount ? " match your filters" : " collected"} · newest retrieved{" "}
              <Time value={list.data.latest_retrieved_at} />
            </>
          ) : (
            "Everything collected from your sources, newest first"
          )
        }
        actions={
          <>
            <Switch id="auto-refresh" checked={autoRefresh} onCheckedChange={setAutoRefresh} label="Auto-refresh" />
            <Button variant="outline" onClick={() => list.refetch()} loading={list.isFetching && !list.isLoading} aria-label="Refresh now">
              <RefreshCw className="h-4 w-4" /> Refresh
            </Button>
          </>
        }
      />

      <Card>
        <div className="flex flex-wrap items-center gap-2 border-b border-border px-3 py-2">
          <div className="relative min-w-[200px] flex-1">
            <label htmlFor="live-q" className="sr-only">
              Search headlines and summaries
            </label>
            <Input
              id="live-q"
              value={qInput}
              onChange={(e) => setQInput(e.target.value)}
              placeholder='Search headlines and summaries — e.g. "garment workers" -football'
            />
          </div>
          <label htmlFor="live-sort" className="sr-only">
            Sort
          </label>
          <Select id="live-sort" value={sort} onChange={(e) => update({ sort: e.target.value === "latest" ? null : e.target.value })} className="w-auto">
            <option value="latest">Newest published</option>
            <option value="retrieved">Newest retrieved</option>
            <option value="oldest">Oldest first</option>
          </Select>
          <Button variant={showFilters ? "secondary" : "outline"} onClick={() => setShowFilters((s) => !s)} aria-expanded={showFilters} aria-controls="live-filters">
            <Filter className="h-4 w-4" /> Filters
            {activeFilterCount ? <Badge tone="accent">{activeFilterCount}</Badge> : null}
          </Button>
          {activeFilterCount ? (
            <Button variant="ghost" onClick={clearFilters}>
              <X className="h-4 w-4" /> Clear
            </Button>
          ) : null}
        </div>

        {showFilters ? (
          <div id="live-filters" className="grid grid-cols-1 gap-3 border-b border-border bg-surface-2/40 px-3 py-3 sm:grid-cols-2 lg:grid-cols-4">
            <Field label="Publisher" htmlFor="f-publisher">
              <Select id="f-publisher" value={get("publisher")} onChange={(e) => update({ publisher: e.target.value || null })}>
                <option value="">All publishers</option>
                {publishers.data?.map((p) => (
                  <option key={p.publisher} value={p.publisher}>
                    {p.publisher}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Country" htmlFor="f-country" hint="Places detected in the report (not the publisher’s location)">
              {get("country") ? (
                <div className="flex h-8 items-center gap-2 rounded border border-border bg-surface px-2 text-sm">
                  <span className="flex-1">
                    {flag(get("country"))} {get("country")}
                  </span>
                  <button type="button" className="rounded p-0.5 text-muted hover:text-text" onClick={() => update({ country: null })} aria-label="Clear country">
                    <X className="h-3.5 w-3.5" />
                  </button>
                </div>
              ) : (
                <PlacePicker id="f-country" kinds={["country"]} placeholder="Any country" onSelect={(p) => update({ country: p.country_code ?? p.code ?? null })} />
              )}
            </Field>
            <Field label="Language" htmlFor="f-language" hint="ISO code, e.g. en, fr, bn">
              <Input
                id="f-language"
                key={get("language")}
                defaultValue={get("language")}
                maxLength={8}
                onBlur={(e) => update({ language: e.target.value.trim().toLowerCase() || null })}
                onKeyDown={(e) => e.key === "Enter" && update({ language: (e.target as HTMLInputElement).value.trim().toLowerCase() || null })}
              />
            </Field>
            <Field label="Duplicates" htmlFor="f-dup">
              <Select id="f-dup" value={get("duplicates") || "include"} onChange={(e) => update({ duplicates: e.target.value === "include" ? null : e.target.value })}>
                <option value="include">Show duplicates</option>
                <option value="exclude">Hide duplicates</option>
                <option value="only">Only duplicates</option>
              </Select>
            </Field>
            <Field label="From" htmlFor="f-from">
              <Input id="f-from" type="date" value={get("from")} max={get("to") || undefined} onChange={(e) => update({ from: e.target.value || null })} />
            </Field>
            <Field label="To" htmlFor="f-to">
              <Input id="f-to" type="date" value={get("to")} min={get("from") || undefined} onChange={(e) => update({ to: e.target.value || null })} />
            </Field>
          </div>
        ) : null}

        {fresh > 0 && !autoRefresh ? (
          <div className="sticky top-12 z-20 flex justify-center border-b border-border bg-accent/5 py-1.5" role="status">
            <Button size="sm" variant="primary" onClick={showNew}>
              <ArrowUp className="h-3.5 w-3.5" /> {formatNumber(fresh)} new item{fresh === 1 ? "" : "s"} collected — show
            </Button>
          </div>
        ) : null}

        {list.isLoading ? (
          <SkeletonRows rows={10} />
        ) : list.isError ? (
          <ErrorState error={list.error} onRetry={() => list.refetch()} />
        ) : !list.data?.items.length ? (
          activeFilterCount ? (
            <EmptyState icon={Filter} title="No reports match these filters" description="Widen the date range or clear some filters." action={<Button onClick={clearFilters}>Clear filters</Button>} />
          ) : (
            <EmptyState
              icon={Newspaper}
              title="Nothing collected yet"
              description="Reports appear here as soon as the worker fetches your sources. Make sure at least one source is active and the worker is running."
              action={
                can(PERMS.sourcesWrite) ? (
                  <Button variant="primary" onClick={() => router.push("/sources?new=1")}>
                    <Rss className="h-4 w-4" /> Add a source
                  </Button>
                ) : null
              }
            />
          )
        ) : (
          <div aria-live="polite" aria-busy={list.isFetching}>
            {list.data.items.map((n) => (
              <NewsRow key={n.id} item={n} active={n.id === itemId} onOpen={(it) => update({ item: it.id }, false)} />
            ))}
          </div>
        )}
        {list.data && list.data.total > PAGE_SIZE ? (
          <Pagination
            page={page}
            pageSize={PAGE_SIZE}
            total={list.data.total}
            onPage={(p) => {
              update({ page: p === 1 ? null : String(p) }, false);
              window.scrollTo({ top: 0 });
            }}
          />
        ) : null}
      </Card>

      <NewsDetailDrawer id={itemId} onClose={() => update({ item: null }, false)} onOpenItem={(id) => update({ item: id }, false)} />
    </div>
  );
}
