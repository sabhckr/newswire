"use client";

import * as D from "@radix-ui/react-dialog";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy, ExternalLink, FilePlus2, FileText, MapPin, RefreshCw, Rss, X } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ErrorState, SkeletonRows } from "@/components/ui/states";
import { Time } from "@/components/ui/time";
import { Tip } from "@/components/ui/tooltip";
import { api, errorMessage } from "@/lib/api";
import { PERMS, useSession } from "@/lib/session";
import type { NewsGeoOut, NewsItemOut } from "@/lib/types";
import { cn, flag, formatDateTime, hostOf } from "@/lib/utils";

export interface NewsDetail extends NewsItemOut {
  content: string | null;
  guid: string | null;
  extra: Record<string, unknown> | null;
  geographies: NewsGeoOut[];
  seen_in: { source_id: string; name: string; publisher: string; first_seen_at: string | null }[];
  duplicates: NewsItemOut[];
  cluster: { id: string; title: string; item_count: number } | null;
  matches: { id: string; assignment_id: string; headline: string; score: number; explanation: string; review_status: string }[];
}

/** The story's own text, read from its page on request (POST /news/{id}/full-text). */
export interface FullText {
  url: string;
  title: string | null;
  paragraphs: string[];
  author: string | null;
  published_at: string | null;
  lead_image: string | null;
  images: { url: string; caption: string }[];
  videos: string[];
  method: string;
  char_count: number;
  fetched_at: string;
}

const ROLE_LABEL: Record<string, string> = {
  publisher: "Publisher location",
  event: "Event location",
  article: "Article location",
  mentioned: "Mentioned",
};

const METHOD_LABEL: Record<string, string> = {
  source_metadata: "Source settings",
  feed_metadata: "Feed metadata",
  feed_category: "Feed category",
  dateline: "Dateline",
  text_match: "Text match",
  editor: "Set by editor",
};

function Section({ title, icon: Icon, children, count }: { title: string; icon?: React.ComponentType<{ className?: string }>; children: React.ReactNode; count?: number }) {
  return (
    <section className="border-t border-border px-4 py-3">
      <h3 className="mb-2 flex items-center gap-1.5 text-2xs font-semibold uppercase tracking-wide text-muted">
        {Icon ? <Icon className="h-3.5 w-3.5" aria-hidden /> : null}
        {title}
        {count !== undefined ? <span className="tabular font-normal">({count})</span> : null}
      </h3>
      {children}
    </section>
  );
}

function Confidence({ value }: { value: number }) {
  const pct = Math.round(value * 100);
  const tone = value >= 0.8 ? "bg-success" : value >= 0.5 ? "bg-info" : "bg-warning";
  return (
    <span className="inline-flex items-center gap-1.5" title={`Confidence ${pct}%${value < 0.5 ? " — below the threshold used for matching" : ""}`}>
      <span className="h-1.5 w-12 overflow-hidden rounded-full bg-surface-2" aria-hidden>
        <span className={cn("block h-full", tone)} style={{ width: `${pct}%` }} />
      </span>
      <span className="tabular text-xs text-muted">{pct}%</span>
    </span>
  );
}

function FullArticle({ item }: { item: NewsDetail }) {
  const qc = useQueryClient();
  const router = useRouter();
  const { can } = useSession();
  const cached = (item.extra?.full_text as FullText | undefined) ?? null;
  const load = useMutation({
    mutationFn: (refresh: boolean) => api<FullText>(`/news/${item.id}/full-text${refresh ? "?refresh=true" : ""}`, { method: "POST" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["news-item", item.id] }),
  });
  const draft = useMutation({
    mutationFn: () => api<{ id: string; notice?: string }>(`/news/${item.id}/article`, { method: "POST" }),
    onSuccess: (art) => {
      if (art.notice) toast.warning(art.notice);
      else toast.success("Draft article created — edit it before publishing");
      qc.invalidateQueries({ queryKey: ["articles"] });
      router.push(`/articles/${art.id}`);
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const full = load.data ?? cached;
  const copy = async () => {
    if (!full) return;
    const text = [full.title ?? item.title, ...full.paragraphs, `Source: ${item.url}`].join("\n\n");
    try {
      await navigator.clipboard.writeText(text);
      toast.success("Full text copied");
    } catch {
      toast.error("The browser did not allow copying; select the text instead");
    }
  };

  return (
    <Section title="Full article" icon={FileText}>
      <div className="mb-2 flex flex-wrap items-center gap-1.5">
        {!full ? (
          <Button size="sm" variant="outline" loading={load.isPending} onClick={() => load.mutate(false)}>
            <FileText className="h-3.5 w-3.5" /> Load full article
          </Button>
        ) : (
          <>
            <Button size="sm" variant="outline" onClick={copy}>
              <Copy className="h-3.5 w-3.5" /> Copy text
            </Button>
            <Tip content="Read the page again">
              <Button size="icon-sm" variant="ghost" aria-label="Reload full article" loading={load.isPending} onClick={() => load.mutate(true)}>
                <RefreshCw className="h-3.5 w-3.5" />
              </Button>
            </Tip>
          </>
        )}
        {can(PERMS.articlesWrite) ? (
          <Button size="sm" variant="primary" loading={draft.isPending} onClick={() => draft.mutate()}>
            <FilePlus2 className="h-3.5 w-3.5" /> Start article from this
          </Button>
        ) : null}
      </div>
      {load.isError ? <p className="mb-2 rounded border border-warning/40 bg-warning/10 p-2 text-xs">{errorMessage(load.error)}</p> : null}
      {full ? (
        <div lang={item.language ?? undefined}>
          {full.lead_image ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={full.lead_image} alt="" referrerPolicy="no-referrer" loading="lazy" className="mb-3 max-h-64 w-full rounded object-cover" />
          ) : null}
          <div className="space-y-3 text-sm leading-7">
            {full.paragraphs.map((p, i) => (
              <p key={i}>{p}</p>
            ))}
          </div>
          {full.videos.length ? (
            <ul className="mt-3 space-y-1 text-xs">
              {full.videos.map((v) => (
                <li key={v}>
                  <a href={v} target="_blank" rel="noopener noreferrer nofollow" className="text-accent hover:underline">
                    Video in the story: {hostOf(v)}
                  </a>
                </li>
              ))}
            </ul>
          ) : null}
          <p className="mt-3 text-2xs text-muted">
            Read from {hostOf(full.url)} {full.fetched_at ? <>on {formatDateTime(full.fetched_at)}</> : null}
            {full.author ? <> · {full.author}</> : null}. The text belongs to the publisher: rewrite it and credit the source
            before you publish it.
          </p>
        </div>
      ) : !load.isPending && !load.isError ? (
        <p className="text-xs text-muted">Reads the story from the publisher’s page (robots.txt respected; paywalls and logins are never bypassed).</p>
      ) : null}
    </Section>
  );
}

export function NewsDetailBody({ id, onOpenItem }: { id: string; onOpenItem?: (id: string) => void }) {
  const q = useQuery({ queryKey: ["news-item", id], queryFn: () => api<NewsDetail>(`/news/${id}`) });
  if (q.isLoading) return <SkeletonRows rows={8} />;
  if (q.isError || !q.data) return <ErrorState error={q.error} onRetry={() => q.refetch()} />;
  const item = q.data;
  const geos = [...item.geographies].sort((a, b) => b.confidence - a.confidence);
  const categories = Array.isArray(item.extra?.categories) ? (item.extra?.categories as string[]) : [];

  return (
    <div>
      <div className="px-4 pb-3">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted">
          <span className="font-medium text-text/80">{item.publisher ?? hostOf(item.url)}</span>
          {item.source_name && item.source_name !== item.publisher ? <span>· {item.source_name}</span> : null}
          {item.author ? <span>· {item.author}</span> : null}
          {item.language ? <span className="uppercase">· {item.language}</span> : null}
        </div>
        <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-xs">
          <dt className="text-muted">Published</dt>
          <dd>{item.published_at ? formatDateTime(item.published_at) : <span className="text-muted">not given by the feed</span>}</dd>
          <dt className="text-muted">Retrieved</dt>
          <dd>{formatDateTime(item.retrieved_at)}</dd>
          {item.category_text ? (
            <>
              <dt className="text-muted">Feed category</dt>
              <dd>{item.category_text}</dd>
            </>
          ) : null}
        </dl>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          {item.is_duplicate ? (
            <Badge tone="warning" icon={Copy} title={item.duplicate_reason ?? undefined}>
              Duplicate{item.duplicate_reason ? ` (${item.duplicate_reason.replace(/_/g, " ")})` : ""}
            </Badge>
          ) : null}
          <a
            href={item.url}
            target="_blank"
            rel="noopener noreferrer nofollow"
            className="inline-flex items-center gap-1 text-xs text-accent hover:underline"
          >
            Read at {hostOf(item.url)} <ExternalLink className="h-3 w-3" aria-hidden />
          </a>
        </div>
        {item.summary ? <p lang={item.language ?? undefined} className="mt-3 whitespace-pre-line text-sm leading-6">{item.summary}</p> : (
          <p className="mt-3 text-sm text-muted">The feed did not include a summary.</p>
        )}
        {categories.length ? (
          <div className="mt-2 flex flex-wrap gap-1">
            {categories.slice(0, 12).map((c) => (
              <Badge key={c}>{c}</Badge>
            ))}
          </div>
        ) : null}
      </div>

      <FullArticle item={item} />

      <Section title="Geography" icon={MapPin} count={geos.length}>
        {geos.length ? (
          <ul className="space-y-1.5">
            {geos.map((g) => (
              <li key={g.id} className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-sm">
                <span className="min-w-0 flex-1 truncate">
                  {g.country_code ? `${flag(g.country_code)} ` : ""}
                  {g.entity?.name ?? g.city ?? g.region ?? g.original_text ?? "Unresolved"}
                  {g.original_text && g.entity && g.original_text !== g.entity.name ? (
                    <span className="text-xs text-muted"> (“{g.original_text}”)</span>
                  ) : null}
                </span>
                <Badge tone={g.role === "event" ? "accent" : g.role === "publisher" ? "neutral" : "info"}>{ROLE_LABEL[g.role] ?? g.role}</Badge>
                <span className="text-2xs text-muted">{METHOD_LABEL[g.method] ?? g.method}</span>
                <Confidence value={g.confidence} />
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-muted">No places were detected in this report.</p>
        )}
      </Section>

      <Section title="Seen in sources" icon={Rss} count={item.seen_in.length}>
        <ul className="space-y-1 text-sm">
          {item.seen_in.map((s) => (
            <li key={s.source_id} className="flex items-center gap-2">
              <Link href={`/sources/${s.source_id}`} className="min-w-0 flex-1 truncate hover:text-accent">
                {s.name} <span className="text-xs text-muted">· {s.publisher}</span>
              </Link>
              <Time value={s.first_seen_at} className="text-xs text-muted" />
            </li>
          ))}
        </ul>
      </Section>

      {item.duplicates.length ? (
        <Section title={item.is_duplicate ? "Original report" : "Duplicates of this report"} icon={Copy} count={item.duplicates.length}>
          <ul className="space-y-1.5 text-sm">
            {item.duplicates.map((d) => (
              <li key={d.id} className="flex items-center gap-2">
                <button type="button" onClick={() => onOpenItem?.(d.id)} className="min-w-0 flex-1 truncate text-left hover:text-accent">
                  {d.title}
                </button>
                <span className="shrink-0 text-xs text-muted">{d.publisher}</span>
              </li>
            ))}
          </ul>
        </Section>
      ) : null}
    </div>
  );
}

/** Right-hand drawer with everything known about one collected report. */
export function NewsDetailDrawer({ id, onClose, onOpenItem }: { id: string | null; onClose: () => void; onOpenItem?: (id: string) => void }) {
  const qc = useQueryClient();
  const title = id ? qc.getQueryData<NewsDetail>(["news-item", id])?.title : undefined;
  const detail = useQuery({ queryKey: ["news-item", id], queryFn: () => api<NewsDetail>(`/news/${id}`), enabled: !!id });
  return (
    <D.Root open={!!id} onOpenChange={(o) => !o && onClose()}>
      <D.Portal>
        <D.Overlay className="fixed inset-0 z-40 bg-black/40 animate-fade-in" />
        <D.Content className="fixed inset-y-0 right-0 z-50 flex w-full max-w-xl flex-col border-l border-border bg-surface shadow-pop animate-fade-in focus:outline-none">
          <div className="flex items-start gap-3 border-b border-border px-4 py-3">
            <D.Title className="min-w-0 flex-1 text-sm font-semibold leading-snug">{detail.data?.title ?? title ?? "Report"}</D.Title>
            <D.Description className="sr-only">Details of a collected news report</D.Description>
            <Tip content="Close (Esc)">
              <D.Close className="rounded p-1 text-muted hover:bg-surface-2 hover:text-text" aria-label="Close">
                <X className="h-4 w-4" />
              </D.Close>
            </Tip>
          </div>
          <div className="scroll-thin min-h-0 flex-1 overflow-y-auto pt-3">{id ? <NewsDetailBody id={id} onOpenItem={onOpenItem} /> : null}</div>
        </D.Content>
      </D.Portal>
    </D.Root>
  );
}
