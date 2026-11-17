"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { ArrowLeft, Layers, Lock, Search } from "lucide-react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { ClusterDetail } from "@/components/clusters/cluster-detail";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input, Select } from "@/components/ui/input";
import { PageHeader, Pagination, Toolbar } from "@/components/ui/page";
import { EmptyState, ErrorState, SkeletonRows } from "@/components/ui/states";
import { Time } from "@/components/ui/time";
import { api } from "@/lib/api";
import type { ClusterOut, Page } from "@/lib/types";
import { cn, formatNumber } from "@/lib/utils";

const PAGE_SIZE = 30;

function useDebounced<T>(value: T, ms = 300): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

export default function ClustersPage() {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const selected = params.get("id");

  const [q, setQ] = useState(params.get("q") ?? "");
  const [status, setStatus] = useState<"active" | "archived">(params.get("status") === "archived" ? "archived" : "active");
  const [minPublishers, setMinPublishers] = useState(Number(params.get("min_publishers")) || 1);
  const [sort, setSort] = useState(params.get("sort") ?? "latest");
  const [page, setPage] = useState(1);
  const term = useDebounced(q.trim());

  useEffect(() => setPage(1), [term, status, minPublishers, sort]);

  const list = useQuery({
    queryKey: ["clusters", { term, status, minPublishers, sort, page }],
    queryFn: () =>
      api<Page<ClusterOut>>("/clusters", {
        query: { q: term || undefined, status, min_publishers: minPublishers, sort, page, page_size: PAGE_SIZE },
      }),
    placeholderData: keepPreviousData,
    refetchInterval: 60_000,
  });

  const select = useCallback(
    (id: string | null) => {
      const next = new URLSearchParams(params.toString());
      if (id) next.set("id", id);
      else next.delete("id");
      const qs = next.toString();
      router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
    },
    [params, pathname, router],
  );

  const filtered = Boolean(term) || minPublishers > 1;

  return (
    <div className="mx-auto max-w-7xl">
      <PageHeader
        title="Story clusters"
        description="Reports from different publishers about the same story, grouped by duplicate detection, similar headlines and shared keywords. Editors can correct any grouping."
      />

      <div className="grid gap-4 lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]">
        <Card className={cn("min-w-0 self-start", selected && "hidden lg:block")}>
          <Toolbar>
            <div className="relative min-w-[180px] flex-1">
              <Search className="pointer-events-none absolute left-2 top-2 h-4 w-4 text-muted" aria-hidden />
              <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search titles and headlines" className="pl-8" aria-label="Search clusters" />
            </div>
            <Select aria-label="Cluster status" value={status} onChange={(e) => setStatus(e.target.value as "active" | "archived")}>
              <option value="active">Active</option>
              <option value="archived">Archived</option>
            </Select>
            <Select aria-label="Minimum publishers" value={minPublishers} onChange={(e) => setMinPublishers(Number(e.target.value))}>
              <option value={1}>Any publishers</option>
              <option value={2}>2+ publishers</option>
              <option value={3}>3+ publishers</option>
              <option value={5}>5+ publishers</option>
            </Select>
            <Select aria-label="Sort clusters" value={sort} onChange={(e) => setSort(e.target.value)}>
              <option value="latest">Latest activity</option>
              <option value="largest">Most reports</option>
              <option value="publishers">Most publishers</option>
              <option value="oldest">Oldest first</option>
            </Select>
          </Toolbar>

          {list.isLoading ? (
            <SkeletonRows rows={8} />
          ) : list.isError ? (
            <ErrorState error={list.error} onRetry={() => list.refetch()} />
          ) : !list.data?.items.length ? (
            filtered ? (
              <EmptyState
                icon={Search}
                title="No clusters match these filters"
                description="Try a broader search or allow clusters with fewer publishers."
                action={
                  <Button
                    variant="outline"
                    onClick={() => {
                      setQ("");
                      setMinPublishers(1);
                    }}
                  >
                    Clear filters
                  </Button>
                }
              />
            ) : status === "archived" ? (
              <EmptyState icon={Layers} title="No archived clusters" description="Clusters you archive, and clusters left empty after edits, appear here." />
            ) : (
              <EmptyState
                icon={Layers}
                title="No story clusters yet"
                description="Clusters form automatically once two reports about the same story are collected. Add more sources covering the same beats to see them."
                action={
                  <Button variant="outline" onClick={() => router.push("/sources")}>
                    Manage sources
                  </Button>
                }
              />
            )
          ) : (
            <>
              <ul aria-label="Clusters" className={cn(list.isFetching && list.isPlaceholderData && "opacity-60")}>
                {list.data.items.map((c) => {
                  const active = c.id === selected;
                  return (
                    <li key={c.id} className="border-b border-border/70 last:border-0">
                      <button
                        type="button"
                        onClick={() => select(c.id)}
                        aria-current={active ? "true" : undefined}
                        className={cn(
                          "flex w-full flex-col gap-1 px-3 py-2.5 text-left hover:bg-surface-2/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent",
                          active && "bg-accent/10 shadow-[inset_3px_0_0] shadow-accent",
                        )}
                      >
                        <span className="flex items-start gap-2">
                          <span className="min-w-0 flex-1 text-sm font-medium leading-snug">{c.title}</span>
                          {c.is_locked ? (
                            <Lock className="mt-0.5 h-3.5 w-3.5 shrink-0 text-warning" aria-label="Locked" />
                          ) : null}
                        </span>
                        <span className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted">
                          <Badge tone={c.publisher_count >= 3 ? "accent" : "neutral"}>
                            {formatNumber(c.item_count)} reports · {formatNumber(c.publisher_count)} publishers
                          </Badge>
                          <span>
                            latest <Time value={c.last_seen_at} />
                          </span>
                        </span>
                        {c.publishers?.length ? (
                          <span className="truncate text-xs text-muted">
                            {c.publishers.slice(0, 4).join(", ")}
                            {c.publishers.length > 4 ? ` +${c.publishers.length - 4}` : ""}
                          </span>
                        ) : null}
                      </button>
                    </li>
                  );
                })}
              </ul>
              <Pagination page={page} pageSize={PAGE_SIZE} total={list.data.total} onPage={setPage} className="border-t border-border" />
            </>
          )}
        </Card>

        <div className={cn("min-w-0", !selected && "hidden lg:block")}>
          {selected ? (
            <>
              <Button variant="ghost" size="sm" className="mb-2 lg:hidden" onClick={() => select(null)}>
                <ArrowLeft className="h-4 w-4" /> All clusters
              </Button>
              <ClusterDetail id={selected} onGone={() => select(null)} />
            </>
          ) : (
            <Card>
              <EmptyState
                icon={Layers}
                title="Select a cluster"
                description="See every report in time order, how each one joined, which publishers covered it and where it happened."
              />
            </Card>
          )}
        </div>
      </div>
    </div>
  );
}
