"use client";

import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertOctagon, AlertTriangle, BookmarkPlus, Check, ChevronLeft, ChevronRight, ExternalLink, ImageIcon, Info, Search, X } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import type { Workspace, WorkspaceImage } from "@/components/research/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Checkbox, Field, Input, Select, Textarea } from "@/components/ui/input";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { EmptyState, ErrorState, InlineAlert, Skeleton } from "@/components/ui/states";
import { StatusBadge } from "@/components/ui/status-badge";
import { api, errorMessage } from "@/lib/api";
import { PERMS, useSession } from "@/lib/session";
import type { ImageOut, ImageSearchResponse, ImageSearchResult, LicenseWarning } from "@/lib/types";
import { cn, formatNumber } from "@/lib/utils";

const LEVEL = {
  error: { icon: AlertOctagon, cls: "text-danger", label: "Blocks use" },
  warning: { icon: AlertTriangle, cls: "text-warning", label: "Warning" },
  info: { icon: Info, cls: "text-info", label: "Note" },
} as const;

function Warnings({ items, compact }: { items: LicenseWarning[] | undefined; compact?: boolean }) {
  if (!items?.length) return null;
  const sorted = [...items].sort((a, b) => ["error", "warning", "info"].indexOf(a.level) - ["error", "warning", "info"].indexOf(b.level));
  const shown = compact ? sorted.filter((w) => w.level !== "info").slice(0, 2) : sorted;
  return (
    <ul className="flex flex-col gap-0.5">
      {shown.map((w) => {
        const L = LEVEL[w.level] ?? LEVEL.info;
        return (
          <li key={w.code} className={cn("flex items-start gap-1 text-2xs", L.cls)}>
            <L.icon className="mt-px h-3 w-3 shrink-0" aria-hidden />
            <span>
              <span className="sr-only">{L.label}: </span>
              {w.message}
            </span>
          </li>
        );
      })}
    </ul>
  );
}

function Thumb({ src, alt }: { src: string | null | undefined; alt: string }) {
  const [broken, setBroken] = useState(false);
  return (
    <div className="flex aspect-[4/3] items-center justify-center overflow-hidden rounded-t-lg bg-surface-2">
      {src && !broken ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img src={src} alt={alt} loading="lazy" referrerPolicy="no-referrer" className="h-full w-full object-cover" onError={() => setBroken(true)} />
      ) : (
        <ImageIcon className="h-6 w-6 text-muted" aria-label="No preview" />
      )}
    </div>
  );
}

function candidateOf(r: ImageSearchResult) {
  return {
    provider: r.provider,
    provider_id: r.provider_id,
    title: r.title,
    creator: r.creator,
    creator_url: r.creator_url,
    source_page_url: r.source_page_url,
    original_url: r.original_url,
    thumbnail_url: r.thumbnail_url,
    width: r.width,
    height: r.height,
    mime: r.mime,
    license_code: r.license_code,
    license_version: r.license_version,
    license_raw: r.license_raw,
    license_url: r.license_url,
    attribution_text: r.attribution_text,
    repository: r.repository,
    metadata: r.metadata ?? {},
  };
}

export function ImagesTab({ ws }: { ws: Workspace }) {
  const a = ws.assignment;
  const qc = useQueryClient();
  const { can } = useSession();
  const canSearch = can(PERMS.imagesWrite);
  const canApprove = can(PERMS.imagesApprove);

  const suggested = a.keywords
    .filter((k) => k.kind === "primary" || k.kind === "required")
    .slice(0, 2)
    .map((k) => k.term)
    .join(" ");
  const [draft, setDraft] = useState(suggested);
  const [term, setTerm] = useState("");
  const [provider, setProvider] = useState<"openverse" | "wikimedia">("openverse");
  const [commercialOnly, setCommercialOnly] = useState(true);
  const [modAllowed, setModAllowed] = useState(false);
  const [page, setPage] = useState(1);

  const search = useQuery({
    queryKey: ["image-search", term, provider, page, commercialOnly, modAllowed],
    queryFn: ({ signal }) =>
      api<ImageSearchResponse>("/images/search", {
        query: {
          q: term,
          provider,
          page,
          commercial_only: provider === "openverse" ? commercialOnly : undefined,
          modification_allowed: provider === "openverse" ? modAllowed : undefined,
        },
        signal,
      }),
    enabled: canSearch && term.length >= 2,
    placeholderData: keepPreviousData,
    staleTime: 300_000,
    retry: false,
  });

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["research", a.id] });
    qc.invalidateQueries({ queryKey: ["image-search"] });
    qc.invalidateQueries({ queryKey: ["images"] });
  };

  const save = useMutation({
    mutationFn: (r: ImageSearchResult) =>
      api<ImageOut>("/images", { method: "POST", body: { candidate: candidateOf(r), assignment_id: a.id, query: term } }),
    onSuccess: (img) => {
      toast.success("Image saved for review", { description: img.title ?? undefined });
      refresh();
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const approve = useMutation({
    mutationFn: (img: WorkspaceImage) => api<ImageOut>(`/images/${img.id}/approve`, { method: "POST" }),
    onSuccess: () => {
      toast.success("Image approved and stored locally");
      refresh();
    },
    onError: (e) => {
      toast.error(errorMessage(e));
      refresh();
    },
  });
  const reject = useMutation({
    mutationFn: ({ img, note }: { img: WorkspaceImage; note: string }) => api<ImageOut>(`/images/${img.id}/reject`, { method: "POST", body: { note: note || null } }),
    onSuccess: () => {
      toast.success("Image rejected");
      refresh();
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    const t = draft.trim();
    if (t.length < 2) return;
    setPage(1);
    setTerm(t);
  };
  const pageCount = search.data?.page_count ?? 1;

  return (
    <div className="flex flex-col gap-4 p-3">
      <Card>
        <CardHeader
          icon={ImageIcon}
          title={`Saved for this story (${ws.images.length})`}
          description="Saved images need an approver before they can be used. Approval downloads a local copy; the original is never hotlinked in published articles."
        />
        {!ws.images.length ? (
          <EmptyState
            icon={ImageIcon}
            title="No images saved yet"
            description={canSearch ? "Search open repositories below and save candidates with clear licences." : "An editor with image permissions can search and save candidates."}
          />
        ) : (
          <ul className="grid grid-cols-1 gap-3 p-3 sm:grid-cols-2 lg:grid-cols-4">
            {ws.images.map((img) => (
              <li key={img.id} className="flex flex-col rounded-lg border border-border">
                <Thumb src={img.local_thumbnail_url ?? img.thumbnail_url} alt={img.title ?? "Saved image"} />
                <div className="flex flex-1 flex-col gap-1.5 p-2">
                  <div className="flex items-start justify-between gap-2">
                    <p className="line-clamp-2 text-xs font-medium">{img.title || "Untitled"}</p>
                    <StatusBadge status={img.approval_status} />
                  </div>
                  <p className="truncate text-2xs text-muted">
                    {img.creator ? `By ${img.creator} · ` : ""}
                    {img.license?.name ?? img.license_raw ?? "Unknown licence"}
                  </p>
                  {img.download_status === "failed" && img.download_error ? (
                    <p className="text-2xs text-danger">Download failed: {img.download_error}</p>
                  ) : null}
                  <Warnings items={img.warnings} compact />
                  <div className="mt-auto flex flex-wrap items-center gap-1 pt-1">
                    {img.source_page_url ? (
                      <a href={img.source_page_url} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-0.5 text-2xs text-muted hover:text-text">
                        Source <ExternalLink className="h-3 w-3" aria-hidden />
                      </a>
                    ) : null}
                    <span className="flex-1" />
                    {canApprove && img.approval_status !== "approved" ? (
                      <Button size="sm" variant="outline" onClick={() => approve.mutate(img)} loading={approve.isPending && approve.variables?.id === img.id}>
                        <Check className="h-3.5 w-3.5" /> Approve
                      </Button>
                    ) : null}
                    {canApprove && img.approval_status !== "rejected" ? <RejectButton onReject={(note) => reject.mutate({ img, note })} /> : null}
                  </div>
                </div>
              </li>
            ))}
          </ul>
        )}
      </Card>

      {canSearch ? (
        <Card>
          <CardHeader
            icon={Search}
            title="Find openly licensed images"
            description="Searches Openverse or Wikimedia Commons. Results are not stored until you save one. Check the licence warnings before saving."
          />
          <CardBody className="flex flex-col gap-3">
            <form onSubmit={submit} className="flex flex-wrap items-end gap-2">
              <Field label="Search" htmlFor="img-q" className="min-w-[200px] flex-1">
                <Input id="img-q" value={draft} onChange={(e) => setDraft(e.target.value)} placeholder="e.g. garment factory Dhaka" maxLength={200} />
              </Field>
              <Field label="Repository" htmlFor="img-provider">
                <Select
                  id="img-provider"
                  value={provider}
                  onChange={(e) => {
                    setProvider(e.target.value as "openverse" | "wikimedia");
                    setPage(1);
                  }}
                >
                  <option value="openverse">Openverse</option>
                  <option value="wikimedia">Wikimedia Commons</option>
                </Select>
              </Field>
              <Button type="submit" variant="primary" disabled={draft.trim().length < 2}>
                <Search className="h-4 w-4" /> Search
              </Button>
            </form>
            {provider === "openverse" ? (
              <div className="flex flex-wrap gap-4">
                <Checkbox
                  label="Commercial use allowed"
                  checked={commercialOnly}
                  onChange={(e) => {
                    setCommercialOnly(e.target.checked);
                    setPage(1);
                  }}
                />
                <Checkbox
                  label="Modification allowed"
                  checked={modAllowed}
                  onChange={(e) => {
                    setModAllowed(e.target.checked);
                    setPage(1);
                  }}
                />
              </div>
            ) : (
              <InlineAlert tone="info">Wikimedia Commons cannot be filtered by licence at search time; check each result’s warnings.</InlineAlert>
            )}

            {!term ? null : search.isLoading ? (
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
                {Array.from({ length: 10 }).map((_, i) => (
                  <Skeleton key={i} className="aspect-[4/3] w-full" />
                ))}
              </div>
            ) : search.isError ? (
              <ErrorState error={search.error} onRetry={() => search.refetch()} />
            ) : !search.data?.results.length ? (
              <EmptyState icon={Search} title={`No images found for “${term}”`} description="Try fewer or more general words, or the other repository." />
            ) : (
              <>
                <p className="text-xs text-muted">
                  {search.data.total != null ? `${formatNumber(search.data.total)} results` : "Results"} · page {search.data.page}
                  {search.data.page_count ? ` of ${formatNumber(pageCount)}` : ""}
                </p>
                <ul className={cn("grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5", search.isFetching && "opacity-70")}>
                  {search.data.results.map((r) => (
                    <li key={`${r.provider}:${r.provider_id}`} className={cn("flex flex-col rounded-lg border", r.blocked ? "border-danger/40" : "border-border")}>
                      <Thumb src={r.thumbnail_url} alt={r.title ?? "Search result"} />
                      <div className="flex flex-1 flex-col gap-1 p-2">
                        <p className="line-clamp-2 text-xs font-medium" title={r.title ?? undefined}>
                          {r.title || "Untitled"}
                        </p>
                        <p className="truncate text-2xs text-muted">
                          {r.creator ? `${r.creator} · ` : ""}
                          {r.license_name}
                        </p>
                        <Warnings items={r.warnings} compact />
                        <div className="mt-auto flex items-center gap-1 pt-1">
                          {r.source_page_url ? (
                            <a
                              href={r.source_page_url}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="rounded p-1 text-muted hover:bg-surface-2 hover:text-text"
                              aria-label={`Open source page for ${r.title ?? "image"}`}
                            >
                              <ExternalLink className="h-3.5 w-3.5" />
                            </a>
                          ) : null}
                          <span className="flex-1" />
                          {r.saved_id ? (
                            <Badge tone="success" icon={Check}>
                              Saved · {r.saved_status}
                            </Badge>
                          ) : r.blocked ? (
                            <Badge tone="danger" icon={AlertOctagon}>
                              Not usable
                            </Badge>
                          ) : (
                            <Button size="sm" variant="outline" onClick={() => save.mutate(r)} loading={save.isPending && save.variables?.provider_id === r.provider_id}>
                              <BookmarkPlus className="h-3.5 w-3.5" /> Save
                            </Button>
                          )}
                        </div>
                      </div>
                    </li>
                  ))}
                </ul>
                <div className="flex items-center justify-end gap-2">
                  <Button size="sm" variant="outline" disabled={page <= 1 || search.isFetching} onClick={() => setPage((p) => p - 1)}>
                    <ChevronLeft className="h-3.5 w-3.5" /> Previous
                  </Button>
                  <Button size="sm" variant="outline" disabled={page >= Math.min(pageCount, 50) || search.isFetching} onClick={() => setPage((p) => p + 1)}>
                    Next <ChevronRight className="h-3.5 w-3.5" />
                  </Button>
                </div>
              </>
            )}
          </CardBody>
        </Card>
      ) : null}
    </div>
  );
}

function RejectButton({ onReject }: { onReject: (note: string) => void }) {
  const [open, setOpen] = useState(false);
  const [note, setNote] = useState("");
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button size="sm" variant="ghost">
          <X className="h-3.5 w-3.5" /> Reject
        </Button>
      </PopoverTrigger>
      <PopoverContent className="flex w-64 flex-col gap-2 p-3">
        <label htmlFor="reject-note" className="text-sm font-medium">
          Reason (optional)
        </label>
        <Textarea id="reject-note" rows={2} value={note} onChange={(e) => setNote(e.target.value)} placeholder="e.g. licence unclear, wrong location" />
        <div className="flex justify-end gap-2">
          <Button size="sm" variant="ghost" onClick={() => setOpen(false)}>
            Cancel
          </Button>
          <Button
            size="sm"
            variant="danger"
            onClick={() => {
              onReject(note.trim());
              setOpen(false);
              setNote("");
            }}
          >
            Reject image
          </Button>
        </div>
      </PopoverContent>
    </Popover>
  );
}
