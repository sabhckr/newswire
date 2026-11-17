"use client";

import { useMutation } from "@tanstack/react-query";
import { CheckCircle2, Search, XCircle } from "lucide-react";
import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Time } from "@/components/ui/time";
import { api, errorMessage } from "@/lib/api";
import { pluralize } from "@/lib/utils";

export interface FeedCandidate {
  url: string;
  source_type: "rss" | "atom" | "json" | "sitemap";
  title: string | null;
  found_by: string;
  ok: boolean;
  entries: number;
  feed_format: string | null;
  language: string | null;
  latest: string | null;
  error: string | null;
  note: string | null;
  same_site: boolean;
  /** A plain sitemap: headlines come from each story's page (page metadata lookup). */
  needs_page_metadata: boolean;
  /** A few headlines this candidate gives, for checking that it is the right feed. */
  samples: string[];
  /** The newest entry is more than three days old: the feed has probably stopped. */
  stale: boolean;
}

export interface DiscoverResult {
  site: string;
  site_title: string | null;
  home_page_error: string | null;
  candidates: FeedCandidate[];
}

const FORMAT_LABEL: Record<string, string> = {
  rss: "RSS",
  atom: "Atom",
  jsonfeed: "JSON Feed",
  json_api: "JSON",
  news_sitemap: "News sitemap",
  sitemap: "Sitemap",
};

/** Looks for the feeds and news sitemaps a website advertises (POST /sources/discover). */
export function FeedFinder({
  respectRobots,
  onUse,
}: {
  respectRobots: boolean;
  onUse: (candidate: FeedCandidate, result: DiscoverResult) => void;
}) {
  const [site, setSite] = useState("");
  const [result, setResult] = useState<DiscoverResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const find = useMutation({
    mutationFn: () => {
      const raw = site.trim();
      const url = /^https?:\/\//i.test(raw) ? raw : `https://${raw}`;
      return api<DiscoverResult>("/sources/discover", { body: { url, respect_robots: respectRobots } });
    },
    onMutate: () => {
      setResult(null);
      setError(null);
    },
    onSuccess: setResult,
    onError: (e) => setError(errorMessage(e)),
  });

  const working = result?.candidates.filter((c) => c.ok) ?? [];
  const failed = result?.candidates.filter((c) => !c.ok) ?? [];

  return (
    <div className="rounded-lg border border-dashed border-border p-3">
      {/* Not a <form>: this sits inside the source form, and forms cannot be nested. */}
      <div className="flex flex-wrap items-end gap-2">
        <label className="min-w-0 flex-1 basis-56 text-sm">
          <span className="mb-1 block font-medium">Find feeds on a website</span>
          <Input
            value={site}
            onChange={(e) => setSite(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                if (site.trim()) find.mutate();
              }
            }}
            inputMode="url"
            placeholder="https://www.example.com"
            aria-describedby="ff-hint"
          />
        </label>
        <Button size="sm" variant="outline" onClick={() => find.mutate()} loading={find.isPending} disabled={!site.trim()}>
          <Search className="h-3.5 w-3.5" /> Find feeds
        </Button>
      </div>
      <p id="ff-hint" className="mt-1 text-xs text-muted">
        Checks the home page&apos;s feed links and the news sitemaps in robots.txt, then tests each one. This can take a
        few seconds.
      </p>

      {error ? <p className="mt-2 text-sm text-danger">{error}</p> : null}

      {result ? (
        <div className="mt-3 space-y-2" aria-live="polite">
          {result.home_page_error ? (
            <p className="text-xs text-muted">Home page: {result.home_page_error}</p>
          ) : null}
          {working.length === 0 ? (
            <p className="text-sm text-muted">
              No working feed or news sitemap found. Ask the publisher, or check the site footer for an RSS link.
            </p>
          ) : null}
          <ul className="space-y-2">
            {working.map((c) => (
              <li key={c.url} className="flex flex-wrap items-start gap-2 rounded-md bg-surface-2 px-3 py-2 text-sm">
                <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-success" aria-hidden />
                <div className="min-w-0 flex-1">
                  <p className="flex flex-wrap items-center gap-2">
                    <Badge tone="info">{FORMAT_LABEL[c.feed_format ?? ""] ?? c.source_type.toUpperCase()}</Badge>
                    {c.title ? <span className="font-medium">{c.title}</span> : null}
                    {c.language ? <span className="text-xs text-muted">{c.language}</span> : null}
                    {!c.same_site ? <Badge tone="warning">other site</Badge> : null}
                    {c.needs_page_metadata ? <Badge tone="warning">headlines from story pages</Badge> : null}
                    {c.stale ? <Badge tone="danger">not updated recently</Badge> : null}
                  </p>
                  <p className="mt-0.5 break-all text-xs text-muted">{c.url}</p>
                  {c.samples?.length ? (
                    <ul className="mt-1 space-y-0.5 text-xs" aria-label="Sample headlines">
                      {c.samples.map((t) => (
                        <li key={t} className="line-clamp-1">
                          “{t}”
                        </li>
                      ))}
                    </ul>
                  ) : null}
                  {c.needs_page_metadata ? (
                    <p className="mt-0.5 text-xs text-muted">
                      This sitemap lists addresses only. Using it turns on page metadata lookup, which reads up to 10 new
                      story pages per fetch.
                    </p>
                  ) : null}
                  <p className="mt-0.5 text-xs text-muted">
                    {pluralize(c.entries, "item")}
                    {c.latest ? (
                      <>
                        {" · newest "}
                        <Time value={c.latest} />
                      </>
                    ) : null}
                    {" · "}found via {c.found_by}
                  </p>
                </div>
                <Button size="sm" onClick={() => onUse(c, result)}>
                  Use
                </Button>
              </li>
            ))}
          </ul>
          {failed.length ? (
            <details className="text-xs text-muted">
              <summary className="cursor-pointer">{pluralize(failed.length, "other candidate")} did not work</summary>
              <ul className="mt-1 space-y-1">
                {failed.map((c) => (
                  <li key={c.url} className="flex gap-1.5">
                    <XCircle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-danger" aria-hidden />
                    <span className="min-w-0 break-all">
                      {c.url}: {c.error ?? "not a feed"}
                    </span>
                  </li>
                ))}
              </ul>
            </details>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
