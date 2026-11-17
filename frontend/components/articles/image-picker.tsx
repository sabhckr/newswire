"use client";

import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, Check, ImageOff, Images, Search, XCircle } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Pagination } from "@/components/ui/page";
import { EmptyState, ErrorState, Skeleton } from "@/components/ui/states";
import { api } from "@/lib/api";
import type { ImageOut, LicenseWarning, Page } from "@/lib/types";
import { cn } from "@/lib/utils";

export function imageWarnings(img: ImageOut | null | undefined): LicenseWarning[] {
  return ((img?.warnings ?? []) as unknown as LicenseWarning[]).filter((w) => w && typeof w.message === "string");
}

export function imageThumb(img: ImageOut): string | null {
  return img.local_thumbnail_url ?? img.local_url ?? img.thumbnail_url ?? null;
}

export function Thumb({ img, className }: { img: ImageOut; className?: string }) {
  const src = imageThumb(img);
  return src ? (
    // eslint-disable-next-line @next/next/no-img-element
    <img src={src} alt={img.title ?? ""} loading="lazy" className={cn("rounded border border-border bg-surface-2 object-cover", className)} />
  ) : (
    <span className={cn("flex items-center justify-center rounded border border-border bg-surface-2 text-muted", className)}>
      <ImageOff className="h-4 w-4" aria-hidden />
    </span>
  );
}

/** Choose a featured image from the approved library (licences already reviewed by an editor). */
export function ImagePickerDialog({
  open,
  onOpenChange,
  selectedId,
  onPick,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  selectedId: string | null;
  onPick: (img: ImageOut) => void;
}) {
  const [q, setQ] = useState("");
  const [term, setTerm] = useState("");
  const [page, setPage] = useState(1);
  useEffect(() => {
    const t = setTimeout(() => {
      setTerm(q.trim());
      setPage(1);
    }, 250);
    return () => clearTimeout(t);
  }, [q]);

  const res = useQuery({
    queryKey: ["images", "approved", term, page],
    queryFn: () => api<Page<ImageOut>>("/images", { query: { status: "approved", q: term || undefined, page, page_size: 24 } }),
    enabled: open,
  });

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        wide
        title="Choose a featured image"
        description="Only approved images appear here. Licence warnings are shown for a commercial publication."
      >
        <div className="mb-3 flex items-center gap-2">
          <div className="relative flex-1">
            <Search className="pointer-events-none absolute left-2 top-2 h-4 w-4 text-muted" aria-hidden />
            <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search title, creator or query…" className="pl-8" aria-label="Search approved images" />
          </div>
          <Link href="/images" className="whitespace-nowrap text-xs text-accent hover:underline">
            Image library
          </Link>
        </div>
        {res.isLoading ? (
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-4">
            {Array.from({ length: 8 }).map((_, i) => (
              <Skeleton key={i} className="aspect-[4/3] w-full" />
            ))}
          </div>
        ) : res.isError ? (
          <ErrorState error={res.error} onRetry={() => res.refetch()} />
        ) : !res.data?.items.length ? (
          <EmptyState
            icon={Images}
            title={term ? `No approved images match “${term}”` : "No approved images yet"}
            description="Search Openverse or Wikimedia Commons in the Image Library or a research workspace, then approve an image to use it here."
          />
        ) : (
          <>
            <ul className="grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-4">
              {res.data.items.map((img) => {
                const warnings = imageWarnings(img);
                const errors = warnings.filter((w) => w.level === "error");
                const selected = img.id === selectedId;
                return (
                  <li key={img.id}>
                    <button
                      type="button"
                      onClick={() => {
                        onPick(img);
                        onOpenChange(false);
                      }}
                      aria-pressed={selected}
                      className={cn(
                        "group flex w-full flex-col overflow-hidden rounded-lg border text-left hover:border-accent/60",
                        selected ? "border-accent ring-2 ring-accent/40" : "border-border",
                      )}
                    >
                      <span className="relative block">
                        <Thumb img={img} className="aspect-[4/3] w-full rounded-none border-0" />
                        {selected ? (
                          <span className="absolute right-1.5 top-1.5 rounded-full bg-accent p-0.5 text-accent-fg">
                            <Check className="h-3.5 w-3.5" aria-label="Selected" />
                          </span>
                        ) : null}
                      </span>
                      <span className="flex flex-col gap-1 p-2">
                        <span className="line-clamp-1 text-xs font-medium">{img.title || "Untitled image"}</span>
                        <span className="line-clamp-1 text-2xs text-muted">{img.creator || img.provider}</span>
                        <span className="flex flex-wrap gap-1">
                          <Badge tone={img.license?.is_known ? "neutral" : "danger"}>{img.license?.name ?? "Unknown licence"}</Badge>
                          {errors.length ? (
                            <Badge tone="danger" icon={XCircle} title={errors.map((w) => w.message).join("\n")}>
                              {errors.length} blocking
                            </Badge>
                          ) : warnings.some((w) => w.level === "warning") ? (
                            <Badge tone="warning" icon={AlertTriangle}>
                              Check terms
                            </Badge>
                          ) : null}
                        </span>
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
            <Pagination className="mt-3 border-0 px-0" page={page} pageSize={24} total={res.data.total} onPage={setPage} />
          </>
        )}
        {selectedId ? (
          <div className="mt-2 flex justify-end">
            <Button size="sm" variant="ghost" onClick={() => onOpenChange(false)}>
              Keep current image
            </Button>
          </div>
        ) : null}
      </DialogContent>
    </Dialog>
  );
}
