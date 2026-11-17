"use client";

import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Download,
  FlaskConical,
  MoreHorizontal,
  Pause,
  Pencil,
  Play,
  Plus,
  RefreshCw,
  Rss,
  Search,
  Trash2,
  Upload,
} from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";

import { useSourceActions } from "@/components/sources/actions";
import { ProbeResultPanel, type ProbeResult } from "@/components/sources/probe-result";
import { SourceDialog } from "@/components/sources/source-dialog";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { useConfirm } from "@/components/ui/confirm";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { Dropdown, DropdownContent, DropdownItem, DropdownSeparator, DropdownTrigger } from "@/components/ui/dropdown";
import { Input, Select } from "@/components/ui/input";
import { PageHeader, Pagination, Toolbar } from "@/components/ui/page";
import { EmptyState, ErrorState, InlineAlert, SkeletonRows } from "@/components/ui/states";
import { StatusBadge } from "@/components/ui/status-badge";
import { Table, TD, TH, THead, TR } from "@/components/ui/table";
import { Time } from "@/components/ui/time";
import { Tip } from "@/components/ui/tooltip";
import { api, downloadUrl, errorMessage } from "@/lib/api";
import { PERMS, useSession } from "@/lib/session";
import type { CategoryOut, Page, SourceOut } from "@/lib/types";
import { cn, flag, formatInterval, formatNumber, hostOf } from "@/lib/utils";

const PAGE_SIZE = 50;
const STATUS_OPTIONS = [
  { value: "", label: "All sources" },
  { value: "active", label: "Active" },
  { value: "healthy", label: "Healthy" },
  { value: "failing", label: "Failing" },
  { value: "pending", label: "Never fetched" },
  { value: "disabled", label: "Disabled" },
];

function useDebounced<T>(value: T, ms = 300): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

/** Converts an OPML subscription list to the import endpoint's JSON entry format. */
function opmlToEntries(text: string): { name: string; publisher: string; feed_url: string; website_url: string | null }[] {
  const doc = new DOMParser().parseFromString(text, "text/xml");
  if (doc.querySelector("parsererror")) throw new Error("The file is not valid OPML/XML");
  return Array.from(doc.querySelectorAll("outline"))
    .map((o) => {
      const url = o.getAttribute("xmlUrl") ?? o.getAttribute("xmlurl");
      if (!url) return null;
      const title = o.getAttribute("title") ?? o.getAttribute("text") ?? url;
      return { name: title, publisher: title, feed_url: url, website_url: o.getAttribute("htmlUrl") ?? o.getAttribute("htmlurl") };
    })
    .filter((x): x is NonNullable<typeof x> => x !== null);
}

export default function SourcesPage() {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const qc = useQueryClient();
  const confirm = useConfirm();
  const { can } = useSession();
  const canWrite = can(PERMS.sourcesWrite);
  const { refresh, setActive, remove, bulk } = useSourceActions();

  const [q, setQ] = useState("");
  const [status, setStatus] = useState(params.get("health") === "failing" ? "failing" : params.get("status") ?? "");
  const [type, setType] = useState("");
  const [country, setCountry] = useState("");
  const [category, setCategory] = useState("");
  const [sort, setSort] = useState("name");
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [editing, setEditing] = useState<SourceOut | null>(null);
  const [formOpen, setFormOpen] = useState(false);
  const [probe, setProbe] = useState<{ source: SourceOut; result: ProbeResult } | null>(null);
  const [importResult, setImportResult] = useState<{ created: number; skipped: number; errors: { index: number; feed_url?: string; error: string }[] } | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const term = useDebounced(q.trim());
  const cc = useDebounced(country.trim());

  // Deep links: ?new=1 opens the form, ?id=<uuid> opens the source's health page.
  useEffect(() => {
    const id = params.get("id");
    if (id) {
      router.replace(`/sources/${id}`);
      return;
    }
    if (params.get("new") === "1" && canWrite) {
      setEditing(null);
      setFormOpen(true);
    }
  }, [params, router, canWrite]);

  useEffect(() => setPage(1), [term, status, type, cc, category, sort]);

  const categories = useQuery({ queryKey: ["categories"], queryFn: () => api<CategoryOut[]>("/categories") });
  const list = useQuery({
    queryKey: ["sources", { term, status, type, cc, category, sort, page }],
    queryFn: () =>
      api<Page<SourceOut>>("/sources", {
        query: {
          q: term,
          status,
          source_type: type,
          country: cc.length === 2 ? cc : undefined,
          category_id: category,
          sort,
          page,
          page_size: PAGE_SIZE,
        },
      }),
    placeholderData: keepPreviousData,
    refetchInterval: 30_000,
  });

  const items = useMemo(() => list.data?.items ?? [], [list.data]);
  const allSelected = items.length > 0 && items.every((s) => selected.has(s.id));
  const toggleAll = () => setSelected(allSelected ? new Set() : new Set(items.map((s) => s.id)));
  const toggle = (id: string) =>
    setSelected((prev) => {
      const n = new Set(prev);
      if (n.has(id)) n.delete(id);
      else n.add(id);
      return n;
    });

  const testSource = useMutation({
    mutationFn: (s: SourceOut) => api<ProbeResult>(`/sources/${s.id}/test`, { method: "POST" }).then((result) => ({ source: s, result })),
    onSuccess: setProbe,
    onError: (e) => toast.error(errorMessage(e)),
  });

  const importFile = useMutation({
    mutationFn: async (file: File) => {
      const text = await file.text();
      const trimmed = text.trimStart();
      const body = trimmed.startsWith("{") || trimmed.startsWith("[") ? JSON.parse(trimmed) : opmlToEntries(text);
      return api<{ created: number; skipped: number; errors: { index: number; feed_url?: string; error: string }[] }>("/sources/import", { body });
    },
    onSuccess: (r) => {
      setImportResult(r);
      qc.invalidateQueries({ queryKey: ["sources"] });
    },
    onError: (e) => toast.error(e instanceof SyntaxError ? `Invalid JSON: ${e.message}` : errorMessage(e)),
  });

  const closeForm = (o: boolean) => {
    setFormOpen(o);
    if (!o && params.get("new")) {
      const next = new URLSearchParams(params.toString());
      next.delete("new");
      router.replace(next.toString() ? `${pathname}?${next}` : pathname);
    }
  };

  const doBulk = async (action: "enable" | "disable" | "delete" | "refresh_soon") => {
    const ids = Array.from(selected);
    if (action === "delete") {
      const ok = await confirm({
        title: `Delete ${ids.length} source${ids.length === 1 ? "" : "s"}?`,
        body: "They stop being collected and disappear from this list. Stories already collected from them are kept.",
        confirmLabel: "Delete",
        destructive: true,
      });
      if (!ok) return;
    }
    bulk.mutate({ ids, action }, { onSuccess: () => setSelected(new Set()) });
  };

  const deleteOne = async (s: SourceOut) => {
    const ok = await confirm({
      title: `Delete ${s.name}?`,
      body: "Collection stops and the source is removed from the list. Stories already collected are kept and stay searchable.",
      confirmLabel: "Delete source",
      destructive: true,
    });
    if (ok) remove.mutate(s.id);
  };

  const filtersActive = !!(term || status || type || cc || category);

  return (
    <div className="mx-auto max-w-7xl">
      <PageHeader
        title="Sources"
        description="Feeds NEWSWIRE collects from. Fetching respects robots.txt, conditional requests and per-host delays."
        actions={
          <>
            <Button variant="outline" onClick={() => window.open(downloadUrl("/sources/export"), "_blank")}>
              <Download className="h-4 w-4" /> Export
            </Button>
            {canWrite ? (
              <>
                <input
                  ref={fileRef}
                  type="file"
                  accept=".json,.opml,.xml,application/json,text/xml,text/x-opml"
                  className="sr-only"
                  aria-label="Import sources file"
                  onChange={(e) => {
                    const f = e.target.files?.[0];
                    if (f) importFile.mutate(f);
                    e.target.value = "";
                  }}
                />
                <Tip content="Import a NEWSWIRE JSON export or an OPML subscription list">
                  <Button variant="outline" onClick={() => fileRef.current?.click()} loading={importFile.isPending}>
                    <Upload className="h-4 w-4" /> Import
                  </Button>
                </Tip>
                <Button
                  variant="primary"
                  onClick={() => {
                    setEditing(null);
                    setFormOpen(true);
                  }}
                >
                  <Plus className="h-4 w-4" /> Add source
                </Button>
              </>
            ) : null}
          </>
        }
      />

      {params.get("welcome") === "1" ? (
        <InlineAlert tone="info" className="mb-4" title="Welcome to NEWSWIRE">
          Start by adding the feeds you want to monitor. The collection worker (<code className="font-mono">python -m app.worker</code>) fetches every active source
          on its own schedule, so new stories arrive without anyone keeping this page open. Then create a headline to match stories by keyword and place.
        </InlineAlert>
      ) : null}

      {importResult ? (
        <InlineAlert
          tone={importResult.errors.length ? "warning" : "success"}
          className="mb-4"
          title={`Imported ${importResult.created} source${importResult.created === 1 ? "" : "s"}; ${importResult.skipped} already existed`}
        >
          {importResult.errors.length ? (
            <ul className="mt-1 list-disc pl-5 text-xs">
              {importResult.errors.slice(0, 8).map((e) => (
                <li key={e.index}>
                  {e.feed_url ?? `Entry ${e.index + 1}`}: {e.error}
                </li>
              ))}
              {importResult.errors.length > 8 ? <li>…and {importResult.errors.length - 8} more</li> : null}
            </ul>
          ) : null}
          <button type="button" className="mt-1 text-xs underline" onClick={() => setImportResult(null)}>
            Dismiss
          </button>
        </InlineAlert>
      ) : null}

      <Card>
        <Toolbar>
          <div className="relative min-w-[180px] flex-1 sm:max-w-xs">
            <Search className="pointer-events-none absolute left-2 top-2 h-4 w-4 text-muted" aria-hidden />
            <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Name, publisher or URL" aria-label="Search sources" className="pl-8" />
          </div>
          <Select value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Health" className="w-auto">
            {STATUS_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </Select>
          <Select value={type} onChange={(e) => setType(e.target.value)} aria-label="Format" className="w-auto">
            <option value="">Any format</option>
            <option value="rss">RSS</option>
            <option value="atom">Atom</option>
            <option value="json">JSON</option>
          </Select>
          <Input
            value={country}
            onChange={(e) => setCountry(e.target.value.toUpperCase())}
            maxLength={2}
            placeholder="Country"
            aria-label="Publisher country (two-letter code)"
            className="w-24 uppercase"
          />
          <Select value={category} onChange={(e) => setCategory(e.target.value)} aria-label="Category" className="w-auto">
            <option value="">Any category</option>
            {categories.data?.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </Select>
          <Select value={sort} onChange={(e) => setSort(e.target.value)} aria-label="Sort by" className="w-auto sm:ml-auto">
            <option value="name">Sort: name</option>
            <option value="publisher">Sort: publisher</option>
            <option value="last_checked">Sort: last fetched</option>
            <option value="errors">Sort: most failures</option>
            <option value="priority">Sort: priority</option>
            <option value="created">Sort: newest</option>
          </Select>
        </Toolbar>

        {selected.size > 0 && canWrite ? (
          <div className="flex flex-wrap items-center gap-2 border-b border-border bg-accent/5 px-3 py-2 text-sm" role="region" aria-label="Bulk actions">
            <span className="font-medium">{selected.size} selected</span>
            <Button size="sm" variant="outline" onClick={() => doBulk("enable")} loading={bulk.isPending}>
              <Play className="h-3.5 w-3.5" /> Enable
            </Button>
            <Button size="sm" variant="outline" onClick={() => doBulk("disable")} loading={bulk.isPending}>
              <Pause className="h-3.5 w-3.5" /> Disable
            </Button>
            <Button size="sm" variant="outline" onClick={() => doBulk("refresh_soon")} loading={bulk.isPending}>
              <RefreshCw className="h-3.5 w-3.5" /> Fetch next cycle
            </Button>
            <Button size="sm" variant="danger-outline" onClick={() => doBulk("delete")} loading={bulk.isPending}>
              <Trash2 className="h-3.5 w-3.5" /> Delete
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setSelected(new Set())} className="ml-auto">
              Clear
            </Button>
          </div>
        ) : null}

        {list.isLoading ? (
          <SkeletonRows rows={8} />
        ) : list.isError ? (
          <ErrorState error={list.error} onRetry={() => list.refetch()} />
        ) : !items.length ? (
          filtersActive ? (
            <EmptyState icon={Search} title="No sources match these filters" description="Clear the search or choose a different health filter." />
          ) : (
            <EmptyState
              icon={Rss}
              title="No sources yet"
              description="Add the RSS, Atom or JSON feeds you want to monitor, or import an OPML list from another reader."
              action={
                canWrite ? (
                  <Button variant="primary" onClick={() => setFormOpen(true)}>
                    <Plus className="h-4 w-4" /> Add source
                  </Button>
                ) : null
              }
            />
          )
        ) : (
          <>
            <Table>
              <THead>
                <tr>
                  {canWrite ? (
                    <TH className="w-8">
                      <input type="checkbox" checked={allSelected} onChange={toggleAll} aria-label="Select all sources on this page" className="h-3.5 w-3.5" />
                    </TH>
                  ) : null}
                  <TH>Source</TH>
                  <TH>Health</TH>
                  <TH className="hidden md:table-cell">Last fetch</TH>
                  <TH className="hidden lg:table-cell">Next fetch</TH>
                  <TH className="hidden md:table-cell">Interval</TH>
                  <TH className="hidden sm:table-cell text-right">Items</TH>
                  <TH className="w-10">
                    <span className="sr-only">Actions</span>
                  </TH>
                </tr>
              </THead>
              <tbody>
                {items.map((s) => (
                  <TR key={s.id} className={cn(selected.has(s.id) && "bg-accent/5")}>
                    {canWrite ? (
                      <TD>
                        <input
                          type="checkbox"
                          checked={selected.has(s.id)}
                          onChange={() => toggle(s.id)}
                          aria-label={`Select ${s.name}`}
                          className="h-3.5 w-3.5"
                        />
                      </TD>
                    ) : null}
                    <TD className="max-w-[340px] py-1.5">
                      <Link href={`/sources/${s.id}`} className="block truncate font-medium hover:text-accent">
                        {s.name}
                      </Link>
                      <div className="flex min-w-0 items-center gap-1.5 text-xs text-muted">
                        <span className="truncate">{s.publisher}</span>
                        <span aria-hidden>·</span>
                        <span className="truncate" title={s.feed_url}>
                          {hostOf(s.feed_url)}
                        </span>
                        <span className="shrink-0 uppercase">{s.source_type}</span>
                        {s.country_code ? (
                          <span className="shrink-0" title={`Publisher country ${s.country_code}`}>
                            {flag(s.country_code)} {s.country_code}
                          </span>
                        ) : null}
                        {s.language ? <span className="shrink-0">{s.language}</span> : null}
                      </div>
                    </TD>
                    <TD>
                      <div className="flex flex-col items-start gap-0.5">
                        <StatusBadge status={s.health} />
                        {s.consecutive_failures > 0 ? (
                          <Tip content={s.last_error ?? "Recent fetches failed"}>
                            <span className="cursor-help text-2xs text-danger">
                              {s.consecutive_failures} failure{s.consecutive_failures === 1 ? "" : "s"}
                              {s.last_http_status ? ` · HTTP ${s.last_http_status}` : ""}
                            </span>
                          </Tip>
                        ) : null}
                      </div>
                    </TD>
                    <TD className="hidden whitespace-nowrap text-xs text-muted md:table-cell">
                      <Time value={s.last_fetched_at} />
                    </TD>
                    <TD className="hidden whitespace-nowrap text-xs text-muted lg:table-cell">
                      {s.is_active ? <Time value={s.next_fetch_at} /> : "—"}
                    </TD>
                    <TD className="hidden whitespace-nowrap text-xs text-muted md:table-cell">{formatInterval(s.polling_interval_seconds)}</TD>
                    <TD className="tabular hidden text-right sm:table-cell">{formatNumber(s.total_items)}</TD>
                    <TD>
                      <Dropdown>
                        <DropdownTrigger asChild>
                          <Button variant="ghost" size="icon-sm" aria-label={`Actions for ${s.name}`}>
                            <MoreHorizontal className="h-4 w-4" />
                          </Button>
                        </DropdownTrigger>
                        <DropdownContent>
                          <DropdownItem icon={Rss} onSelect={() => router.push(`/sources/${s.id}`)}>
                            Health and fetch log
                          </DropdownItem>
                          {canWrite ? (
                            <>
                              <DropdownItem icon={RefreshCw} onSelect={() => refresh.mutate(s.id)} disabled={refresh.isPending}>
                                Fetch now
                              </DropdownItem>
                              <DropdownItem icon={FlaskConical} onSelect={() => testSource.mutate(s)}>
                                Test without storing
                              </DropdownItem>
                              <DropdownItem
                                icon={Pencil}
                                onSelect={() => {
                                  setEditing(s);
                                  setFormOpen(true);
                                }}
                              >
                                Edit
                              </DropdownItem>
                              <DropdownItem icon={s.is_active ? Pause : Play} onSelect={() => setActive.mutate({ id: s.id, active: !s.is_active })}>
                                {s.is_active ? "Disable" : "Enable"}
                              </DropdownItem>
                              <DropdownSeparator />
                              <DropdownItem icon={Trash2} danger onSelect={() => deleteOne(s)}>
                                Delete
                              </DropdownItem>
                            </>
                          ) : null}
                        </DropdownContent>
                      </Dropdown>
                    </TD>
                  </TR>
                ))}
              </tbody>
            </Table>
            <Pagination page={page} pageSize={PAGE_SIZE} total={list.data?.total ?? 0} onPage={setPage} />
          </>
        )}
      </Card>

      {refresh.isPending ? (
        <p className="mt-2 text-xs text-muted" role="status">
          Fetching… a slow feed can take up to the fetch timeout.
        </p>
      ) : null}

      <SourceDialog open={formOpen} onOpenChange={closeForm} source={editing} />

      <Dialog open={!!probe} onOpenChange={(o) => !o && setProbe(null)}>
        {probe ? (
          <DialogContent wide title={`Test: ${probe.source.name}`} description="A one-off fetch. Nothing was stored.">
            <ProbeResultPanel result={probe.result} />
          </DialogContent>
        ) : null}
      </Dialog>
    </div>
  );
}
