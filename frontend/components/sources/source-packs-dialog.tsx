"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { Package } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { ErrorState, SkeletonRows } from "@/components/ui/states";
import { api, errorMessage } from "@/lib/api";
import { formatDate, pluralize } from "@/lib/utils";

export interface SourcePack {
  id: string;
  name: string;
  description: string | null;
  verified_at: string | null;
  notes: string | null;
  count: number;
  languages: string[];
  publishers: string[];
}

export interface ImportResult {
  created: number;
  skipped: number;
  errors: { index: number; feed_url?: string; error: string }[];
}

/** Bundled lists of public feeds (GET /sources/packs) that can be added in one step. */
export function SourcePacksDialog({
  open,
  onOpenChange,
  onImported,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  onImported: (r: ImportResult) => void;
}) {
  const packs = useQuery({ queryKey: ["source-packs"], queryFn: () => api<SourcePack[]>("/sources/packs"), enabled: open });
  const add = useMutation({
    mutationFn: (id: string) => api<ImportResult>(`/sources/packs/${encodeURIComponent(id)}/import`, { method: "POST" }),
    onSuccess: (r) => {
      onImported(r);
      onOpenChange(false);
    },
  });

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      {open ? (
        <DialogContent
          title="Source packs"
          description="Ready-made lists of public feeds. Feeds you already have are skipped; every added source can be edited or disabled afterwards."
        >
          {packs.isLoading ? <SkeletonRows rows={2} /> : null}
          {packs.isError ? <ErrorState error={packs.error} onRetry={() => packs.refetch()} /> : null}
          {packs.data && packs.data.length === 0 ? <p className="text-sm text-muted">No source packs are installed.</p> : null}
          {add.isError ? <p className="mb-2 text-sm text-danger">{errorMessage(add.error)}</p> : null}
          <ul className="space-y-3">
            {packs.data?.map((p) => (
              <li key={p.id} className="rounded-lg border border-border p-3">
                <div className="flex flex-wrap items-start gap-3">
                  <Package className="mt-0.5 h-5 w-5 shrink-0 text-muted" aria-hidden />
                  <div className="min-w-0 flex-1">
                    <p className="font-medium">{p.name}</p>
                    {p.description ? <p className="mt-0.5 text-sm text-muted">{p.description}</p> : null}
                    <p className="mt-1 text-xs text-muted">
                      {pluralize(p.count, "feed")} from {pluralize(p.publishers.length, "publisher")}
                      {p.languages.length ? ` · ${p.languages.join(", ")}` : ""}
                      {p.verified_at ? ` · checked ${formatDate(p.verified_at)}` : ""}
                    </p>
                  </div>
                  <Button size="sm" variant="primary" onClick={() => add.mutate(p.id)} loading={add.isPending && add.variables === p.id}>
                    Add {pluralize(p.count, "feed")}
                  </Button>
                </div>
                {p.notes ? <p className="mt-2 text-xs text-muted">{p.notes}</p> : null}
                <details className="mt-2 text-xs text-muted">
                  <summary className="cursor-pointer">Publishers</summary>
                  <p className="mt-1 leading-5">{p.publishers.join(" · ")}</p>
                </details>
              </li>
            ))}
          </ul>
        </DialogContent>
      ) : null}
    </Dialog>
  );
}
