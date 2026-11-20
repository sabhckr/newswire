"use client";

import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Eye, FileText, MoreHorizontal, Pencil, Plus, Search, Trash2 } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { useConfirm } from "@/components/ui/confirm";
import { Dropdown, DropdownContent, DropdownItem, DropdownTrigger } from "@/components/ui/dropdown";
import { Input, Select } from "@/components/ui/input";
import { Kbd, PageHeader, Pagination, Toolbar } from "@/components/ui/page";
import { EmptyState, ErrorState, SkeletonRows } from "@/components/ui/states";
import { StatusBadge } from "@/components/ui/status-badge";
import { Table, TD, TH, THead, TR } from "@/components/ui/table";
import { Time } from "@/components/ui/time";
import { api, errorMessage } from "@/lib/api";
import { PERMS, useSession } from "@/lib/session";
import type { ArticleListOut, ArticleOut, CountedPage } from "@/lib/types";
import { cn, formatNumber } from "@/lib/utils";

const PAGE_SIZE = 50;

const TABS: { key: string; label: string; statuses: string[] }[] = [
  { key: "all", label: "All", statuses: [] },
  { key: "progress", label: "In progress", statuses: ["draft", "in_review"] },
  { key: "draft", label: "Draft", statuses: ["draft"] },
  { key: "in_review", label: "In review", statuses: ["in_review"] },
  { key: "approved", label: "Approved", statuses: ["approved"] },
  { key: "scheduled", label: "Scheduled", statuses: ["scheduled"] },
  { key: "published", label: "Published", statuses: ["published"] },
  { key: "unpublished", label: "Unpublished", statuses: ["unpublished"] },
  { key: "archived", label: "Archived", statuses: ["archived"] },
];

const SORTS = [
  { value: "updated", label: "Recently updated" },
  { value: "created", label: "Recently created" },
  { value: "published", label: "Recently published" },
  { value: "headline", label: "Headline A–Z" },
];

function sameSet(a: string[], b: string[]) {
  return a.length === b.length && a.every((x) => b.includes(x));
}

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

export default function ArticlesPage() {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const qc = useQueryClient();
  const confirm = useConfirm();
  const { can } = useSession();
  const write = can(PERMS.articlesWrite);

  const statuses = params.getAll("status");
  const activeTab = TABS.find((t) => sameSet(t.statuses, statuses))?.key ?? null;
  const sort = params.get("sort") ?? "updated";
  const page = Math.max(1, Number(params.get("page") ?? 1) || 1);
  const [qInput, setQInput] = useState(params.get("q") ?? "");
  const q = useDebounced(qInput.trim(), 300);

  const navigate = useCallback(
    (patch: { status?: string[]; q?: string | null; sort?: string | null; page?: number | null }) => {
      const next = new URLSearchParams(params.toString());
      if (patch.status) {
        next.delete("status");
        patch.status.forEach((s) => next.append("status", s));
      }
      for (const k of ["q", "sort", "page"] as const) {
        if (!(k in patch)) continue;
        const v = patch[k];
        if (v === null || v === undefined || v === "" || (k === "sort" && v === "updated") || (k === "page" && v === 1)) next.delete(k);
        else next.set(k, String(v));
      }
      if (!("page" in patch)) next.delete("page");
      const s = next.toString();
      router.replace(s ? `${pathname}?${s}` : pathname, { scroll: false });
    },
    [params, pathname, router],
  );

  useEffect(() => {
    if ((params.get("q") ?? "") !== q) navigate({ q: q || null });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q]);

  const query = useMemo(
    () => ({ status: statuses, q: q || undefined, sort, page, page_size: PAGE_SIZE }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [statuses.join(","), q, sort, page],
  );
  const list = useQuery({
    queryKey: ["articles", "list", query],
    queryFn: ({ signal }) => api<CountedPage<ArticleListOut>>("/articles", { query, signal }),
    placeholderData: keepPreviousData,
  });

  const create = useMutation({
    mutationFn: () => api<ArticleOut>("/articles", { body: { headline: "Untitled article", body_html: "" } }),
    onSuccess: (a) => {
      qc.invalidateQueries({ queryKey: ["articles"] });
      router.push(`/articles/${a.id}`);
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  const del = useMutation({
    mutationFn: (id: string) => api(`/articles/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      toast.success("Article deleted");
      qc.invalidateQueries({ queryKey: ["articles"] });
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  // "n" starts a new article (when not typing in a field).
  useEffect(() => {
    if (!write) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "n" && !e.metaKey && !e.ctrlKey && !e.altKey && !isTyping(e.target) && !create.isPending) {
        e.preventDefault();
        create.mutate();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [write, create]);

  const counts = list.data?.status_counts ?? {};
  const countFor = (t: (typeof TABS)[number]) =>
    t.statuses.length
      ? t.statuses.reduce((n, s) => n + (counts[s] ?? 0), 0)
      : Object.entries(counts).reduce((n, [s, c]) => (s === "archived" ? n : n + c), 0);
  const items = list.data?.items ?? [];
  const filtered = !!q;
  const dateCol = sort === "published" ? "Published" : sort === "created" ? "Created" : "Updated";

  return (
    <div className="mx-auto max-w-7xl">
      <PageHeader
        title="Articles"
        description="Write articles and publish them to your website and social pages."
        actions={
          write ? (
            <Button variant="primary" onClick={() => create.mutate()} loading={create.isPending}>
              <Plus className="h-4 w-4" /> New article <Kbd>N</Kbd>
            </Button>
          ) : null
        }
      />

      <Card>
        <nav aria-label="Filter by status" className="flex gap-1 overflow-x-auto border-b border-border px-2 pt-2">
          {TABS.map((t) => {
            const active = activeTab === t.key;
            return (
              <button
                key={t.key}
                type="button"
                aria-current={active ? "page" : undefined}
                onClick={() => navigate({ status: t.statuses })}
                className={cn(
                  "-mb-px inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap border-b-2 px-2.5 pb-2 pt-1 text-sm",
                  active ? "border-accent font-medium text-text" : "border-transparent text-muted hover:text-text",
                )}
              >
                {t.label}
                <span className="tabular rounded bg-surface-2 px-1.5 text-2xs text-muted">{list.data ? formatNumber(countFor(t)) : "–"}</span>
              </button>
            );
          })}
        </nav>
        <Toolbar>
          <div className="relative min-w-[200px] flex-1">
            <Search className="pointer-events-none absolute left-2 top-2 h-4 w-4 text-muted" aria-hidden />
            <Input
              value={qInput}
              onChange={(e) => setQInput(e.target.value)}
              placeholder="Search headline, subtitle or slug…"
              className="pl-8"
              aria-label="Search articles"
            />
          </div>
          <label className="flex items-center gap-2 text-xs text-muted">
            <span className="hidden sm:inline">Sort</span>
            <Select value={sort} onChange={(e) => navigate({ sort: e.target.value })} className="w-auto" aria-label="Sort articles">
              {SORTS.map((s) => (
                <option key={s.value} value={s.value}>
                  {s.label}
                </option>
              ))}
            </Select>
          </label>
          {activeTab === null && statuses.length ? (
            <span className="text-xs text-muted">
              Showing: {statuses.map((s) => s.replace("_", " ")).join(", ")}
            </span>
          ) : null}
        </Toolbar>

        {list.isLoading ? (
          <SkeletonRows rows={8} />
        ) : list.isError ? (
          <ErrorState error={list.error} onRetry={() => list.refetch()} />
        ) : !items.length ? (
          filtered ? (
            <EmptyState icon={Search} title="No articles match" description="Try a different search or status." />
          ) : activeTab === "all" || activeTab === "draft" || activeTab === "progress" ? (
            <EmptyState
              icon={FileText}
              title="No articles yet"
              description="Open a story in Live News and press “Start article from this”, or begin with a blank article."
              action={
                write ? (
                  <Button variant="primary" onClick={() => create.mutate()} loading={create.isPending}>
                    <Plus className="h-4 w-4" /> New article
                  </Button>
                ) : null
              }
            />
          ) : (
            <EmptyState icon={FileText} title={`No ${TABS.find((t) => t.key === activeTab)?.label.toLowerCase() ?? "matching"} articles`} />
          )
        ) : (
          <>
            <Table className={cn(list.isPlaceholderData && "opacity-60")}>
              <THead>
                <tr>
                  <TH className="w-14">
                    <span className="sr-only">Image</span>
                  </TH>
                  <TH>Headline</TH>
                  <TH className="hidden md:table-cell">Author</TH>
                  <TH className="hidden text-right lg:table-cell">Words</TH>
                  <TH>Status</TH>
                  <TH className="hidden sm:table-cell">{dateCol}</TH>
                  <TH className="w-10">
                    <span className="sr-only">Actions</span>
                  </TH>
                </tr>
              </THead>
              <tbody>
                {items.map((a) => {
                  const date = sort === "published" ? a.published_at : sort === "created" ? a.created_at : a.updated_at;
                  const locked = a.status === "published" || a.status === "scheduled";
                  return (
                    <TR key={a.id}>
                      <TD className="py-1.5">
                        {a.featured_thumbnail ? (
                          // eslint-disable-next-line @next/next/no-img-element
                          <img src={a.featured_thumbnail} alt="" loading="lazy" className="h-9 w-12 rounded border border-border object-cover" />
                        ) : (
                          <span className="flex h-9 w-12 items-center justify-center rounded border border-dashed border-border text-muted">
                            <FileText className="h-4 w-4" aria-hidden />
                          </span>
                        )}
                      </TD>
                      <TD className="max-w-0 py-2">
                        <Link href={`/articles/${a.id}`} className="block truncate font-medium hover:text-accent" title={a.headline}>
                          {a.headline}
                        </Link>
                        <div className="mt-0.5 flex items-center gap-2 truncate text-xs text-muted">
                          {a.category ? <span className="shrink-0">{a.category.name}</span> : null}
                          {a.subtitle ? <span className="truncate">{a.subtitle}</span> : null}
                          {a.last_published_version && a.version > a.last_published_version && a.status === "published" ? (
                            <span className="shrink-0 text-info">unpublished edits</span>
                          ) : null}
                          <span className="shrink-0 sm:hidden">
                            <Time value={date} />
                          </span>
                        </div>
                      </TD>
                      <TD className="hidden whitespace-nowrap text-xs text-muted md:table-cell">{a.author_name ?? "—"}</TD>
                      <TD className="tabular hidden whitespace-nowrap text-right text-xs lg:table-cell">{formatNumber(a.word_count)}</TD>
                      <TD className="whitespace-nowrap">
                        <StatusBadge status={a.status} />
                        {a.status === "scheduled" && a.scheduled_at ? (
                          <span className="ml-1 hidden text-2xs text-muted xl:inline">
                            <Time value={a.scheduled_at} absolute />
                          </span>
                        ) : null}
                      </TD>
                      <TD className="hidden whitespace-nowrap text-xs text-muted sm:table-cell">
                        {date ? <Time value={date} /> : "—"}
                      </TD>
                      <TD>
                        <Dropdown>
                          <DropdownTrigger asChild>
                            <Button size="icon-sm" variant="ghost" aria-label={`Actions for ${a.headline}`}>
                              <MoreHorizontal className="h-4 w-4" />
                            </Button>
                          </DropdownTrigger>
                          <DropdownContent>
                            <DropdownItem icon={Pencil} onSelect={() => router.push(`/articles/${a.id}`)}>
                              {write ? "Edit" : "Open"}
                            </DropdownItem>
                            <DropdownItem icon={Eye} onSelect={() => window.open(`/articles/${a.id}/preview`, "_blank", "noopener")}>
                              Preview
                            </DropdownItem>
                            {write ? (
                              <DropdownItem
                                icon={Trash2}
                                danger
                                disabled={locked}
                                onSelect={async () => {
                                  const ok = await confirm({
                                    title: "Delete this article?",
                                    body: `“${a.headline}” is removed from every list. Its history stays in the activity log.`,
                                    confirmLabel: "Delete",
                                    destructive: true,
                                  });
                                  if (ok) del.mutate(a.id);
                                }}
                              >
                                {locked ? "Delete (unpublish first)" : "Delete"}
                              </DropdownItem>
                            ) : null}
                          </DropdownContent>
                        </Dropdown>
                      </TD>
                    </TR>
                  );
                })}
              </tbody>
            </Table>
            <Pagination page={page} pageSize={PAGE_SIZE} total={list.data?.total ?? 0} onPage={(p) => navigate({ page: p })} />
          </>
        )}
      </Card>
    </div>
  );
}
