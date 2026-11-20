"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FileEdit, FilePlus2, FileText, Newspaper, Send, Users } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { toast } from "sonner";

import { NewsRow } from "@/components/news-row";
import { useOverview } from "@/components/shell/header";
import { Button } from "@/components/ui/button";
import { Card, CardHeader } from "@/components/ui/card";
import { PageHeader, Stat } from "@/components/ui/page";
import { EmptyState, ErrorState, InlineAlert, SkeletonRows } from "@/components/ui/states";
import { Time } from "@/components/ui/time";
import { api, errorMessage } from "@/lib/api";
import { PERMS, useSession } from "@/lib/session";
import type { ArticleOut, NewsItemOut, Page } from "@/lib/types";
import { formatNumber } from "@/lib/utils";

export default function OverviewPage() {
  const router = useRouter();
  const qc = useQueryClient();
  const { session, can } = useSession();
  const overview = useOverview();
  const latest = useQuery({
    queryKey: ["news", "overview"],
    queryFn: () => api<Page<NewsItemOut>>("/news", { query: { page_size: 12, duplicates: "exclude" } }),
    refetchInterval: 30_000,
  });
  const newArticle = useMutation({
    mutationFn: () => api<ArticleOut>("/articles", { body: { headline: "Untitled article", body_html: "" } }),
    onSuccess: (a) => {
      qc.invalidateQueries({ queryKey: ["articles"] });
      router.push(`/articles/${a.id}`);
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  const m = overview.data?.metrics;
  const s = overview.data?.status;
  const first = session.user.display_name.split(" ")[0];

  return (
    <div className="mx-auto max-w-5xl">
      <PageHeader
        title={`Good to see you, ${first}`}
        description="Today’s news and your articles. Open a story to start writing from it."
        actions={
          can(PERMS.articlesWrite) ? (
            <Button variant="primary" loading={newArticle.isPending} onClick={() => newArticle.mutate()}>
              <FilePlus2 className="h-4 w-4" /> New article
            </Button>
          ) : null
        }
      />

      {overview.isError ? <ErrorState error={overview.error} onRetry={() => overview.refetch()} /> : null}

      {s && s.worker.state !== "running" ? (
        <InlineAlert tone="danger" className="mb-4" title={s.worker.state === "stale" ? "News collection has stopped reporting" : "News collection is not running"}>
          No new stories are arriving. <Link className="underline" href="/system">Open system status</Link> to see why.
        </InlineAlert>
      ) : null}
      {s && s.failed_sources > 0 ? (
        <InlineAlert tone="warning" className="mb-4" title={`${s.failed_sources} source${s.failed_sources === 1 ? " is" : "s are"} failing`}>
          <Link className="underline" href="/sources?health=failing">
            Review failing sources
          </Link>
        </InlineAlert>
      ) : null}

      <section aria-label="Today" className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat label="Collected today" value={formatNumber(m?.articles_collected_today)} icon={Newspaper} href="/live" />
        <Stat label="Publishers today" value={formatNumber(m?.distinct_publishers_today)} icon={Users} href="/sources" />
        <Stat label="Drafts in progress" value={formatNumber(m?.draft_articles)} icon={FileEdit} href="/articles?status=draft&status=in_review" />
        <Stat label="Published" value={formatNumber(m?.published_articles)} icon={Send} href="/articles?status=published" />
      </section>

      <Card className="mt-4">
        <CardHeader
          title="Latest news"
          description={
            s ? (
              <>
                From {formatNumber(s.active_sources)} sources · last collected <Time value={s.last_collection_at} />
              </>
            ) : (
              "The most recent stories, without duplicates"
            )
          }
          icon={FileText}
          actions={
            <Link href="/live" className="text-xs text-accent hover:underline">
              Open Live News
            </Link>
          }
        />
        {latest.isLoading ? (
          <SkeletonRows rows={8} />
        ) : latest.isError ? (
          <ErrorState error={latest.error} onRetry={() => latest.refetch()} />
        ) : latest.data?.items.length ? (
          latest.data.items.map((n) => <NewsRow key={n.id} item={n} onOpen={() => router.push(`/live?item=${n.id}`)} />)
        ) : (
          <EmptyState
            icon={Newspaper}
            title="No stories yet"
            description="Add the news sites you want to follow and their stories appear here."
            action={
              can(PERMS.sourcesWrite) ? (
                <Button variant="primary" onClick={() => router.push("/sources?new=1")}>
                  Add a source
                </Button>
              ) : null
            }
          />
        )}
      </Card>
    </div>
  );
}
