"use client";

import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, ExternalLink, Monitor, RefreshCw, Smartphone } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { ErrorState, Skeleton } from "@/components/ui/states";
import { StatusBadge } from "@/components/ui/status-badge";
import { Time } from "@/components/ui/time";
import { API_BASE, api } from "@/lib/api";
import type { ArticleOut } from "@/lib/types";
import { cn } from "@/lib/utils";

type Device = "desktop" | "phone";

export default function ArticlePreviewPage() {
  const { id } = useParams<{ id: string }>();
  const [device, setDevice] = useState<Device>("desktop");
  const [nonce, setNonce] = useState(0);
  const [loaded, setLoaded] = useState(false);
  const article = useQuery({ queryKey: ["article", id], queryFn: () => api<ArticleOut>(`/articles/${id}`) });
  const src = `${API_BASE}/articles/${id}/preview`;

  return (
    <div className="mx-auto flex max-w-7xl flex-col gap-3">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="min-w-0">
          <Link href={`/articles/${id}`} className="inline-flex items-center gap-1 text-xs text-muted hover:text-accent">
            <ArrowLeft className="h-3.5 w-3.5" /> Back to editor
          </Link>
          <div className="mt-1 flex min-w-0 items-center gap-2">
            {article.isLoading ? (
              <Skeleton className="h-6 w-64" />
            ) : (
              <h1 className="truncate text-lg font-semibold tracking-tight">{article.data?.headline ?? "Preview"}</h1>
            )}
            {article.data ? <StatusBadge status={article.data.status} className="shrink-0" /> : null}
          </div>
          {article.data ? (
            <p className="mt-0.5 text-xs text-muted">
              Preview of saved version v{article.data.version} · <Time value={article.data.updated_at} />. Unsaved edits in the editor are not shown.
            </p>
          ) : null}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <div role="group" aria-label="Preview width" className="inline-flex rounded border border-border p-0.5">
            {(
              [
                ["desktop", "Desktop", Monitor],
                ["phone", "Phone", Smartphone],
              ] as const
            ).map(([d, label, Icon]) => (
              <button
                key={d}
                type="button"
                aria-pressed={device === d}
                onClick={() => setDevice(d)}
                className={cn(
                  "inline-flex items-center gap-1.5 rounded px-2.5 py-1 text-xs",
                  device === d ? "bg-accent/10 font-medium text-accent" : "text-muted hover:text-text",
                )}
              >
                <Icon className="h-3.5 w-3.5" aria-hidden /> {label}
              </button>
            ))}
          </div>
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              setLoaded(false);
              setNonce((n) => n + 1);
              void article.refetch();
            }}
          >
            <RefreshCw className="h-3.5 w-3.5" /> Reload
          </Button>
          <a
            href={src}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1 text-xs text-accent hover:underline"
          >
            Open alone <ExternalLink className="h-3 w-3" aria-hidden />
          </a>
        </div>
      </div>

      {article.isError ? (
        <ErrorState error={article.error} onRetry={() => article.refetch()} />
      ) : (
        <div className="flex justify-center rounded-lg border border-border bg-surface-2 p-2 sm:p-4">
          <div
            className={cn(
              "relative w-full overflow-hidden bg-surface shadow-sm transition-[max-width] duration-200",
              device === "phone" ? "max-w-[390px] rounded-[1.25rem] border-[6px] border-border" : "max-w-5xl rounded border border-border",
            )}
          >
            {!loaded ? <Skeleton className="absolute inset-0 rounded-none" /> : null}
            {/* Sandboxed without scripts: the preview is server-rendered, sanitised HTML. Same-origin lets /media images load. */}
            <iframe
              key={nonce}
              src={src}
              title={`Preview of ${article.data?.headline ?? "the article"}`}
              sandbox="allow-same-origin allow-popups allow-popups-to-escape-sandbox"
              referrerPolicy="no-referrer"
              onLoad={() => setLoaded(true)}
              className={cn("block w-full", device === "phone" ? "h-[760px]" : "h-[calc(100vh-13rem)] min-h-[560px]")}
            />
          </div>
        </div>
      )}
    </div>
  );
}
