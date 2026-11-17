"use client";

import * as D from "@radix-ui/react-dialog";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Archive, CheckCircle2, CircleDot, Copy, ExternalLink, Layers, MapPin, Rss, Target, X } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { toast } from "sonner";

import { PlacePicker } from "@/components/assignments/place-picker";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Field, Select } from "@/components/ui/input";
import { ErrorState, SkeletonRows } from "@/components/ui/states";
import { StatusBadge } from "@/components/ui/status-badge";
import { Time } from "@/components/ui/time";
import { Tip } from "@/components/ui/tooltip";
import { api, errorMessage } from "@/lib/api";
import { PERMS, useSession } from "@/lib/session";
import type { GeoEntityOut, NewsGeoOut, NewsItemOut } from "@/lib/types";
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

function LocationCorrection({ item }: { item: NewsDetail }) {
  const qc = useQueryClient();
  const [role, setRole] = useState<"event" | "article">("event");
  const set = useMutation({
    mutationFn: (place: GeoEntityOut) => api(`/news/${item.id}/geographies`, { body: { entity_id: place.id, role } }),
    onSuccess: (_d, place) => {
      toast.success(`${role === "event" ? "Event" : "Article"} location set to ${place.name}`);
      qc.invalidateQueries({ queryKey: ["news-item", item.id] });
      qc.invalidateQueries({ queryKey: ["news"] });
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  return (
    <div className="mt-3 rounded border border-dashed border-border p-2.5">
      <p className="mb-2 text-xs text-muted">Correct the location: an editor’s choice is stored with full confidence and replaces any earlier editor choice for that role.</p>
      <div className="flex flex-col gap-2 sm:flex-row">
        <Field label="Role" htmlFor="geo-role" className="sm:w-36">
          <Select id="geo-role" value={role} onChange={(e) => setRole(e.target.value as "event" | "article")}>
            <option value="event">Event location</option>
            <option value="article">Article location</option>
          </Select>
        </Field>
        <Field label="Place" htmlFor="geo-place" className="flex-1">
          <PlacePicker id="geo-place" onSelect={(p) => set.mutate(p)} disabled={set.isPending} placeholder="Search a city, region or country…" />
        </Field>
      </div>
    </div>
  );
}

function ReviewButtons({ item }: { item: NewsDetail }) {
  const qc = useQueryClient();
  const review = useMutation({
    mutationFn: (status: "unreviewed" | "verified" | "archived") => api(`/news/${item.id}`, { method: "PATCH", body: { review_status: status } }),
    onSuccess: (_d, status) => {
      toast.success(`Marked ${status}`);
      qc.invalidateQueries({ queryKey: ["news-item", item.id] });
      qc.invalidateQueries({ queryKey: ["news"] });
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const options = [
    { v: "unreviewed" as const, label: "Unreviewed", icon: CircleDot },
    { v: "verified" as const, label: "Verified", icon: CheckCircle2 },
    { v: "archived" as const, label: "Archived", icon: Archive },
  ];
  return (
    <div className="flex flex-wrap gap-1.5" role="group" aria-label="Review status">
      {options.map((o) => (
        <Button
          key={o.v}
          size="sm"
          variant={item.review_status === o.v ? "primary" : "outline"}
          aria-pressed={item.review_status === o.v}
          disabled={review.isPending}
          onClick={() => review.mutate(o.v)}
        >
          <o.icon className="h-3.5 w-3.5" /> {o.label}
        </Button>
      ))}
    </div>
  );
}

export function NewsDetailBody({ id, onOpenItem }: { id: string; onOpenItem?: (id: string) => void }) {
  const { can } = useSession();
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
          <StatusBadge status={item.review_status} />
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
        {can(PERMS.researchWrite) ? (
          <div className="mt-3">
            <ReviewButtons item={item} />
          </div>
        ) : null}
      </div>

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
        <p className="mt-2 text-2xs text-muted">Publisher location comes from the source settings and is not used for matching unless enabled in Settings.</p>
        {can(PERMS.researchWrite) ? <LocationCorrection item={item} /> : null}
      </Section>

      <Section title="Matched headlines" icon={Target} count={item.matches.length}>
        {item.matches.length ? (
          <ul className="space-y-2">
            {item.matches.map((m) => (
              <li key={m.id} className="rounded border border-border p-2">
                <div className="flex items-center gap-2">
                  <Link href={`/research/${m.assignment_id}`} className="min-w-0 flex-1 truncate text-sm font-medium hover:text-accent">
                    {m.headline}
                  </Link>
                  <span className="tabular rounded bg-accent/10 px-1.5 text-xs font-semibold text-accent" title="Match score">
                    {Math.round(m.score)}
                  </span>
                  <StatusBadge status={m.review_status} />
                </div>
                <p className="mt-1 text-xs text-muted">{m.explanation}</p>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-muted">Not matched by any headline.</p>
        )}
      </Section>

      {item.cluster ? (
        <Section title="Story cluster" icon={Layers}>
          <Link href={`/clusters?id=${item.cluster.id}`} className="text-sm hover:text-accent">
            {item.cluster.title}
          </Link>
          <span className="ml-2 text-xs text-muted">{item.cluster.item_count} reports</span>
        </Section>
      ) : null}

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
