"use client";

import { AlertTriangle, CheckCircle2, ExternalLink, Image as ImageIcon, XCircle } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Time } from "@/components/ui/time";
import { formatDuration, hostOf } from "@/lib/utils";

export interface ProbeResult {
  ok: boolean;
  status: string;
  http_status: number | null;
  duration_ms: number | null;
  error: string | null;
  final_url: string | null;
  etag: string | null;
  last_modified: string | null;
  feed_title: string | null;
  feed_format: string | null;
  language: string | null;
  entries: number;
  warning?: string | null;
  sample: { title: string | null; url: string | null; published_at: string | null; has_summary: boolean; has_image: boolean }[];
}

/** Outcome of POST /sources/test or /sources/{id}/test. Nothing is stored by a test. */
export function ProbeResultPanel({ result }: { result: ProbeResult }) {
  if (!result.ok) {
    return (
      <div role="alert" className="rounded-lg border border-danger/30 bg-danger/5 p-3 text-sm">
        <p className="flex items-center gap-2 font-medium text-danger">
          <XCircle className="h-4 w-4" aria-hidden /> Test failed
          {result.http_status ? <span className="font-normal text-muted">HTTP {result.http_status}</span> : null}
        </p>
        <p className="mt-1 text-text/80">{result.error ?? `Status: ${result.status}`}</p>
        {result.status === "blocked" ? (
          <p className="mt-1 text-xs text-muted">The site’s robots.txt does not allow automated fetching of this URL. NEWSWIRE will not fetch it.</p>
        ) : null}
      </div>
    );
  }
  return (
    <div role="status" className="rounded-lg border border-success/30 bg-success/5 p-3 text-sm">
      <p className="flex flex-wrap items-center gap-2 font-medium text-success">
        <CheckCircle2 className="h-4 w-4" aria-hidden /> Feed works
        <span className="font-normal text-muted">
          {result.entries} entr{result.entries === 1 ? "y" : "ies"} · {result.feed_format ?? "feed"}
          {result.language ? ` · ${result.language}` : ""} · {formatDuration(result.duration_ms)}
        </span>
      </p>
      {result.feed_title ? <p className="mt-1 text-text">“{result.feed_title}”</p> : null}
      {result.final_url ? <p className="mt-0.5 break-all text-xs text-muted">Final URL: {result.final_url}</p> : null}
      {result.warning ? (
        <p className="mt-1 flex items-start gap-1.5 text-xs text-warning">
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden /> {result.warning}
        </p>
      ) : null}
      {result.sample.length ? (
        <ul className="mt-2 divide-y divide-border/70 rounded border border-border bg-surface">
          {result.sample.map((s, i) => (
            <li key={`${s.url}-${i}`} className="flex items-start gap-2 px-2.5 py-1.5">
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm">{s.title || <span className="italic text-muted">(no title)</span>}</p>
                <p className="flex flex-wrap items-center gap-2 text-xs text-muted">
                  <span>{hostOf(s.url)}</span>
                  {s.published_at ? <Time value={s.published_at} /> : <span>no publication time</span>}
                  {!s.has_summary ? <Badge tone="warning">no summary</Badge> : null}
                  {s.has_image ? <ImageIcon className="h-3 w-3" aria-label="has image" /> : null}
                </p>
              </div>
              {s.url ? (
                <a
                  href={s.url}
                  target="_blank"
                  rel="noopener noreferrer nofollow"
                  className="shrink-0 rounded p-1 text-muted hover:bg-surface-2 hover:text-text"
                  aria-label="Open entry"
                >
                  <ExternalLink className="h-3.5 w-3.5" />
                </a>
              ) : null}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
