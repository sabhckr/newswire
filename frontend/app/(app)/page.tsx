"use client";

import { useQuery } from "@tanstack/react-query";
import {
  AlertTriangle,
  FileEdit,
  FileText,
  Microscope,
  Newspaper,
  Radio,
  Rss,
  Send,
  Target,
  Users,
} from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";

import { VolumeChart } from "@/components/charts";
import { NewsRow } from "@/components/news-row";
import { useOverview } from "@/components/shell/header";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { PageHeader, Stat } from "@/components/ui/page";
import { EmptyState, ErrorState, InlineAlert, SkeletonRows } from "@/components/ui/states";
import { PriorityBadge } from "@/components/ui/status-badge";
import { Time } from "@/components/ui/time";
import { api } from "@/lib/api";
import { PERMS, useSession } from "@/lib/session";
import type { AssignmentOut, NewsItemOut, Page } from "@/lib/types";
import { formatNumber } from "@/lib/utils";

function isoDate(d: Date) {
  return d.toISOString().slice(0, 10);
}

export default function OverviewPage() {
  const router = useRouter();
  const { session, can } = useSession();
  const overview = useOverview();
  const volume = useQuery({
    queryKey: ["analytics", "volume", "48h"],
    queryFn: () =>
      api<{ rows: { bucket: string; items: number; duplicates: number }[] }>("/analytics/reports/volume", {
        query: { start: isoDate(new Date(Date.now() - 86400000)), end: isoDate(new Date()) },
      }),
    refetchInterval: 60_000,
  });
  const assignments = useQuery({
    queryKey: ["assignments", "overview"],
    queryFn: () => api<Page<AssignmentOut>>("/assignments", { query: { status: ["active"], sort: "priority", page_size: 8 } }),
    refetchInterval: 60_000,
  });
  const latest = useQuery({
    queryKey: ["news", "overview"],
    queryFn: () => api<Page<NewsItemOut>>("/news", { query: { page_size: 8, duplicates: "exclude" } }),
    refetchInterval: 30_000,
  });

  const m = overview.data?.metrics;
  const s = overview.data?.status;
  const first = session.user.display_name.split(" ")[0];

  return (
    <div className="mx-auto max-w-7xl">
      <PageHeader
        title={`Good to see you, ${first}`}
        description="Today’s collection, matches and editorial queue. Numbers count from midnight in your timezone."
        actions={
          can(PERMS.assignmentsWrite) ? (
            <Button variant="primary" onClick={() => router.push("/headlines/new")}>
              <Target className="h-4 w-4" /> New headline
            </Button>
          ) : null
        }
      />

      {overview.isError ? <ErrorState error={overview.error} onRetry={() => overview.refetch()} /> : null}

      {s && s.worker.state !== "running" ? (
        <InlineAlert tone="danger" className="mb-4" title={s.worker.state === "stale" ? "The collection worker has stopped reporting" : "The collection worker is not running"}>
          News is not being collected. Start it with <code className="font-mono">python -m app.worker</code> (or the
          <code className="font-mono"> worker</code> container). <Link className="underline" href="/system">System status</Link>
        </InlineAlert>
      ) : null}
      {s && s.failed_sources > 0 ? (
        <InlineAlert tone="warning" className="mb-4" title={`${s.failed_sources} source${s.failed_sources === 1 ? " is" : "s are"} failing`}>
          <Link className="underline" href="/sources?health=failing">
            Review failing sources
          </Link>
        </InlineAlert>
      ) : null}

      <section aria-label="Today" className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-7">
        <Stat label="Collected today" value={formatNumber(m?.articles_collected_today)} icon={Newspaper} href="/live" />
        <Stat label="New matches today" value={formatNumber(m?.new_matches_today)} icon={Target} href="/headlines" />
        <Stat label="Active headlines" value={formatNumber(m?.active_assignments)} icon={Microscope} href="/headlines" />
        <Stat label="Publishers today" value={formatNumber(m?.distinct_publishers_today)} icon={Users} href="/analytics" />
        <Stat
          label="Matches to review"
          value={formatNumber(m?.pending_research_reviews)}
          icon={AlertTriangle}
          tone={m && m.pending_research_reviews > 0 ? "warning" : undefined}
          href="/research"
        />
        <Stat label="Drafts in progress" value={formatNumber(m?.draft_articles)} icon={FileEdit} href="/articles?status=draft&status=in_review" />
        <Stat label="Published" value={formatNumber(m?.published_articles)} icon={Send} href="/articles?status=published" />
      </section>

      <div className="mt-4 grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader
            title="Collection volume"
            description="Items retrieved per hour over the last two days"
            icon={Radio}
            actions={
              s ? (
                <span className="text-xs text-muted">
                  Last run <Time value={s.last_collection_at} /> · next poll <Time value={s.next_poll_at} />
                </span>
              ) : null
            }
          />
          <CardBody>
            {volume.isLoading ? (
              <SkeletonRows rows={5} />
            ) : volume.data?.rows.length ? (
              <VolumeChart
                data={volume.data.rows.map((r) => ({ ...r, unique: r.items - r.duplicates }))}
                x="bucket"
                bars={[
                  { key: "unique", name: "New stories" },
                  { key: "duplicates", name: "Duplicates" },
                ]}
                stacked
              />
            ) : (
              <EmptyState
                icon={Rss}
                title="Nothing collected yet"
                description="Add RSS, Atom or JSON sources and the worker will start collecting on its schedule."
                action={
                  can(PERMS.sourcesWrite) ? (
                    <Button variant="primary" onClick={() => router.push("/sources?new=1")}>
                      Add a source
                    </Button>
                  ) : null
                }
              />
            )}
          </CardBody>
        </Card>

        <Card>
          <CardHeader
            title="Active headlines"
            description="By priority, with matches awaiting review"
            icon={Target}
            actions={
              <Link href="/headlines" className="text-xs text-accent hover:underline">
                All
              </Link>
            }
          />
          {assignments.isLoading ? (
            <SkeletonRows rows={4} />
          ) : assignments.data?.items.length ? (
            <ul>
              {assignments.data.items.map((a) => (
                <li key={a.id} className="border-b border-border/70 last:border-0">
                  <Link href={`/research/${a.id}`} className="flex items-center gap-3 px-4 py-2.5 hover:bg-surface-2/60">
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm font-medium">{a.headline}</p>
                      <p className="text-xs text-muted">
                        {formatNumber(a.match_count)} matches · last <Time value={a.last_matched_at} />
                      </p>
                    </div>
                    {a.unreviewed_count ? (
                      <span className="tabular rounded bg-info/10 px-1.5 text-xs font-medium text-info" title="Unreviewed matches">
                        {a.unreviewed_count}
                      </span>
                    ) : null}
                    <PriorityBadge priority={a.priority} />
                  </Link>
                </li>
              ))}
            </ul>
          ) : (
            <EmptyState icon={Target} title="No active headlines" description="Create a headline with keywords and places to start monitoring." />
          )}
        </Card>
      </div>

      <Card className="mt-4">
        <CardHeader
          title="Latest news"
          description={`Most recent unique stories from ${formatNumber(s?.active_sources)} active sources`}
          icon={FileText}
          actions={
            <Link href="/live" className="text-xs text-accent hover:underline">
              Open live feed
            </Link>
          }
        />
        {latest.isLoading ? (
          <SkeletonRows rows={6} />
        ) : latest.isError ? (
          <ErrorState error={latest.error} onRetry={() => latest.refetch()} />
        ) : latest.data?.items.length ? (
          latest.data.items.map((n) => <NewsRow key={n.id} item={n} onOpen={() => router.push(`/live?item=${n.id}`)} />)
        ) : (
          <EmptyState icon={Newspaper} title="No stories yet" description="Collected stories appear here as soon as the worker fetches your sources." />
        )}
      </Card>
    </div>
  );
}
