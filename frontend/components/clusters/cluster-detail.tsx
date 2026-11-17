"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Archive,
  ArchiveRestore,
  GitMerge,
  Globe2,
  Layers,
  Lock,
  MapPin,
  Pencil,
  Plus,
  RotateCcw,
  Scissors,
  Search,
  Target,
  Unlock,
  Users,
  X,
} from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import { NewsRow } from "@/components/news-row";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { useConfirm } from "@/components/ui/confirm";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { Field, Input, Textarea } from "@/components/ui/input";
import { EmptyState, ErrorState, InlineAlert, Skeleton, SkeletonRows, Spinner } from "@/components/ui/states";
import { StatusBadge } from "@/components/ui/status-badge";
import { Time } from "@/components/ui/time";
import { Tip } from "@/components/ui/tooltip";
import { api, errorMessage } from "@/lib/api";
import { PERMS, useSession } from "@/lib/session";
import type { ClusterOut, NewsItemOut, Page } from "@/lib/types";
import { cn, flag, formatNumber } from "@/lib/utils";

export type ClusterItem = NewsItemOut & { cluster_method: string | null; cluster_similarity: number | null; added_at: string | null };
export type ClusterDetailData = ClusterOut & {
  items: ClusterItem[];
  assignments: { id: string; headline: string; matches: number; top_score: number | null }[];
  geography: { country_code: string | null; city: string | null; role: string; items: number }[];
};

const METHOD_LABELS: Record<string, string> = {
  seed: "First report",
  duplicate: "Duplicate of a report",
  headline: "Similar headline",
  keywords: "Shared keywords",
  editor: "Placed by an editor",
};

function methodLabel(m: string | null): string {
  if (!m) return "Grouped";
  return METHOD_LABELS[m] ?? m.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
}

function useDebounced<T>(value: T, ms = 300): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

function invalidateClusters(qc: ReturnType<typeof useQueryClient>, ...ids: string[]) {
  qc.invalidateQueries({ queryKey: ["clusters"] });
  for (const id of ids) qc.invalidateQueries({ queryKey: ["cluster", id] });
  qc.invalidateQueries({ queryKey: ["news"] });
}

function RenameDialog({ cluster, open, onOpenChange }: { cluster: ClusterDetailData; open: boolean; onOpenChange: (o: boolean) => void }) {
  const qc = useQueryClient();
  const [title, setTitle] = useState(cluster.title);
  useEffect(() => {
    if (open) setTitle(cluster.title);
  }, [open, cluster.title]);
  const save = useMutation({
    mutationFn: () => api<ClusterOut>(`/clusters/${cluster.id}`, { method: "PATCH", body: { title: title.trim() } }),
    onSuccess: () => {
      invalidateClusters(qc, cluster.id);
      toast.success("Cluster renamed");
      onOpenChange(false);
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      {open ? (
        <DialogContent
          title="Rename cluster"
          description="An editor title is kept even as new reports join. Reset it to follow the representative headline again."
          footer={
            <>
              <Button variant="outline" onClick={() => onOpenChange(false)}>
                Cancel
              </Button>
              <Button variant="primary" onClick={() => save.mutate()} loading={save.isPending} disabled={!title.trim()}>
                Save title
              </Button>
            </>
          }
        >
          <form
            onSubmit={(e) => {
              e.preventDefault();
              if (title.trim()) save.mutate();
            }}
          >
            <Field label="Title" htmlFor="cl-title">
              <Input id="cl-title" value={title} onChange={(e) => setTitle(e.target.value)} maxLength={500} autoFocus />
            </Field>
          </form>
        </DialogContent>
      ) : null}
    </Dialog>
  );
}

function MergeDialog({ cluster, open, onOpenChange }: { cluster: ClusterDetailData; open: boolean; onOpenChange: (o: boolean) => void }) {
  const qc = useQueryClient();
  const [q, setQ] = useState("");
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const term = useDebounced(q.trim());
  useEffect(() => {
    if (open) {
      setQ("");
      setPicked(new Set());
    }
  }, [open]);
  const others = useQuery({
    queryKey: ["clusters", "merge-candidates", term],
    queryFn: () => api<Page<ClusterOut>>("/clusters", { query: { q: term, page_size: 30, sort: "latest" } }),
    enabled: open,
  });
  const merge = useMutation({
    mutationFn: () => api<ClusterOut>(`/clusters/${cluster.id}/merge`, { body: { cluster_ids: Array.from(picked) } }),
    onSuccess: () => {
      invalidateClusters(qc, cluster.id, ...Array.from(picked));
      toast.success(`Merged ${picked.size} cluster${picked.size === 1 ? "" : "s"} into this one`);
      onOpenChange(false);
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const candidates = (others.data?.items ?? []).filter((c) => c.id !== cluster.id);
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      {open ? (
        <DialogContent
          wide
          title="Merge clusters into this one"
          description={`Reports from the selected clusters move into “${cluster.title}”. The other clusters are removed and their reports keep a record that an editor placed them.`}
          footer={
            <>
              <Button variant="outline" onClick={() => onOpenChange(false)}>
                Cancel
              </Button>
              <Button variant="primary" onClick={() => merge.mutate()} loading={merge.isPending} disabled={!picked.size}>
                <GitMerge className="h-4 w-4" /> Merge {picked.size || ""}
              </Button>
            </>
          }
        >
          <div className="relative mb-3">
            <Search className="pointer-events-none absolute left-2 top-2 h-4 w-4 text-muted" aria-hidden />
            <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Find clusters by title or report headline" className="pl-8" aria-label="Find clusters" autoFocus />
          </div>
          {others.isLoading ? (
            <SkeletonRows rows={4} className="p-0" />
          ) : !candidates.length ? (
            <p className="py-6 text-center text-sm text-muted">No other clusters found.</p>
          ) : (
            <ul className="divide-y divide-border/70 rounded border border-border">
              {candidates.map((c) => (
                <li key={c.id}>
                  <label className="flex cursor-pointer items-start gap-2 px-3 py-2 hover:bg-surface-2/60">
                    <input
                      type="checkbox"
                      className="mt-1 h-3.5 w-3.5"
                      checked={picked.has(c.id)}
                      onChange={() =>
                        setPicked((prev) => {
                          const n = new Set(prev);
                          if (n.has(c.id)) n.delete(c.id);
                          else n.add(c.id);
                          return n;
                        })
                      }
                    />
                    <span className="min-w-0 flex-1">
                      <span className="block text-sm">{c.title}</span>
                      <span className="block text-xs text-muted">
                        {c.item_count} reports · {c.publisher_count} publishers · last <Time value={c.last_seen_at} />
                        {c.is_locked ? " · locked" : ""}
                      </span>
                    </span>
                  </label>
                </li>
              ))}
            </ul>
          )}
        </DialogContent>
      ) : null}
    </Dialog>
  );
}

function AddReportDialog({ cluster, open, onOpenChange }: { cluster: ClusterDetailData; open: boolean; onOpenChange: (o: boolean) => void }) {
  const qc = useQueryClient();
  const [q, setQ] = useState("");
  const term = useDebounced(q.trim());
  useEffect(() => {
    if (open) setQ("");
  }, [open]);
  const results = useQuery({
    queryKey: ["news", "cluster-add", term],
    queryFn: () => api<Page<NewsItemOut>>("/news", { query: { q: term, page_size: 15, duplicates: "exclude" } }),
    enabled: open && term.length >= 2,
  });
  const inCluster = new Set(cluster.items.map((i) => i.id));
  const add = useMutation({
    mutationFn: (item: NewsItemOut) => api<ClusterOut>(`/clusters/${cluster.id}/items`, { body: { news_item_id: item.id } }),
    onSuccess: (_r, item) => {
      invalidateClusters(qc, cluster.id, ...(item.cluster_id ? [item.cluster_id] : []));
      toast.success("Report added to the cluster");
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      {open ? (
        <DialogContent wide title="Add a report" description="Search collected news. A report in another cluster is moved here.">
          <div className="relative mb-3">
            <Search className="pointer-events-none absolute left-2 top-2 h-4 w-4 text-muted" aria-hidden />
            <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search headlines and summaries" className="pl-8" aria-label="Search news" autoFocus />
          </div>
          {term.length < 2 ? (
            <p className="py-6 text-center text-sm text-muted">Type at least two characters.</p>
          ) : results.isLoading ? (
            <Spinner />
          ) : !results.data?.items.length ? (
            <p className="py-6 text-center text-sm text-muted">No stories match “{term}”.</p>
          ) : (
            <div className="rounded border border-border">
              {results.data.items.map((n) => (
                <NewsRow
                  key={n.id}
                  item={n}
                  extra={
                    inCluster.has(n.id) ? (
                      <Badge tone="success">In this cluster</Badge>
                    ) : (
                      <Button size="sm" variant="outline" className="h-6" onClick={() => add.mutate(n)} loading={add.isPending && add.variables?.id === n.id}>
                        <Plus className="h-3 w-3" /> Add
                      </Button>
                    )
                  }
                />
              ))}
            </div>
          )}
        </DialogContent>
      ) : null}
    </Dialog>
  );
}

export function ClusterDetail({ id, onGone }: { id: string; onGone?: () => void }) {
  const qc = useQueryClient();
  const confirm = useConfirm();
  const { can } = useSession();
  const canWrite = can(PERMS.clustersWrite);
  const [renaming, setRenaming] = useState(false);
  const [merging, setMerging] = useState(false);
  const [adding, setAdding] = useState(false);
  const [splitMode, setSplitMode] = useState(false);
  const [splitIds, setSplitIds] = useState<Set<string>>(new Set());
  const [splitTitle, setSplitTitle] = useState("");
  const [notes, setNotes] = useState<string | null>(null);

  const detail = useQuery({
    queryKey: ["cluster", id],
    queryFn: () => api<ClusterDetailData>(`/clusters/${id}`),
    refetchInterval: 60_000,
  });

  useEffect(() => {
    setSplitMode(false);
    setSplitIds(new Set());
    setSplitTitle("");
    setNotes(null);
  }, [id]);

  const patch = useMutation({
    mutationFn: (body: Record<string, unknown>) => api<ClusterOut>(`/clusters/${id}`, { method: "PATCH", body }),
    onSuccess: (_r, body) => {
      invalidateClusters(qc, id);
      if ("notes" in body) {
        setNotes(null);
        toast.success("Notes saved");
      } else if ("is_locked" in body) toast.success(body.is_locked ? "Cluster locked: automatic clustering will not add reports" : "Cluster unlocked");
      else if ("status" in body) toast.success(body.status === "archived" ? "Cluster archived" : "Cluster restored");
      else if ("reset_title" in body) toast.success("Title follows the representative headline again");
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  const split = useMutation({
    mutationFn: () =>
      api<{ original: ClusterOut; new: ClusterOut }>(`/clusters/${id}/split`, {
        body: { item_ids: Array.from(splitIds), title: splitTitle.trim() || null },
      }),
    onSuccess: (r) => {
      invalidateClusters(qc, id, r.new.id);
      toast.success(`Split ${splitIds.size} report${splitIds.size === 1 ? "" : "s"} into “${r.new.title}”`);
      setSplitMode(false);
      setSplitIds(new Set());
      setSplitTitle("");
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  const removeItem = useMutation({
    mutationFn: (itemId: string) => api<ClusterOut>(`/clusters/${id}/items/${itemId}`, { method: "DELETE" }),
    onSuccess: (r) => {
      invalidateClusters(qc, id);
      toast.success("Report removed from the cluster");
      if (r.item_count === 0) onGone?.();
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  if (detail.isLoading) {
    return (
      <div className="space-y-3 p-4">
        <Skeleton className="h-6 w-3/4" />
        <SkeletonRows rows={6} className="p-0" />
      </div>
    );
  }
  if (detail.isError || !detail.data) return <ErrorState error={detail.error} onRetry={() => detail.refetch()} />;
  const c = detail.data;
  const items = [...c.items].sort((a, b) => new Date(a.sort_at).getTime() - new Date(b.sort_at).getTime());
  const publishers = Array.from(
    items.reduce((m, i) => m.set(i.publisher ?? "Unknown", (m.get(i.publisher ?? "Unknown") ?? 0) + 1), new Map<string, number>()),
  ).sort((a, b) => b[1] - a[1]);
  const countries = Array.from(
    c.geography
      .filter((g) => g.country_code)
      .reduce((m, g) => m.set(g.country_code!, Math.max(m.get(g.country_code!) ?? 0, g.items)), new Map<string, number>()),
  ).sort((a, b) => b[1] - a[1]);
  const cities = c.geography.filter((g) => g.city).sort((a, b) => b.items - a.items);

  const remove = async (item: ClusterItem) => {
    const ok = await confirm({
      title: "Remove this report from the cluster?",
      body: (
        <>
          “{item.title}” will stand on its own. {c.is_locked ? "" : "Automatic clustering may group it again unless you lock this cluster."}
        </>
      ),
      confirmLabel: "Remove report",
      destructive: true,
    });
    if (ok) removeItem.mutate(item.id);
  };

  const toggleSplit = (itemId: string) =>
    setSplitIds((prev) => {
      const n = new Set(prev);
      if (n.has(itemId)) n.delete(itemId);
      else n.add(itemId);
      return n;
    });

  return (
    <div className="flex flex-col gap-4">
      <div>
        <div className="flex flex-wrap items-center gap-2">
          <StatusBadge status={c.status} />
          {c.is_locked ? (
            <Badge tone="warning" icon={Lock}>
              Locked
            </Badge>
          ) : null}
          <Badge tone={c.title_source === "editor" ? "accent" : "neutral"} icon={c.title_source === "editor" ? Pencil : undefined}>
            {c.title_source === "editor" ? "Editor title" : "Representative headline"}
          </Badge>
        </div>
        <h2 className="mt-2 text-base font-semibold leading-snug">{c.title}</h2>
        <p className="mt-1 text-xs text-muted">
          {formatNumber(c.item_count)} reports from {formatNumber(c.publisher_count)} publishers · first seen <Time value={c.first_seen_at} /> · latest{" "}
          <Time value={c.last_seen_at} />
        </p>
        {canWrite ? (
          <div className="mt-3 flex flex-wrap gap-2">
            <Button size="sm" variant="outline" onClick={() => setRenaming(true)}>
              <Pencil className="h-3.5 w-3.5" /> Rename
            </Button>
            {c.title_source === "editor" ? (
              <Button size="sm" variant="outline" onClick={() => patch.mutate({ reset_title: true })} loading={patch.isPending && "reset_title" in (patch.variables ?? {})}>
                <RotateCcw className="h-3.5 w-3.5" /> Reset title
              </Button>
            ) : null}
            <Tip content={c.is_locked ? "Allow automatic clustering to add reports again" : "Stop automatic clustering from adding or moving reports"}>
              <Button size="sm" variant="outline" onClick={() => patch.mutate({ is_locked: !c.is_locked })}>
                {c.is_locked ? <Unlock className="h-3.5 w-3.5" /> : <Lock className="h-3.5 w-3.5" />}
                {c.is_locked ? "Unlock" : "Lock"}
              </Button>
            </Tip>
            <Button size="sm" variant="outline" onClick={() => setMerging(true)}>
              <GitMerge className="h-3.5 w-3.5" /> Merge in…
            </Button>
            <Button
              size="sm"
              variant={splitMode ? "primary" : "outline"}
              onClick={() => {
                setSplitMode((m) => !m);
                setSplitIds(new Set());
              }}
              disabled={c.item_count < 2}
              aria-pressed={splitMode}
            >
              <Scissors className="h-3.5 w-3.5" /> {splitMode ? "Cancel split" : "Split…"}
            </Button>
            <Button size="sm" variant="outline" onClick={() => setAdding(true)}>
              <Plus className="h-3.5 w-3.5" /> Add report
            </Button>
            <Button
              size="sm"
              variant="outline"
              onClick={async () => {
                if (c.status === "archived") return patch.mutate({ status: "active" });
                const ok = await confirm({
                  title: "Archive this cluster?",
                  body: "It leaves the active list. Reports stay in it and you can restore it from the archived filter.",
                  confirmLabel: "Archive",
                });
                if (ok) patch.mutate({ status: "archived" });
              }}
            >
              {c.status === "archived" ? <ArchiveRestore className="h-3.5 w-3.5" /> : <Archive className="h-3.5 w-3.5" />}
              {c.status === "archived" ? "Restore" : "Archive"}
            </Button>
          </div>
        ) : null}
      </div>

      {splitMode ? (
        <InlineAlert tone="info" title="Select the reports to move into a new cluster">
          <div className="mt-2 flex flex-wrap items-end gap-2">
            <Field label="New cluster title (optional)" htmlFor="split-title" className="min-w-[220px] flex-1">
              <Input id="split-title" value={splitTitle} onChange={(e) => setSplitTitle(e.target.value)} placeholder="Defaults to the earliest selected headline" />
            </Field>
            <Button
              variant="primary"
              onClick={() => split.mutate()}
              loading={split.isPending}
              disabled={!splitIds.size || splitIds.size >= c.item_count}
            >
              <Scissors className="h-4 w-4" /> Split {splitIds.size || ""} report{splitIds.size === 1 ? "" : "s"}
            </Button>
          </div>
          {splitIds.size >= c.item_count ? <p className="mt-1 text-xs text-danger">Leave at least one report in this cluster.</p> : null}
        </InlineAlert>
      ) : null}

      <Card>
        <CardHeader title="Reports in time order" description="How each report joined the cluster" icon={Layers} />
        {items.length ? (
          <ol>
            {items.map((item, idx) => (
              <li key={item.id} className={cn("flex items-stretch", splitMode && splitIds.has(item.id) && "bg-accent/5")}>
                {splitMode ? (
                  <label className="flex items-start px-3 pt-3">
                    <input
                      type="checkbox"
                      className="h-3.5 w-3.5"
                      checked={splitIds.has(item.id)}
                      onChange={() => toggleSplit(item.id)}
                      aria-label={`Select “${item.title}” for the new cluster`}
                    />
                  </label>
                ) : (
                  <span className="tabular hidden w-8 shrink-0 pt-3 text-center text-2xs text-muted sm:block" aria-hidden>
                    {idx + 1}
                  </span>
                )}
                <div className="min-w-0 flex-1">
                  <NewsRow
                    item={item}
                    onOpen={() => window.open(item.url, "_blank", "noopener,noreferrer")}
                    extra={
                      <>
                        <Tip content={item.added_at ? `Joined ${new Date(item.added_at).toLocaleString()}` : undefined}>
                          <span className="cursor-default">
                            <Badge tone={item.cluster_method === "editor" ? "accent" : "neutral"}>
                              {methodLabel(item.cluster_method)}
                              {item.cluster_similarity !== null && item.cluster_similarity !== undefined && item.cluster_method !== "seed" && item.cluster_method !== "duplicate"
                                ? ` · ${Math.round(item.cluster_similarity)}%`
                                : ""}
                            </Badge>
                          </span>
                        </Tip>
                        {canWrite && !splitMode && c.item_count > 1 ? (
                          <button
                            type="button"
                            onClick={() => remove(item)}
                            className="inline-flex items-center gap-1 rounded px-1 text-2xs text-muted hover:bg-danger/10 hover:text-danger"
                            aria-label={`Remove “${item.title}” from the cluster`}
                          >
                            <X className="h-3 w-3" /> Remove
                          </button>
                        ) : null}
                      </>
                    }
                  />
                </div>
              </li>
            ))}
          </ol>
        ) : (
          <EmptyState icon={Layers} title="This cluster is empty" description="Its reports were merged into another cluster or removed." />
        )}
      </Card>

      <div className="grid gap-4 md:grid-cols-2">
        <Card>
          <CardHeader title="Publishers" description="Reports per publisher" icon={Users} />
          <CardBody className="py-2">
            <ul className="flex flex-col gap-1">
              {publishers.map(([p, n]) => (
                <li key={p} className="flex items-center justify-between gap-2 text-sm">
                  <span className="truncate">{p}</span>
                  <span className="tabular text-xs text-muted">{n}</span>
                </li>
              ))}
            </ul>
          </CardBody>
        </Card>
        <Card>
          <CardHeader title="Geography" description="Confident places (publisher locations excluded)" icon={Globe2} />
          <CardBody className="py-2">
            {!countries.length && !cities.length ? (
              <p className="text-sm text-muted">No places detected with enough confidence.</p>
            ) : (
              <div className="flex flex-col gap-2">
                <div className="flex flex-wrap gap-1.5">
                  {countries.map(([cc, n]) => (
                    <Badge key={cc} tone="neutral" title={`${n} report${n === 1 ? "" : "s"}`}>
                      {flag(cc)} {cc} · {n}
                    </Badge>
                  ))}
                </div>
                {cities.length ? (
                  <ul className="flex flex-col gap-0.5 text-sm">
                    {cities.slice(0, 8).map((g, i) => (
                      <li key={`${g.city}-${g.role}-${i}`} className="flex items-center gap-1.5">
                        <MapPin className="h-3 w-3 text-muted" aria-hidden />
                        <span>{g.city}</span>
                        {g.country_code ? <span className="text-xs text-muted">{g.country_code}</span> : null}
                        <span className="text-2xs text-muted">({g.role === "event" ? "dateline" : g.role})</span>
                        <span className="tabular ml-auto text-xs text-muted">{g.items}</span>
                      </li>
                    ))}
                  </ul>
                ) : null}
              </div>
            )}
          </CardBody>
        </Card>
      </div>

      <Card>
        <CardHeader title="Linked headlines" description="Editorial assignments that matched reports in this cluster" icon={Target} />
        {c.assignments.length ? (
          <ul>
            {c.assignments.map((a) => (
              <li key={a.id} className="border-b border-border/70 last:border-0">
                <Link href={`/research/${a.id}`} className="flex items-center gap-3 px-4 py-2 hover:bg-surface-2/60">
                  <span className="min-w-0 flex-1 truncate text-sm">{a.headline}</span>
                  <span className="text-xs text-muted">
                    {a.matches} match{a.matches === 1 ? "" : "es"}
                    {a.top_score !== null ? ` · top score ${Math.round(a.top_score)}` : ""}
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        ) : (
          <p className="px-4 py-3 text-sm text-muted">No headline has matched these reports.</p>
        )}
      </Card>

      <Card>
        <CardHeader title="Editor notes" />
        <CardBody>
          <Textarea
            aria-label="Cluster notes"
            rows={3}
            value={notes ?? c.notes ?? ""}
            onChange={(e) => setNotes(e.target.value)}
            disabled={!canWrite}
            placeholder={canWrite ? "Context for colleagues: what this story is, what is confirmed…" : "No notes"}
          />
          {canWrite && notes !== null && notes !== (c.notes ?? "") ? (
            <div className="mt-2 flex justify-end gap-2">
              <Button size="sm" variant="ghost" onClick={() => setNotes(null)}>
                Discard
              </Button>
              <Button size="sm" variant="primary" onClick={() => patch.mutate({ notes })} loading={patch.isPending}>
                Save notes
              </Button>
            </div>
          ) : null}
        </CardBody>
      </Card>

      <RenameDialog cluster={c} open={renaming} onOpenChange={setRenaming} />
      <MergeDialog cluster={c} open={merging} onOpenChange={setMerging} />
      <AddReportDialog cluster={c} open={adding} onOpenChange={setAdding} />
    </div>
  );
}
