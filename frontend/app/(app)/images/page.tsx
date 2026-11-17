"use client";

import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BookmarkPlus, Check, ExternalLink, ImageIcon, Images, Search, ShieldAlert, Upload } from "lucide-react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

import { ImageDetailDialog } from "@/components/images/image-detail";
import { imageWarnings, LicenceBadge, previewSrc, Thumb, WarningList } from "@/components/images/media";
import { UploadDialog } from "@/components/images/upload-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Checkbox, Input, Select } from "@/components/ui/input";
import { PageHeader, Pagination, Toolbar } from "@/components/ui/page";
import { EmptyState, ErrorState, InlineAlert, Skeleton } from "@/components/ui/states";
import { StatusBadge } from "@/components/ui/status-badge";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Tip } from "@/components/ui/tooltip";
import { api, errorMessage } from "@/lib/api";
import { PERMS, useSession } from "@/lib/session";
import type { ImageOut, ImageSearchResponse, ImageSearchResult, LicenseOut, Page } from "@/lib/types";
import { cn, formatNumber, hostOf } from "@/lib/utils";

const PAGE_SIZE = 24;


function useDebounced<T>(value: T, ms = 300): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

function GridSkeleton({ n = 12 }: { n?: number }) {
  return (
    <div className="grid grid-cols-2 gap-3 p-3 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-6" aria-hidden>
      {Array.from({ length: n }).map((_, i) => (
        <div key={i} className="flex flex-col gap-1.5">
          <Skeleton className="aspect-[4/3] w-full" />
          <Skeleton className="h-3 w-3/4" />
          <Skeleton className="h-3 w-1/2" />
        </div>
      ))}
    </div>
  );
}

/* ----------------------------------------------------------------------------------------- */

function Library({ onOpen, onUpload, assignmentId }: { onOpen: (id: string) => void; onUpload: (() => void) | null; assignmentId: string | null }) {
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("");
  const [provider, setProvider] = useState("");
  const [licence, setLicence] = useState("");
  const [page, setPage] = useState(1);
  const term = useDebounced(q.trim());
  useEffect(() => setPage(1), [term, status, provider, licence]);

  const licences = useQuery({ queryKey: ["image-licenses"], queryFn: () => api<LicenseOut[]>("/images/licenses"), staleTime: 5 * 60_000 });
  const licenceCodes = Array.from(new Map((licences.data ?? []).map((l) => [l.code, l.name])).entries());

  const list = useQuery({
    queryKey: ["images", { term, status, provider, licence, page, assignmentId }],
    queryFn: () =>
      api<Page<ImageOut>>("/images", {
        query: {
          q: term || undefined,
          status: status || undefined,
          provider: provider || undefined,
          license_code: licence || undefined,
          assignment_id: assignmentId || undefined,
          page,
          page_size: PAGE_SIZE,
        },
      }),
    placeholderData: keepPreviousData,
  });
  const filtered = Boolean(term || status || provider || licence);

  return (
    <Card>
      <Toolbar>
        <div className="relative min-w-[180px] flex-1">
          <Search className="pointer-events-none absolute left-2 top-2 h-4 w-4 text-muted" aria-hidden />
          <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Title, creator or search term" className="pl-8" aria-label="Search the library" />
        </div>
        <Select aria-label="Approval status" value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">Any status</option>
          <option value="pending">Pending review</option>
          <option value="approved">Approved</option>
          <option value="rejected">Rejected</option>
        </Select>
        <Select aria-label="Origin" value={provider} onChange={(e) => setProvider(e.target.value)}>
          <option value="">Any origin</option>
          <option value="openverse">Openverse</option>
          <option value="wikimedia">Wikimedia Commons</option>
          <option value="upload">Uploaded</option>
        </Select>
        <Select aria-label="Licence" value={licence} onChange={(e) => setLicence(e.target.value)}>
          <option value="">Any licence</option>
          {licenceCodes.map(([code, name]) => (
            <option key={code} value={code}>
              {name}
            </option>
          ))}
        </Select>
      </Toolbar>

      {assignmentId ? (
        <p className="border-b border-border px-3 py-1.5 text-xs text-muted">Showing images saved for one headline.</p>
      ) : null}

      {list.isLoading ? (
        <GridSkeleton />
      ) : list.isError ? (
        <ErrorState error={list.error} onRetry={() => list.refetch()} />
      ) : !list.data?.items.length ? (
        filtered ? (
          <EmptyState
            icon={Search}
            title="No images match these filters"
            description="Clear the filters to see the whole library."
            action={
              <Button
                variant="outline"
                onClick={() => {
                  setQ("");
                  setStatus("");
                  setProvider("");
                  setLicence("");
                }}
              >
                Clear filters
              </Button>
            }
          />
        ) : (
          <EmptyState
            icon={Images}
            title="The image library is empty"
            description="Search Openverse or Wikimedia Commons for openly licensed images and save them for review, or upload images you hold the rights to."
            action={
              onUpload ? (
                <Button variant="outline" onClick={onUpload}>
                  <Upload className="h-4 w-4" /> Upload an image
                </Button>
              ) : undefined
            }
          />
        )
      ) : (
        <>
          <ul
            className={cn("grid grid-cols-2 gap-3 p-3 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-6", list.isPlaceholderData && "opacity-60")}
            aria-label="Images"
          >
            {list.data.items.map((img) => {
              const issues = imageWarnings(img).filter((w) => w.level === "error" && w.code !== "not_approved");
              return (
                <li key={img.id}>
                  <button
                    type="button"
                    onClick={() => onOpen(img.id)}
                    className="group flex w-full flex-col gap-1.5 rounded text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2 focus-visible:ring-offset-bg"
                  >
                    <Thumb
                      src={previewSrc(img)}
                      alt={img.title ?? "Untitled image"}
                      className="aspect-[4/3] w-full rounded border border-border transition-colors group-hover:border-accent"
                    />
                    <span className="line-clamp-2 text-xs font-medium leading-snug">{img.title || "Untitled"}</span>
                    <span className="truncate text-2xs text-muted">
                      {img.creator || "Unknown creator"} · {img.provider === "upload" ? "Uploaded" : img.provider === "wikimedia" ? "Wikimedia" : "Openverse"}
                    </span>
                    <span className="flex flex-wrap gap-1">
                      <StatusBadge status={img.approval_status} />
                      <LicenceBadge code={img.license?.code} name={img.license?.name ?? img.license_raw} />
                      {issues.length ? (
                        <Badge tone="danger" icon={ShieldAlert} title={issues.map((w) => w.message).join("\n")}>
                          {issues.length} issue{issues.length === 1 ? "" : "s"}
                        </Badge>
                      ) : null}
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
          <Pagination page={page} pageSize={PAGE_SIZE} total={list.data.total} onPage={setPage} className="border-t border-border" />
        </>
      )}
    </Card>
  );
}

/* ----------------------------------------------------------------------------------------- */

function RepositorySearch({ onOpen, assignmentId, initialQuery }: { onOpen: (id: string) => void; assignmentId: string | null; initialQuery: string }) {
  const qc = useQueryClient();
  const [input, setInput] = useState(initialQuery);
  const [query, setQuery] = useState(initialQuery.trim().length >= 2 ? initialQuery.trim() : "");
  const [provider, setProvider] = useState<"openverse" | "wikimedia">("openverse");
  const [commercialOnly, setCommercialOnly] = useState(false);
  const [modifiable, setModifiable] = useState(false);
  const [page, setPage] = useState(1);
  useEffect(() => setPage(1), [query, provider, commercialOnly, modifiable]);

  const search = useQuery({
    queryKey: ["image-search", { query, provider, commercialOnly, modifiable, page }],
    queryFn: () =>
      api<ImageSearchResponse>("/images/search", {
        query: {
          q: query,
          provider,
          page,
          ...(provider === "openverse" ? { commercial_only: commercialOnly, modification_allowed: modifiable } : {}),
        },
      }),
    enabled: query.length >= 2,
    placeholderData: keepPreviousData,
    staleTime: 5 * 60_000,
    retry: false,
  });

  const save = useMutation({
    mutationFn: (r: ImageSearchResult) => {
      const { license_name: _n, warnings: _w, blocked: _b, saved_id: _s, saved_status: _ss, ...candidate } = r;
      return api<ImageOut>("/images", { body: { candidate, query, assignment_id: assignmentId || null } });
    },
    onSuccess: (img) => {
      qc.invalidateQueries({ queryKey: ["image-search"] });
      qc.invalidateQueries({ queryKey: ["images"] });
      toast.success("Saved to the library for review", { action: { label: "Open", onClick: () => onOpen(img.id) } });
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  const pageCount = search.data?.page_count ?? 1;

  return (
    <Card>
      <form
        className="flex flex-wrap items-center gap-2 border-b border-border px-3 py-2"
        onSubmit={(e) => {
          e.preventDefault();
          setQuery(input.trim());
        }}
        role="search"
      >
        <div className="relative min-w-[200px] flex-1">
          <Search className="pointer-events-none absolute left-2 top-2 h-4 w-4 text-muted" aria-hidden />
          <Input value={input} onChange={(e) => setInput(e.target.value)} placeholder="Search openly licensed images" className="pl-8" aria-label="Search terms" minLength={2} />
        </div>
        <Select aria-label="Repository" value={provider} onChange={(e) => setProvider(e.target.value as "openverse" | "wikimedia")}>
          <option value="openverse">Openverse</option>
          <option value="wikimedia">Wikimedia Commons</option>
        </Select>
        <Button type="submit" variant="primary" disabled={input.trim().length < 2}>
          Search
        </Button>
        {provider === "openverse" ? (
          <div className="flex w-full flex-wrap gap-x-4 gap-y-1">
            <Checkbox label="Commercial use allowed" checked={commercialOnly} onChange={(e) => setCommercialOnly(e.target.checked)} />
            <Checkbox label="Modifications allowed" checked={modifiable} onChange={(e) => setModifiable(e.target.checked)} />
          </div>
        ) : (
          <p className="w-full text-2xs text-muted">Wikimedia Commons results are checked for licence after retrieval; filter by the warnings shown.</p>
        )}
      </form>

      {query.length < 2 ? (
        <EmptyState
          icon={ImageIcon}
          title="Search open repositories"
          description="Results come from Openverse or Wikimedia Commons with their licence and attribution. Nothing is stored until you save an image for review, and nothing is downloaded until an approver approves it."
        />
      ) : search.isLoading ? (
        <GridSkeleton n={8} />
      ) : search.isError ? (
        <ErrorState error={search.error} onRetry={() => search.refetch()} />
      ) : !search.data?.results.length ? (
        <EmptyState icon={Search} title={`No results for “${query}”`} description="Try broader terms, the other repository, or fewer licence filters." />
      ) : (
        <>
          <p className="px-3 pt-2 text-xs text-muted">
            {search.data.total !== null ? `${formatNumber(search.data.total)} results` : "Results"} from {provider === "openverse" ? "Openverse" : "Wikimedia Commons"}
          </p>
          <ul className={cn("grid grid-cols-1 gap-3 p-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4", search.isPlaceholderData && "opacity-60")} aria-label="Search results">
            {search.data.results.map((r) => {
              const errors = r.warnings.filter((w) => w.level === "error" && w.code !== "not_approved" && w.code !== "not_downloaded");
              const notes = r.warnings.filter((w) => w.level !== "error" && w.code !== "not_downloaded");
              const saving = save.isPending && save.variables?.provider_id === r.provider_id;
              return (
                <li key={`${r.provider}-${r.provider_id}`} className="flex flex-col overflow-hidden rounded border border-border bg-surface">
                  <Thumb src={r.thumbnail_url} alt={r.title ?? "Search result"} className="aspect-[4/3] w-full border-b border-border" />
                  <div className="flex flex-1 flex-col gap-1.5 p-2.5">
                    <p className="line-clamp-2 text-sm font-medium leading-snug">{r.title || "Untitled"}</p>
                    <p className="truncate text-xs text-muted">
                      {r.creator || "Unknown creator"}
                      {r.repository ? ` · ${r.repository}` : ""}
                      {r.width && r.height ? ` · ${r.width}×${r.height}` : ""}
                    </p>
                    <div className="flex flex-wrap gap-1">
                      <LicenceBadge code={r.license_code} name={r.license_name} />
                      {r.blocked ? (
                        <Badge tone="danger" icon={ShieldAlert}>
                          Blocked
                        </Badge>
                      ) : null}
                      {r.saved_status ? <StatusBadge status={r.saved_status} /> : null}
                    </div>
                    <WarningList warnings={[...errors, ...notes]} />
                    <div className="mt-auto flex flex-wrap items-center gap-2 pt-1">
                      {r.saved_id ? (
                        <Button size="sm" variant="outline" onClick={() => onOpen(r.saved_id!)}>
                          <Check className="h-3.5 w-3.5" /> Saved · open
                        </Button>
                      ) : (
                        <Tip content={r.blocked ? "Saved images with blocking issues cannot be approved for use, but you can keep them for reference" : undefined}>
                          <Button size="sm" variant={r.blocked ? "outline" : "primary"} onClick={() => save.mutate(r)} loading={saving}>
                            {saving ? null : <BookmarkPlus className="h-3.5 w-3.5" />} Save for review
                          </Button>
                        </Tip>
                      )}
                      {r.source_page_url ? (
                        <a
                          href={r.source_page_url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="ml-auto inline-flex items-center gap-1 rounded text-xs text-muted hover:text-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
                        >
                          {hostOf(r.source_page_url)} <ExternalLink className="h-3 w-3" aria-hidden />
                        </a>
                      ) : null}
                    </div>
                  </div>
                </li>
              );
            })}
          </ul>
          {pageCount > 1 ? (
            <div className="flex items-center justify-between border-t border-border px-3 py-2 text-xs text-muted">
              <span>
                Page {page}
                {provider === "openverse" ? ` of ${formatNumber(Math.min(pageCount, 50))}` : ""}
              </span>
              <span className="flex gap-2">
                <Button size="sm" variant="outline" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>
                  Previous
                </Button>
                <Button size="sm" variant="outline" disabled={page >= Math.min(pageCount, 50)} onClick={() => setPage((p) => p + 1)}>
                  Next
                </Button>
              </span>
            </div>
          ) : null}
        </>
      )}
    </Card>
  );
}

/* ----------------------------------------------------------------------------------------- */

export default function ImagesPage() {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const { can } = useSession();
  const canWrite = can(PERMS.imagesWrite);
  const tab = params.get("tab") === "search" && canWrite ? "search" : "library";
  const openId = params.get("id");
  const assignmentId = params.get("assignment_id");
  const [uploading, setUploading] = useState(false);

  const setParam = useCallback(
    (key: string, value: string | null) => {
      const next = new URLSearchParams(params.toString());
      if (value) next.set(key, value);
      else next.delete(key);
      const qs = next.toString();
      router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
    },
    [params, pathname, router],
  );
  const open = useCallback((id: string) => setParam("id", id), [setParam]);

  return (
    <div className="mx-auto max-w-7xl">
      <PageHeader
        title="Image library"
        description="Openly licensed and uploaded images with their provenance. Every image needs an approver's licence review before it can be used in an article."
        actions={
          canWrite ? (
            <Button variant="primary" onClick={() => setUploading(true)}>
              <Upload className="h-4 w-4" /> Upload
            </Button>
          ) : null
        }
      />

      {!canWrite ? (
        <InlineAlert tone="info" className="mb-4">
          You can browse the library. Searching repositories, saving and uploading need image editing permission.
        </InlineAlert>
      ) : null}

      <Tabs value={tab} onValueChange={(v) => setParam("tab", v === "search" ? "search" : null)}>
        {canWrite ? (
          <TabsList className="mb-3">
            <TabsTrigger value="library">
              <Images className="h-4 w-4" /> Library
            </TabsTrigger>
            <TabsTrigger value="search">
              <Search className="h-4 w-4" /> Search repositories
            </TabsTrigger>
          </TabsList>
        ) : null}
        <TabsContent value="library">
          <Library onOpen={open} onUpload={canWrite ? () => setUploading(true) : null} assignmentId={assignmentId} />
        </TabsContent>
        {canWrite ? (
          <TabsContent value="search">
            <RepositorySearch onOpen={open} assignmentId={assignmentId} initialQuery={params.get("q") ?? ""} />
          </TabsContent>
        ) : null}
      </Tabs>

      <ImageDetailDialog id={openId} onOpenChange={(o) => (!o ? setParam("id", null) : undefined)} />
      {canWrite ? <UploadDialog open={uploading} onOpenChange={setUploading} assignmentId={assignmentId} onUploaded={(img) => open(img.id)} /> : null}
    </div>
  );
}
