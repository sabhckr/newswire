"use client";

import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Archive, MapPin, Pause, Play, Plus, Search, Target, Trash2, X } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import { AssignmentRowMenu } from "@/components/assignments/assignment-actions";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { useConfirm } from "@/components/ui/confirm";
import { Checkbox, Input, Select } from "@/components/ui/input";
import { Kbd, PageHeader, Pagination } from "@/components/ui/page";
import { EmptyState, ErrorState, SkeletonRows } from "@/components/ui/states";
import { PriorityBadge, StatusBadge } from "@/components/ui/status-badge";
import { Table, TD, TH, THead, TR } from "@/components/ui/table";
import { Time } from "@/components/ui/time";
import { api, errorMessage } from "@/lib/api";
import { PERMS, useSession } from "@/lib/session";
import type { AssignmentOut, Page } from "@/lib/types";
import { cn, formatNumber } from "@/lib/utils";

const TABS = [
  { value: "active", label: "Active" },
  { value: "paused", label: "Paused" },
  { value: "archived", label: "Archived" },
] as const;
const PAGE_SIZE = 50;

function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

function isTyping(target: EventTarget | null) {
  const el = target as HTMLElement | null;
  return !!el && (el.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName));
}

export default function HeadlinesPage() {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const qc = useQueryClient();
  const confirm = useConfirm();
  const { can } = useSession();
  const write = can(PERMS.assignmentsWrite);

  const tab = (params.get("status") ?? "active") as (typeof TABS)[number]["value"];
  const priority = params.get("priority") ?? "";
  const sort = params.get("sort") ?? "priority";
  const page = Math.max(1, Number(params.get("page") ?? 1) || 1);
  const [qInput, setQInput] = useState(params.get("q") ?? "");
  const q = useDebounced(qInput.trim(), 300);
  const [selected, setSelected] = useState<Set<string>>(new Set());

  const update = useCallback(
    (patch: Record<string, string | null>) => {
      const next = new URLSearchParams(params.toString());
      for (const [k, v] of Object.entries(patch)) {
        if (!v) next.delete(k);
        else next.set(k, v);
      }
      if (!("page" in patch)) next.delete("page");
      const s = next.toString();
      router.replace(s ? `${pathname}?${s}` : pathname, { scroll: false });
    },
    [params, pathname, router],
  );

  useEffect(() => {
    if ((params.get("q") ?? "") !== q) update({ q: q || null });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q]);
  useEffect(() => setSelected(new Set()), [tab, priority, q, page]);

  // "n" opens a new headline (when not typing in a field).
  useEffect(() => {
    if (!write) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "n" && !e.metaKey && !e.ctrlKey && !e.altKey && !isTyping(e.target)) {
        e.preventDefault();
        router.push("/headlines/new");
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [router, write]);

  const query = useMemo(
    () => ({ status: [tab], q: q || undefined, priority: priority || undefined, sort, page, page_size: PAGE_SIZE }),
    [tab, q, priority, sort, page],
  );
  const list = useQuery({
    queryKey: ["assignments", "list", query],
    queryFn: ({ signal }) => api<Page<AssignmentOut>>("/assignments", { query, signal }),
    placeholderData: keepPreviousData,
  });

  const bulk = useMutation({
    mutationFn: (action: "pause" | "resume" | "archive" | "delete") =>
      api<{ updated: number }>("/assignments/bulk", { body: { ids: [...selected], action } }),
    onSuccess: (r, action) => {
      toast.success(`${r.updated} headline${r.updated === 1 ? "" : "s"} ${action === "delete" ? "deleted" : action === "resume" ? "resumed" : `${action}d`}`);
      setSelected(new Set());
      qc.invalidateQueries({ queryKey: ["assignments"] });
      qc.invalidateQueries({ queryKey: ["overview"] });
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  const runBulk = async (action: "pause" | "resume" | "archive" | "delete") => {
    if (action === "delete") {
      const ok = await confirm({
        title: `Delete ${selected.size} headline${selected.size === 1 ? "" : "s"}?`,
        body: "Their stored matches are deleted too. Collected news reports are kept. This cannot be undone.",
        confirmLabel: "Delete",
        destructive: true,
      });
      if (!ok) return;
    }
    bulk.mutate(action);
  };

  const items = list.data?.items ?? [];
  const allSelected = items.length > 0 && items.every((a) => selected.has(a.id));
  const toggleAll = () => setSelected(allSelected ? new Set() : new Set(items.map((a) => a.id)));
  const toggle = (id: string) =>
    setSelected((s) => {
      const n = new Set(s);
      if (n.has(id)) n.delete(id);
      else n.add(id);
      return n;
    });
  const filtered = !!(q || priority);

  return (
    <div className="mx-auto max-w-7xl">
      <PageHeader
        title="My Headlines"
        description="Editorial assignments: the stories you are monitoring, with keyword and location rules that decide which reports match."
        actions={
          write ? (
            <Button variant="primary" onClick={() => router.push("/headlines/new")}>
              <Plus className="h-4 w-4" /> New headline <Kbd>N</Kbd>
            </Button>
          ) : null
        }
      />

      <Card>
        <div className="flex flex-wrap items-center gap-2 border-b border-border px-3 pt-2">
          <div role="tablist" aria-label="Status" className="-mb-px flex">
            {TABS.map((t) => (
              <button
                key={t.value}
                role="tab"
                type="button"
                aria-selected={tab === t.value}
                onClick={() => update({ status: t.value === "active" ? null : t.value })}
                className={cn(
                  "border-b-2 px-3 py-2 text-sm",
                  tab === t.value ? "border-accent font-medium text-text" : "border-transparent text-muted hover:text-text",
                )}
              >
                {t.label}
                {tab === t.value && list.data ? <span className="tabular ml-1.5 text-xs text-muted">{formatNumber(list.data.total)}</span> : null}
              </button>
            ))}
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2 border-b border-border px-3 py-2">
          <div className="relative min-w-[200px] flex-1">
            <Search className="pointer-events-none absolute left-2.5 top-2 h-4 w-4 text-muted" aria-hidden />
            <label htmlFor="hl-q" className="sr-only">
              Search headlines and keywords
            </label>
            <Input id="hl-q" value={qInput} onChange={(e) => setQInput(e.target.value)} placeholder="Search headlines and keywords…" className="pl-8" />
          </div>
          <label htmlFor="hl-priority" className="sr-only">
            Priority
          </label>
          <Select id="hl-priority" value={priority} onChange={(e) => update({ priority: e.target.value || null })} className="w-auto">
            <option value="">Any priority</option>
            <option value="urgent">Urgent</option>
            <option value="high">High</option>
            <option value="normal">Normal</option>
            <option value="low">Low</option>
          </Select>
          <label htmlFor="hl-sort" className="sr-only">
            Sort
          </label>
          <Select id="hl-sort" value={sort} onChange={(e) => update({ sort: e.target.value === "priority" ? null : e.target.value })} className="w-auto">
            <option value="priority">Priority</option>
            <option value="last_matched">Latest match</option>
            <option value="matches">Most matches</option>
            <option value="updated">Recently edited</option>
            <option value="created">Newest</option>
            <option value="headline">Headline A–Z</option>
          </Select>
        </div>

        {selected.size > 0 && write ? (
          <div className="flex flex-wrap items-center gap-2 border-b border-border bg-accent/5 px-3 py-2" role="region" aria-label="Bulk actions">
            <span className="text-sm font-medium">{selected.size} selected</span>
            {tab !== "active" ? (
              <Button size="sm" variant="outline" onClick={() => runBulk("resume")} disabled={bulk.isPending}>
                <Play className="h-3.5 w-3.5" /> Resume
              </Button>
            ) : (
              <Button size="sm" variant="outline" onClick={() => runBulk("pause")} disabled={bulk.isPending}>
                <Pause className="h-3.5 w-3.5" /> Pause
              </Button>
            )}
            {tab !== "archived" ? (
              <Button size="sm" variant="outline" onClick={() => runBulk("archive")} disabled={bulk.isPending}>
                <Archive className="h-3.5 w-3.5" /> Archive
              </Button>
            ) : null}
            <Button size="sm" variant="danger-outline" onClick={() => runBulk("delete")} disabled={bulk.isPending}>
              <Trash2 className="h-3.5 w-3.5" /> Delete
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setSelected(new Set())}>
              <X className="h-3.5 w-3.5" /> Clear
            </Button>
          </div>
        ) : null}

        {list.isLoading ? (
          <SkeletonRows rows={8} />
        ) : list.isError ? (
          <ErrorState error={list.error} onRetry={() => list.refetch()} />
        ) : !items.length ? (
          filtered ? (
            <EmptyState icon={Search} title="No headlines match" description="Try a different search or priority." />
          ) : tab === "active" ? (
            <EmptyState
              icon={Target}
              title="No active headlines"
              description="A headline is a story you want to follow. Give it keywords and places, and NEWSWIRE scores every collected report against it."
              action={
                write ? (
                  <Button variant="primary" onClick={() => router.push("/headlines/new")}>
                    <Plus className="h-4 w-4" /> Create your first headline
                  </Button>
                ) : null
              }
            />
          ) : (
            <EmptyState icon={tab === "paused" ? Pause : Archive} title={`No ${tab} headlines`} />
          )
        ) : (
          <Table>
            <THead>
              <tr>
                {write ? (
                  <TH className="w-8">
                    <Checkbox checked={allSelected} onChange={toggleAll} aria-label="Select all on this page" />
                  </TH>
                ) : null}
                <TH>Headline</TH>
                <TH className="hidden md:table-cell">Priority</TH>
                <TH className="text-right">Matches</TH>
                <TH className="hidden lg:table-cell">Last match</TH>
                <TH className="hidden xl:table-cell">Research</TH>
                <TH className="w-10">
                  <span className="sr-only">Actions</span>
                </TH>
              </tr>
            </THead>
            <tbody>
              {items.map((a) => {
                const primary = a.keywords.filter((k) => k.kind === "primary" || k.kind === "required").map((k) => k.term);
                return (
                  <TR key={a.id} className={cn(selected.has(a.id) && "bg-accent/5")}>
                    {write ? (
                      <TD>
                        <Checkbox checked={selected.has(a.id)} onChange={() => toggle(a.id)} aria-label={`Select ${a.headline}`} />
                      </TD>
                    ) : null}
                    <TD className="max-w-0 py-2">
                      <Link href={`/research/${a.id}`} className="block truncate font-medium hover:text-accent" title={a.headline}>
                        {a.headline}
                      </Link>
                      <div className="mt-0.5 flex items-center gap-2 truncate text-xs text-muted">
                        {primary.length ? <span className="truncate">{primary.slice(0, 5).join(" · ")}</span> : null}
                        {a.geographies.length ? (
                          <span className="inline-flex shrink-0 items-center gap-0.5">
                            <MapPin className="h-3 w-3" aria-hidden />
                            {a.geographies.slice(0, 2).map((g) => g.name).join(", ")}
                            {a.geographies.length > 2 ? ` +${a.geographies.length - 2}` : ""}
                          </span>
                        ) : null}
                        {tab !== "active" ? <StatusBadge status={a.status} /> : null}
                        <span className="md:hidden">
                          <PriorityBadge priority={a.priority} />
                        </span>
                      </div>
                    </TD>
                    <TD className="hidden md:table-cell">
                      <PriorityBadge priority={a.priority} />
                    </TD>
                    <TD className="tabular whitespace-nowrap text-right">
                      {formatNumber(a.match_count)}
                      {a.unreviewed_count ? (
                        <Link
                          href={`/research/${a.id}`}
                          className="ml-1.5 rounded bg-info/10 px-1.5 text-xs font-medium text-info"
                          title={`${a.unreviewed_count} awaiting review`}
                        >
                          {a.unreviewed_count} new
                        </Link>
                      ) : null}
                    </TD>
                    <TD className="hidden whitespace-nowrap text-xs text-muted lg:table-cell">
                      <Time value={a.last_matched_at} />
                    </TD>
                    <TD className="hidden xl:table-cell">
                      <StatusBadge status={a.research_status} />
                    </TD>
                    <TD>
                      <AssignmentRowMenu a={a} />
                    </TD>
                  </TR>
                );
              })}
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
