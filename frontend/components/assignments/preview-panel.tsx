"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { ChevronDown, ChevronRight, ExternalLink, Eye, Loader2, SearchX } from "lucide-react";
import { useEffect, useState } from "react";

import { ScoreBreakdown, ScorePill, type BreakdownLine } from "@/components/assignments/score-breakdown";
import { Card, CardHeader } from "@/components/ui/card";
import { Select } from "@/components/ui/input";
import { EmptyState, ErrorState, SkeletonRows } from "@/components/ui/states";
import { Time } from "@/components/ui/time";
import { api } from "@/lib/api";
import type { AssignmentIn } from "@/lib/types";
import { cn, formatNumber } from "@/lib/utils";

interface PreviewItem {
  news_item_id: string;
  title: string | null;
  url: string;
  publisher: string | null;
  published_at: string | null;
  score: number;
  explanation: string;
  breakdown: BreakdownLine[];
}
interface PreviewResult {
  scanned: number;
  matched: number;
  below_threshold: number;
  items: PreviewItem[];
}

function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

/**
 * Dry-runs draft rules against recently collected reports (POST /assignments/preview).
 * Nothing is stored. `body` is null while the rules are not yet previewable; `notReady` says why.
 */
export function PreviewPanel({ body, notReady, className }: { body: AssignmentIn | null; notReady?: string; className?: string }) {
  const [days, setDays] = useState(7);
  const key = useDebounced(body ? JSON.stringify(body) : "", 700);
  const preview = useQuery({
    queryKey: ["assignment-preview", key, days],
    queryFn: ({ signal }) => api<PreviewResult>("/assignments/preview", { method: "POST", body: JSON.parse(key), query: { days }, signal }),
    enabled: !!key,
    placeholderData: keepPreviousData,
    staleTime: 30_000,
    retry: false,
  });
  const [open, setOpen] = useState<string | null>(null);
  const pending = !!body && (key !== JSON.stringify(body) || preview.isFetching);
  const minScore = body?.min_score;

  return (
    <Card className={cn("flex flex-col", className)} aria-live="polite" aria-busy={pending}>
      <CardHeader
        icon={Eye}
        title="Live preview"
        description="How these rules score reports already collected. Nothing is saved until you press Save."
        actions={
          <>
            {pending ? <Loader2 className="h-4 w-4 animate-spin text-muted" aria-label="Updating preview" /> : null}
            <label htmlFor="preview-days" className="sr-only">
              Preview window
            </label>
            <Select id="preview-days" value={days} onChange={(e) => setDays(Number(e.target.value))} className="h-7 w-auto text-xs">
              <option value={1}>Last 24 h</option>
              <option value={3}>Last 3 days</option>
              <option value={7}>Last 7 days</option>
              <option value={14}>Last 14 days</option>
              <option value={30}>Last 30 days</option>
            </Select>
          </>
        }
      />
      {!body ? (
        <EmptyState icon={Eye} title="Preview appears here" description={notReady ?? "Add a primary keyword to see which reports would match."} />
      ) : preview.isError ? (
        <ErrorState error={preview.error} onRetry={() => preview.refetch()} />
      ) : !preview.data ? (
        <SkeletonRows rows={6} />
      ) : (
        <>
          <dl className="grid grid-cols-3 divide-x divide-border border-b border-border text-center">
            <div className="px-2 py-2">
              <dt className="text-2xs uppercase tracking-wide text-muted">Scanned</dt>
              <dd className="tabular text-base font-semibold">{formatNumber(preview.data.scanned)}</dd>
            </div>
            <div className="px-2 py-2">
              <dt className="text-2xs uppercase tracking-wide text-muted">Would match</dt>
              <dd className="tabular text-base font-semibold text-success">{formatNumber(preview.data.matched)}</dd>
            </div>
            <div className="px-2 py-2">
              <dt className="text-2xs uppercase tracking-wide text-muted">Near misses</dt>
              <dd className="tabular text-base font-semibold">{formatNumber(preview.data.below_threshold)}</dd>
            </div>
          </dl>
          {!preview.data.items.length ? (
            <EmptyState
              icon={SearchX}
              title={preview.data.scanned ? "No reports would match" : "No reports collected in this window"}
              description={
                !preview.data.scanned
                  ? "Widen the window, or check that your sources are being fetched."
                  : preview.data.below_threshold
                    ? `${formatNumber(preview.data.below_threshold)} reports mention your terms but score below the threshold. Lower the minimum score, add synonyms, or loosen required phrases.`
                    : "Try broader keywords, synonyms or alternate spellings, or remove required phrases and place requirements."
              }
            />
          ) : (
            <ul className={cn("scroll-thin max-h-[70vh] divide-y divide-border overflow-y-auto", pending && "opacity-70")}>
              {preview.data.items.map((it) => {
                const expanded = open === it.news_item_id;
                return (
                  <li key={it.news_item_id} className="px-3 py-2">
                    <div className="flex items-start gap-2">
                      <ScorePill score={it.score} minScore={minScore} />
                      <div className="min-w-0 flex-1">
                        <a href={it.url} target="_blank" rel="noopener noreferrer" className="group line-clamp-2 text-sm font-medium hover:text-accent">
                          {it.title || it.url}
                          <ExternalLink className="ml-1 inline h-3 w-3 text-muted opacity-0 group-hover:opacity-100" aria-label="opens original" />
                        </a>
                        <p className="mt-0.5 truncate text-xs text-muted">
                          {it.publisher ?? "Unknown publisher"} · <Time value={it.published_at} />
                        </p>
                        <button
                          type="button"
                          className="mt-1 inline-flex items-start gap-1 text-left text-xs text-muted hover:text-text"
                          aria-expanded={expanded}
                          onClick={() => setOpen(expanded ? null : it.news_item_id)}
                        >
                          {expanded ? <ChevronDown className="mt-0.5 h-3 w-3 shrink-0" /> : <ChevronRight className="mt-0.5 h-3 w-3 shrink-0" />}
                          <span className="line-clamp-2">{it.explanation}</span>
                        </button>
                        {expanded ? <ScoreBreakdown className="mt-1.5" lines={it.breakdown} score={it.score} minScore={minScore} /> : null}
                      </div>
                    </div>
                  </li>
                );
              })}
              {preview.data.matched > preview.data.items.length ? (
                <li className="px-3 py-2 text-center text-xs text-muted">
                  Showing the top {preview.data.items.length} of {formatNumber(preview.data.matched)} matches
                </li>
              ) : null}
            </ul>
          )}
        </>
      )}
    </Card>
  );
}
