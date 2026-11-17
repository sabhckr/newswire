"use client";

import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Archive, Check, ChevronDown, ChevronRight, Copy, ExternalLink, Filter, Inbox, RotateCcw, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import { asBreakdown, ScoreBreakdown, ScorePill } from "@/components/assignments/score-breakdown";
import { useUrlState, type Workspace } from "@/components/research/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox, Field, Input, Select, Textarea } from "@/components/ui/input";
import { Pagination } from "@/components/ui/page";
import { EmptyState, ErrorState, SkeletonRows } from "@/components/ui/states";
import { StatusBadge } from "@/components/ui/status-badge";
import { Time } from "@/components/ui/time";
import { Tip } from "@/components/ui/tooltip";
import { api, errorMessage } from "@/lib/api";
import { PERMS, useSession } from "@/lib/session";
import type { MatchOut, Page } from "@/lib/types";
import { cn, flag, formatNumber } from "@/lib/utils";

type Review = "unreviewed" | "verified" | "rejected" | "archived";
const PAGE_SIZE = 50;
const FILTER_KEYS = ["m_status", "m_sort", "m_publisher", "m_country", "m_dup", "m_min", "m_lang"] as const;

export function MatchesTab({ ws, onOpenItem }: { ws: Workspace; onOpenItem: (newsItemId: string) => void }) {
  const a = ws.assignment;
  const qc = useQueryClient();
  const { can } = useSession();
  const write = can(PERMS.researchWrite);
  const { get, update } = useUrlState();

  const status = get("m_status");
  const sort = get("m_sort") || "relevance";
  const page = Math.max(1, Number(get("m_page") || 1) || 1);
  const setFilter = (patch: Record<string, string | null>) => update({ ...patch, m_page: null });
  const activeFilters = FILTER_KEYS.filter((k) => k !== "m_sort" && get(k)).length;
  const [showFilters, setShowFilters] = useState(activeFilters > 0);

  const query = useMemo(
    () => ({
      review_status: status ? [status] : undefined,
      sort,
      publisher: get("m_publisher") || undefined,
      country: get("m_country") || undefined,
      language: get("m_lang") || undefined,
      duplicates: get("m_dup") || undefined,
      min_score: get("m_min") || undefined,
      page,
      page_size: PAGE_SIZE,
    }),
    [status, sort, get, page],
  );
  const list = useQuery({
    queryKey: ["matches", a.id, query],
    queryFn: ({ signal }) => api<Page<MatchOut>>(`/assignments/${a.id}/matches`, { query, signal }),
    placeholderData: keepPreviousData,
  });

  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [expanded, setExpanded] = useState<string | null>(null);
  const queryHash = JSON.stringify(query);
  useEffect(() => setSelected(new Set()), [queryHash]);

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["matches", a.id] });
    qc.invalidateQueries({ queryKey: ["research", a.id] });
    qc.invalidateQueries({ queryKey: ["assignments"] });
  };

  const review = useMutation({
    mutationFn: ({ id, review_status, notes }: { id: string; review_status?: Review; notes?: string; prev?: string }) =>
      api<MatchOut>(`/assignments/matches/${id}`, { method: "PATCH", body: { review_status, notes } }),
    onSuccess: (m, vars) => {
      refresh();
      if (vars.review_status && vars.prev && vars.prev !== vars.review_status) {
        const prev = vars.prev as Review;
        toast.success(`Marked ${vars.review_status}`, {
          description: m.news_item.title,
          action: { label: "Undo", onClick: () => review.mutate({ id: m.id, review_status: prev }) },
        });
      } else if (vars.notes !== undefined) {
        toast.success("Note saved");
      }
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  const bulk = useMutation({
    mutationFn: (review_status: Review) => api<{ updated: number }>("/assignments/matches/bulk", { method: "POST", body: { ids: [...selected], review_status } }),
    onSuccess: (r, s) => {
      toast.success(`${formatNumber(r.updated)} match${r.updated === 1 ? "" : "es"} marked ${s}`);
      setSelected(new Set());
      refresh();
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  const items = list.data?.items ?? [];
  const allSelected = items.length > 0 && items.every((m) => selected.has(m.id));
  const toggle = (id: string) =>
    setSelected((s) => {
      const n = new Set(s);
      if (n.has(id)) n.delete(id);
      else n.add(id);
      return n;
    });
  const clearFilters = () => {
    const patch: Record<string, null> = { m_page: null };
    for (const k of FILTER_KEYS) patch[k] = null;
    update(patch);
  };
  const countries = ws.geography.map((g) => g.country_code);
  const counts = ws.stats.by_review_status;

  return (
    <div>
      <div className="flex flex-wrap items-center gap-2 border-b border-border px-3 py-2">
        <div role="radiogroup" aria-label="Review status" className="flex flex-wrap gap-1">
          {(
            [
              ["", "Open", (counts.unreviewed ?? 0) + (counts.verified ?? 0) + (counts.rejected ?? 0)],
              ["unreviewed", "To review", counts.unreviewed ?? 0],
              ["verified", "Verified", counts.verified ?? 0],
              ["rejected", "Rejected", counts.rejected ?? 0],
              ["archived", "Archived", counts.archived ?? 0],
            ] as const
          ).map(([v, label, n]) => (
            <button
              key={v}
              type="button"
              role="radio"
              aria-checked={status === v}
              onClick={() => setFilter({ m_status: v || null })}
              className={cn(
                "rounded border px-2 py-1 text-xs",
                status === v ? "border-accent bg-accent/10 font-medium text-text" : "border-border text-muted hover:text-text",
              )}
            >
              {label} <span className="tabular">{formatNumber(n)}</span>
            </button>
          ))}
        </div>
        <div className="ml-auto flex items-center gap-2">
          <label htmlFor="m-sort" className="sr-only">
            Sort matches
          </label>
          <Select id="m-sort" value={sort} onChange={(e) => setFilter({ m_sort: e.target.value === "relevance" ? null : e.target.value })} className="w-auto">
            <option value="relevance">Most relevant</option>
            <option value="latest">Newest</option>
            <option value="oldest">Oldest</option>
          </Select>
          <Button variant={showFilters ? "secondary" : "outline"} onClick={() => setShowFilters((s) => !s)} aria-expanded={showFilters} aria-controls="m-filters">
            <Filter className="h-4 w-4" /> Filters {activeFilters ? <Badge tone="accent">{activeFilters}</Badge> : null}
          </Button>
          {activeFilters ? (
            <Button variant="ghost" onClick={clearFilters}>
              <X className="h-4 w-4" /> Clear
            </Button>
          ) : null}
        </div>
      </div>

      {showFilters ? (
        <div id="m-filters" className="grid grid-cols-1 gap-3 border-b border-border bg-surface-2/40 px-3 py-3 sm:grid-cols-2 lg:grid-cols-5">
          <Field label="Publisher" htmlFor="m-publisher">
            <Select id="m-publisher" value={get("m_publisher")} onChange={(e) => setFilter({ m_publisher: e.target.value || null })}>
              <option value="">All publishers</option>
              {ws.publishers.map((p) => (
                <option key={p.publisher} value={p.publisher}>
                  {p.publisher} ({p.items})
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Country" htmlFor="m-country" hint="Detected in the report">
            <Select id="m-country" value={get("m_country")} onChange={(e) => setFilter({ m_country: e.target.value || null })}>
              <option value="">Any country</option>
              {countries.map((c) => (
                <option key={c} value={c}>
                  {flag(c)} {c}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Language" htmlFor="m-lang" hint="ISO code, e.g. en">
            <Input
              id="m-lang"
              key={get("m_lang")}
              defaultValue={get("m_lang")}
              maxLength={8}
              onBlur={(e) => setFilter({ m_lang: e.target.value.trim().toLowerCase() || null })}
              onKeyDown={(e) => e.key === "Enter" && setFilter({ m_lang: (e.target as HTMLInputElement).value.trim().toLowerCase() || null })}
            />
          </Field>
          <Field label="Duplicates" htmlFor="m-dup">
            <Select id="m-dup" value={get("m_dup") || "include"} onChange={(e) => setFilter({ m_dup: e.target.value === "include" ? null : e.target.value })}>
              <option value="include">Show duplicates</option>
              <option value="exclude">Hide duplicates</option>
              <option value="only">Only duplicates</option>
            </Select>
          </Field>
          <Field label="Minimum score" htmlFor="m-min" hint={`Headline threshold is ${Math.round(a.min_score)}`}>
            <Input
              id="m-min"
              key={get("m_min")}
              type="number"
              min={0}
              defaultValue={get("m_min")}
              onBlur={(e) => setFilter({ m_min: e.target.value.trim() || null })}
              onKeyDown={(e) => e.key === "Enter" && setFilter({ m_min: (e.target as HTMLInputElement).value.trim() || null })}
            />
          </Field>
        </div>
      ) : null}

      {selected.size > 0 && write ? (
        <div className="flex flex-wrap items-center gap-2 border-b border-border bg-accent/5 px-3 py-2" role="region" aria-label="Bulk review">
          <span className="text-sm font-medium">{selected.size} selected</span>
          <Button size="sm" variant="outline" onClick={() => bulk.mutate("verified")} disabled={bulk.isPending}>
            <Check className="h-3.5 w-3.5" /> Verify
          </Button>
          <Button size="sm" variant="outline" onClick={() => bulk.mutate("rejected")} disabled={bulk.isPending}>
            <X className="h-3.5 w-3.5" /> Reject
          </Button>
          <Button size="sm" variant="outline" onClick={() => bulk.mutate("archived")} disabled={bulk.isPending}>
            <Archive className="h-3.5 w-3.5" /> Archive
          </Button>
          <Button size="sm" variant="ghost" onClick={() => bulk.mutate("unreviewed")} disabled={bulk.isPending}>
            <RotateCcw className="h-3.5 w-3.5" /> Mark unreviewed
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setSelected(new Set())}>
            Clear
          </Button>
        </div>
      ) : null}

      {list.isLoading ? (
        <SkeletonRows rows={8} />
      ) : list.isError ? (
        <ErrorState error={list.error} onRetry={() => list.refetch()} />
      ) : !items.length ? (
        activeFilters || status ? (
          <EmptyState
            icon={Filter}
            title="No matches with these filters"
            description={status === "unreviewed" ? "Everything here has been reviewed." : "Try another review status or clear the filters."}
            action={activeFilters ? <Button onClick={clearFilters}>Clear filters</Button> : null}
          />
        ) : (
          <EmptyState
            icon={Inbox}
            title="No matches yet"
            description={
              a.status === "active"
                ? "New reports are scored as they are collected. If nothing appears, broaden the keywords or lower the minimum score."
                : "This headline is not active, so new reports are not being scored."
            }
          />
        )
      ) : (
        <>
          {write ? (
            <div className="flex items-center gap-2 border-b border-border px-3 py-1.5 text-xs text-muted">
              <Checkbox
                checked={allSelected}
                onChange={() => setSelected(allSelected ? new Set() : new Set(items.map((m) => m.id)))}
                label={`Select all ${items.length} on this page`}
              />
            </div>
          ) : null}
          <ul aria-busy={list.isFetching} className={cn(list.isFetching && !list.isLoading && "opacity-80")}>
            {items.map((m) => (
              <MatchRow
                key={m.id}
                m={m}
                minScore={a.min_score}
                write={write}
                selected={selected.has(m.id)}
                onToggle={() => toggle(m.id)}
                open={expanded === m.id}
                onExpand={() => setExpanded(expanded === m.id ? null : m.id)}
                onOpenItem={onOpenItem}
                onReview={(s) => review.mutate({ id: m.id, review_status: s, prev: m.review_status })}
                onNote={(notes) => review.mutate({ id: m.id, notes })}
                busy={review.isPending && review.variables?.id === m.id}
              />
            ))}
          </ul>
        </>
      )}
      {list.data && list.data.total > PAGE_SIZE ? (
        <Pagination page={page} pageSize={PAGE_SIZE} total={list.data.total} onPage={(p) => update({ m_page: p === 1 ? null : String(p) })} />
      ) : null}
    </div>
  );
}

function MatchRow({
  m,
  minScore,
  write,
  selected,
  onToggle,
  open,
  onExpand,
  onOpenItem,
  onReview,
  onNote,
  busy,
}: {
  m: MatchOut;
  minScore: number;
  write: boolean;
  selected: boolean;
  onToggle: () => void;
  open: boolean;
  onExpand: () => void;
  onOpenItem: (id: string) => void;
  onReview: (s: Review) => void;
  onNote: (notes: string) => void;
  busy: boolean;
}) {
  const n = m.news_item;
  const [note, setNote] = useState(m.notes ?? "");
  useEffect(() => setNote(m.notes ?? ""), [m.notes]);
  return (
    <li className={cn("border-b border-border px-3 py-2 last:border-0", selected && "bg-accent/5")}>
      <div className="flex items-start gap-2">
        {write ? (
          <span className="pt-0.5">
            <Checkbox checked={selected} onChange={onToggle} aria-label={`Select ${n.title}`} />
          </span>
        ) : null}
        <ScorePill score={m.score} minScore={minScore} />
        <div className="min-w-0 flex-1">
          <button type="button" onClick={() => onOpenItem(n.id)} className="line-clamp-2 text-left text-sm font-medium hover:text-accent">
            {n.title}
          </button>
          <div className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs text-muted">
            <span>{n.publisher ?? n.source_name ?? "Unknown publisher"}</span>
            <Time value={n.published_at ?? n.sort_at} />
            {n.language ? <span className="uppercase">{n.language}</span> : null}
            {n.countries?.length ? <span>{n.countries.slice(0, 4).map((c) => `${flag(c)} ${c}`).join(" ")}</span> : null}
            {n.is_duplicate ? (
              <Badge tone="neutral" icon={Copy}>
                Duplicate
              </Badge>
            ) : null}
            <a href={n.url} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-0.5 hover:text-text">
              Original <ExternalLink className="h-3 w-3" aria-hidden />
            </a>
          </div>
          <button type="button" onClick={onExpand} aria-expanded={open} className="mt-1 flex items-start gap-1 text-left text-xs text-muted hover:text-text">
            {open ? <ChevronDown className="mt-0.5 h-3 w-3 shrink-0" /> : <ChevronRight className="mt-0.5 h-3 w-3 shrink-0" />}
            <span className={open ? "" : "line-clamp-1"}>{m.explanation}</span>
          </button>
          {open ? (
            <div className="mt-2 grid gap-3 rounded border border-border bg-surface-2/40 p-2 md:grid-cols-2">
              <div>
                <p className="mb-1 text-2xs font-medium uppercase tracking-wide text-muted">Why it matched</p>
                <ScoreBreakdown lines={asBreakdown(m.breakdown)} score={m.score} minScore={minScore} />
                <p className="mt-1 text-2xs text-muted">
                  Matched <Time value={m.matched_at} />
                  {m.reviewed_at ? (
                    <>
                      {" "}
                      · reviewed <Time value={m.reviewed_at} />
                    </>
                  ) : null}
                </p>
              </div>
              <div className="flex flex-col gap-1.5">
                <label htmlFor={`note-${m.id}`} className="text-2xs font-medium uppercase tracking-wide text-muted">
                  Reviewer note
                </label>
                <Textarea id={`note-${m.id}`} rows={3} value={note} onChange={(e) => setNote(e.target.value)} disabled={!write} placeholder="Why it is or isn’t relevant, who to call…" />
                {write && note !== (m.notes ?? "") ? (
                  <div className="flex justify-end gap-2">
                    <Button size="sm" variant="ghost" onClick={() => setNote(m.notes ?? "")}>
                      Discard
                    </Button>
                    <Button size="sm" variant="primary" onClick={() => onNote(note)} loading={busy}>
                      Save note
                    </Button>
                  </div>
                ) : null}
              </div>
            </div>
          ) : null}
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <StatusBadge status={m.review_status} />
          {write ? (
            <>
              {m.review_status !== "verified" ? (
                <Tip content="Verify — relevant to this story">
                  <Button size="icon-sm" variant="ghost" onClick={() => onReview("verified")} disabled={busy} aria-label={`Verify: ${n.title}`}>
                    <Check className="h-4 w-4 text-success" />
                  </Button>
                </Tip>
              ) : null}
              {m.review_status !== "rejected" ? (
                <Tip content="Reject — not relevant">
                  <Button size="icon-sm" variant="ghost" onClick={() => onReview("rejected")} disabled={busy} aria-label={`Reject: ${n.title}`}>
                    <X className="h-4 w-4 text-danger" />
                  </Button>
                </Tip>
              ) : null}
              {m.review_status !== "archived" ? (
                <Tip content="Archive — hide from the open list">
                  <Button size="icon-sm" variant="ghost" onClick={() => onReview("archived")} disabled={busy} aria-label={`Archive: ${n.title}`}>
                    <Archive className="h-4 w-4" />
                  </Button>
                </Tip>
              ) : (
                <Tip content="Back to unreviewed">
                  <Button size="icon-sm" variant="ghost" onClick={() => onReview("unreviewed")} disabled={busy} aria-label={`Mark unreviewed: ${n.title}`}>
                    <RotateCcw className="h-4 w-4" />
                  </Button>
                </Tip>
              )}
            </>
          ) : null}
        </div>
      </div>
    </li>
  );
}
