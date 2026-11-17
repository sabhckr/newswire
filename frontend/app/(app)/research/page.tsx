"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { MapPin, Microscope, Plus, Search } from "lucide-react";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import { LinkButton } from "@/components/assignments/link-button";
import { useUrlState } from "@/components/research/types";
import { Card } from "@/components/ui/card";
import { Input, Select } from "@/components/ui/input";
import { PageHeader, Pagination } from "@/components/ui/page";
import { EmptyState, ErrorState, SkeletonRows } from "@/components/ui/states";
import { PriorityBadge, StatusBadge } from "@/components/ui/status-badge";
import { Table, TD, TH, THead, TR } from "@/components/ui/table";
import { Time } from "@/components/ui/time";
import { api } from "@/lib/api";
import { PERMS, useSession } from "@/lib/session";
import type { AssignmentOut, Page } from "@/lib/types";
import { formatNumber } from "@/lib/utils";

const PAGE_SIZE = 50;
const SCOPES: Record<string, string[]> = {
  current: ["active", "paused"],
  active: ["active"],
  paused: ["paused"],
  archived: ["archived"],
  all: ["active", "paused", "archived"],
};

function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

export default function ResearchIndexPage() {
  const { can } = useSession();
  const { get, update } = useUrlState();
  const scope = SCOPES[get("scope")] ? get("scope") : "current";
  const sort = get("sort") || "last_matched";
  const page = Math.max(1, Number(get("page") || 1) || 1);
  const [qInput, setQInput] = useState(get("q"));
  const q = useDebounced(qInput.trim(), 300);
  useEffect(() => {
    if (q !== get("q")) update({ q: q || null, page: null });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q]);

  const query = useMemo(() => ({ status: SCOPES[scope], q: q || undefined, sort, page, page_size: PAGE_SIZE }), [scope, q, sort, page]);
  const list = useQuery({
    queryKey: ["assignments", "research-index", query],
    queryFn: ({ signal }) => api<Page<AssignmentOut>>("/assignments", { query, signal }),
    placeholderData: keepPreviousData,
  });
  const items = list.data?.items ?? [];

  return (
    <div className="mx-auto max-w-7xl">
      <PageHeader
        title="Research"
        description="One workspace per headline: matched reports to review, the timeline, who reported it first, places, related clusters, images and notes."
      />
      <Card>
        <div className="flex flex-wrap items-center gap-2 border-b border-border px-3 py-2">
          <div className="relative min-w-[200px] flex-1">
            <Search className="pointer-events-none absolute left-2.5 top-2 h-4 w-4 text-muted" aria-hidden />
            <label htmlFor="r-q" className="sr-only">
              Search headlines
            </label>
            <Input id="r-q" value={qInput} onChange={(e) => setQInput(e.target.value)} placeholder="Search headlines and keywords…" className="pl-8" />
          </div>
          <label htmlFor="r-scope" className="sr-only">
            Headline status
          </label>
          <Select id="r-scope" value={scope} onChange={(e) => update({ scope: e.target.value === "current" ? null : e.target.value, page: null })} className="w-auto">
            <option value="current">Active and paused</option>
            <option value="active">Active only</option>
            <option value="paused">Paused only</option>
            <option value="archived">Archived</option>
            <option value="all">All headlines</option>
          </Select>
          <label htmlFor="r-sort" className="sr-only">
            Sort
          </label>
          <Select id="r-sort" value={sort} onChange={(e) => update({ sort: e.target.value === "last_matched" ? null : e.target.value, page: null })} className="w-auto">
            <option value="last_matched">Latest match</option>
            <option value="matches">Most matches</option>
            <option value="priority">Priority</option>
            <option value="updated">Recently edited</option>
            <option value="headline">Headline A–Z</option>
          </Select>
        </div>

        {list.isLoading ? (
          <SkeletonRows rows={8} />
        ) : list.isError ? (
          <ErrorState error={list.error} onRetry={() => list.refetch()} />
        ) : !items.length ? (
          q || scope !== "current" ? (
            <EmptyState icon={Search} title="No headlines match" description="Try a different search or status." />
          ) : (
            <EmptyState
              icon={Microscope}
              title="Nothing to research yet"
              description="Research workspaces are created from headlines. Create one with keywords and places, and matching reports collect here."
              action={
                can(PERMS.assignmentsWrite) ? (
                  <LinkButton href="/headlines/new" variant="primary">
                    <Plus className="h-4 w-4" /> New headline
                  </LinkButton>
                ) : null
              }
            />
          )
        ) : (
          <Table>
            <THead>
              <tr>
                <TH>Headline</TH>
                <TH>Research</TH>
                <TH className="text-right">Matches</TH>
                <TH className="text-right">To review</TH>
                <TH className="hidden md:table-cell">Last match</TH>
                <TH className="hidden lg:table-cell">Priority</TH>
              </tr>
            </THead>
            <tbody aria-busy={list.isFetching}>
              {items.map((a) => (
                <TR key={a.id}>
                  <TD className="max-w-0 py-2">
                    <Link href={`/research/${a.id}`} className="block truncate font-medium hover:text-accent" title={a.headline}>
                      {a.headline}
                    </Link>
                    <div className="mt-0.5 flex items-center gap-2 truncate text-xs text-muted">
                      {a.status !== "active" ? <StatusBadge status={a.status} /> : null}
                      {a.geographies.length ? (
                        <span className="inline-flex items-center gap-0.5 truncate">
                          <MapPin className="h-3 w-3 shrink-0" aria-hidden />
                          {a.geographies.slice(0, 3).map((g) => g.name).join(", ")}
                        </span>
                      ) : null}
                    </div>
                  </TD>
                  <TD>
                    <StatusBadge status={a.research_status} />
                  </TD>
                  <TD className="tabular text-right">{formatNumber(a.match_count)}</TD>
                  <TD className="tabular text-right">
                    {a.unreviewed_count ? (
                      <Link href={`/research/${a.id}?m_status=unreviewed`} className="rounded bg-info/10 px-1.5 py-0.5 text-xs font-medium text-info hover:bg-info/20">
                        {formatNumber(a.unreviewed_count)}
                      </Link>
                    ) : (
                      <span className="text-muted">0</span>
                    )}
                  </TD>
                  <TD className="hidden whitespace-nowrap text-xs text-muted md:table-cell">
                    <Time value={a.last_matched_at} />
                  </TD>
                  <TD className="hidden lg:table-cell">
                    <PriorityBadge priority={a.priority} />
                  </TD>
                </TR>
              ))}
            </tbody>
          </Table>
        )}
        {list.data && list.data.total > PAGE_SIZE ? (
          <Pagination page={page} pageSize={PAGE_SIZE} total={list.data.total} onPage={(p) => update({ page: p === 1 ? null : String(p) })} />
        ) : null}
      </Card>
    </div>
  );
}
