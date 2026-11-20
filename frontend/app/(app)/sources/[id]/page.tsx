"use client";

import { keepPreviousData, useMutation, useQuery } from "@tanstack/react-query";
import {
  Activity,
  ArrowLeft,
  ExternalLink,
  FlaskConical,
  ListOrdered,
  Newspaper,
  Pause,
  Pencil,
  Play,
  RefreshCw,
  Settings2,
  Trash2,
} from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";

import { NewsRow } from "@/components/news-row";
import { useSourceActions } from "@/components/sources/actions";
import { ProbeResultPanel, type ProbeResult } from "@/components/sources/probe-result";
import { SourceDialog } from "@/components/sources/source-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { useConfirm } from "@/components/ui/confirm";
import { Select } from "@/components/ui/input";
import { PageHeader, Pagination, Stat } from "@/components/ui/page";
import { EmptyState, ErrorState, InlineAlert, Skeleton, SkeletonRows } from "@/components/ui/states";
import { StatusBadge } from "@/components/ui/status-badge";
import { Table, TD, TH, THead, TR } from "@/components/ui/table";
import { Time } from "@/components/ui/time";
import { Tip } from "@/components/ui/tooltip";
import { api, errorMessage } from "@/lib/api";
import { PERMS, useSession } from "@/lib/session";
import type { FetchLogOut, NewsItemOut, Page, SourceOut } from "@/lib/types";
import { flag, formatBytes, formatDuration, formatInterval, formatNumber } from "@/lib/utils";

type Window = {
  fetches: number;
  successes: number;
  not_modified: number;
  success_rate: number | null;
  avg_duration_ms: number | null;
  new_items: number;
};
type Health = { source_id: string; health: string; windows: Record<"24h" | "7d" | "30d", Window>; items_total: number; latest_item_at: string | null };

const LOG_PAGE = 25;

function Detail({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-0.5 sm:flex-row sm:gap-3">
      <dt className="w-40 shrink-0 text-xs text-muted">{label}</dt>
      <dd className="min-w-0 break-words text-sm">{children ?? "—"}</dd>
    </div>
  );
}

export default function SourceDetailPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const confirm = useConfirm();
  const { can } = useSession();
  const canWrite = can(PERMS.sourcesWrite);
  const { refresh, setActive, remove } = useSourceActions();
  const [editOpen, setEditOpen] = useState(false);
  const [probe, setProbe] = useState<ProbeResult | null>(null);
  const [logStatus, setLogStatus] = useState("");
  const [logPage, setLogPage] = useState(1);

  const source = useQuery({ queryKey: ["source", id], queryFn: () => api<SourceOut>(`/sources/${id}`), refetchInterval: 30_000 });
  const health = useQuery({ queryKey: ["source-health", id], queryFn: () => api<Health>(`/sources/${id}/health`), refetchInterval: 60_000 });
  const logs = useQuery({
    queryKey: ["source-logs", id, logStatus, logPage],
    queryFn: () => api<Page<FetchLogOut>>(`/sources/${id}/logs`, { query: { status: logStatus, page: logPage, page_size: LOG_PAGE } }),
    placeholderData: keepPreviousData,
    refetchInterval: 30_000,
  });
  const recent = useQuery({
    queryKey: ["news", "source", id],
    queryFn: () => api<Page<NewsItemOut>>("/news", { query: { source_id: [id], page_size: 8 } }),
  });

  const test = useMutation({
    mutationFn: () => api<ProbeResult>(`/sources/${id}/test`, { method: "POST" }),
    onMutate: () => setProbe(null),
    onSuccess: setProbe,
    onError: (e) => toast.error(errorMessage(e)),
  });

  if (source.isLoading) {
    return (
      <div className="mx-auto max-w-6xl space-y-3">
        <Skeleton className="h-8 w-72" />
        <SkeletonRows rows={6} />
      </div>
    );
  }
  if (source.isError || !source.data) {
    return (
      <div className="mx-auto max-w-6xl">
        <ErrorState error={source.error} onRetry={() => source.refetch()} />
      </div>
    );
  }
  const s = source.data;
  const w = health.data?.windows;

  const del = async () => {
    const ok = await confirm({
      title: `Delete ${s.name}?`,
      body: "Collection stops and the source is removed from the list. Stories already collected are kept.",
      confirmLabel: "Delete source",
      destructive: true,
    });
    if (ok) remove.mutate(s.id, { onSuccess: () => router.push("/sources") });
  };

  return (
    <div className="mx-auto max-w-6xl">
      <Link href="/sources" className="mb-2 inline-flex items-center gap-1 text-xs text-muted hover:text-text">
        <ArrowLeft className="h-3.5 w-3.5" /> Sources
      </Link>
      <PageHeader
        title={s.name}
        description={
          <span className="flex flex-wrap items-center gap-2">
            <span>{s.publisher}</span>
            <StatusBadge status={s.health} />
            <Badge>{s.source_type.toUpperCase()}</Badge>
            <a href={s.feed_url} target="_blank" rel="noopener noreferrer nofollow" className="inline-flex max-w-full items-center gap-1 truncate text-accent hover:underline">
              <span className="truncate">{s.feed_url}</span> <ExternalLink className="h-3 w-3 shrink-0" />
            </a>
          </span>
        }
        actions={
          canWrite ? (
            <>
              <Button variant="outline" onClick={() => test.mutate()} loading={test.isPending}>
                <FlaskConical className="h-4 w-4" /> Test
              </Button>
              <Button variant="outline" onClick={() => refresh.mutate(s.id)} loading={refresh.isPending}>
                <RefreshCw className="h-4 w-4" /> Fetch now
              </Button>
              <Button variant="outline" onClick={() => setActive.mutate({ id: s.id, active: !s.is_active })} loading={setActive.isPending}>
                {s.is_active ? <Pause className="h-4 w-4" /> : <Play className="h-4 w-4" />}
                {s.is_active ? "Disable" : "Enable"}
              </Button>
              <Button variant="primary" onClick={() => setEditOpen(true)}>
                <Pencil className="h-4 w-4" /> Edit
              </Button>
              <Button variant="danger-outline" size="icon" onClick={del} aria-label="Delete source">
                <Trash2 className="h-4 w-4" />
              </Button>
            </>
          ) : null
        }
      />

      {s.last_error && s.consecutive_failures > 0 ? (
        <InlineAlert tone="danger" className="mb-4" title={`Last ${s.consecutive_failures} fetch${s.consecutive_failures === 1 ? "" : "es"} failed`}>
          {s.last_error}
          {s.last_error_at ? (
            <span className="text-muted">
              {" "}
              · <Time value={s.last_error_at} />
            </span>
          ) : null}
          . Samyabadi backs off exponentially between retries; the next attempt is <Time value={s.next_fetch_at} />.
        </InlineAlert>
      ) : null}
      {!s.is_active ? (
        <InlineAlert tone="info" className="mb-4" title="This source is disabled">
          It is not collected on schedule. Enable it to resume collection.
        </InlineAlert>
      ) : null}
      {probe ? (
        <div className="mb-4">
          <ProbeResultPanel result={probe} />
        </div>
      ) : null}

      <section aria-label="Collection health" className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat label="Stories collected" value={formatNumber(health.data?.items_total ?? s.total_items)} icon={Newspaper} hint={<>Latest <Time value={health.data?.latest_item_at} /></>} />
        {(["24h", "7d", "30d"] as const).map((k) => {
          const x = w?.[k];
          const rate = x?.success_rate;
          return (
            <Stat
              key={k}
              label={`Success rate · ${k}`}
              value={rate === null || rate === undefined ? "—" : `${Math.round(rate * 100)}%`}
              tone={rate === null || rate === undefined ? undefined : rate >= 0.95 ? "success" : rate >= 0.7 ? "warning" : "danger"}
              icon={Activity}
              hint={x ? `${formatNumber(x.fetches)} fetches · ${formatNumber(x.new_items)} new · avg ${formatDuration(x.avg_duration_ms)}` : "Loading…"}
            />
          );
        })}
      </section>

      <div className="mt-4 grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-1">
          <CardHeader title="Configuration" icon={Settings2} />
          <CardBody>
            <dl className="flex flex-col gap-2">
              <Detail label="Polling">{formatInterval(s.polling_interval_seconds)} · priority {s.priority}</Detail>
              <Detail label="Last fetched">
                <Time value={s.last_fetched_at} />
                {s.last_http_status ? <span className="text-muted"> · HTTP {s.last_http_status}</span> : null}
              </Detail>
              <Detail label="Last success">
                <Time value={s.last_success_at} />
              </Detail>
              <Detail label="Next fetch">{s.is_active ? <Time value={s.next_fetch_at} /> : "Disabled"}</Detail>
              <Detail label="Publisher location">
                {s.country_code || s.city || s.region
                  ? [s.city, s.region, s.country_code ? `${flag(s.country_code)} ${s.country_code}` : null].filter(Boolean).join(", ")
                  : "—"}
              </Detail>
              <Detail label="Language">{s.language ?? "From feed"}</Detail>
              <Detail label="Categories">{s.categories.length ? s.categories.map((c) => c.name).join(", ") : "—"}</Detail>
              <Detail label="Website">
                {s.website_url ? (
                  <a href={s.website_url} target="_blank" rel="noopener noreferrer nofollow" className="text-accent hover:underline">
                    {s.website_url}
                  </a>
                ) : (
                  "—"
                )}
              </Detail>
              <Detail label="robots.txt">{s.respect_robots ? "Respected" : <span className="text-warning">Not checked</span>}</Detail>
              <Detail label="Page metadata">{s.extract_page_metadata ? "Fetched for new items" : "Off"}</Detail>
              <Detail label="Conditional GET">
                {s.etag || s.last_modified ? (
                  <Tip content={[s.etag && `ETag ${s.etag}`, s.last_modified && `Last-Modified ${s.last_modified}`].filter(Boolean).join(" · ")}>
                    <span className="cursor-help underline decoration-dotted">Validators stored</span>
                  </Tip>
                ) : (
                  "No validators"
                )}
              </Detail>
              {s.api_config ? (
                <Detail label="JSON mapping">
                  <code className="block whitespace-pre-wrap break-all rounded bg-surface-2 p-1.5 font-mono text-2xs">{JSON.stringify(s.api_config, null, 2)}</code>
                </Detail>
              ) : null}
              {s.reliability_notes ? <Detail label="Reliability notes">{s.reliability_notes}</Detail> : null}
              {s.terms_notes ? <Detail label="Terms of use">{s.terms_notes}</Detail> : null}
              <Detail label="Added">
                <Time value={s.created_at} absolute />
              </Detail>
            </dl>
          </CardBody>
        </Card>

        <Card className="lg:col-span-2">
          <CardHeader
            title="Fetch log"
            description="Every scheduled, manual and test fetch"
            icon={ListOrdered}
            actions={
              <Select
                value={logStatus}
                onChange={(e) => {
                  setLogStatus(e.target.value);
                  setLogPage(1);
                }}
                aria-label="Filter fetch log by status"
                className="w-auto"
              >
                <option value="">All results</option>
                <option value="success">Success</option>
                <option value="not_modified">Not modified</option>
                <option value="error">Error</option>
                <option value="blocked">Blocked by robots.txt</option>
              </Select>
            }
          />
          {logs.isLoading ? (
            <SkeletonRows rows={6} />
          ) : logs.isError ? (
            <ErrorState error={logs.error} onRetry={() => logs.refetch()} />
          ) : !logs.data?.items.length ? (
            <EmptyState
              icon={ListOrdered}
              title={logStatus ? "No fetches with this result" : "Not fetched yet"}
              description={logStatus ? undefined : "The worker fetches new sources on its next cycle, or use Fetch now."}
            />
          ) : (
            <>
              <Table>
                <THead>
                  <tr>
                    <TH>When</TH>
                    <TH>Result</TH>
                    <TH className="hidden sm:table-cell">HTTP</TH>
                    <TH className="hidden sm:table-cell">Duration</TH>
                    <TH className="text-right">New</TH>
                    <TH className="hidden text-right md:table-cell">Dup.</TH>
                    <TH className="hidden text-right md:table-cell">Skipped</TH>
                    <TH className="hidden text-right lg:table-cell">Size</TH>
                  </tr>
                </THead>
                <tbody>
                  {logs.data.items.map((l) => (
                    <TR key={l.id}>
                      <TD className="whitespace-nowrap text-xs">
                        <Time value={l.started_at} />
                        <span className="ml-1 text-muted">{l.trigger}</span>
                      </TD>
                      <TD className="max-w-[260px]">
                        <StatusBadge status={l.status} />
                        {l.error_message ? (
                          <p className="mt-0.5 line-clamp-2 text-2xs text-danger" title={l.error_message}>
                            {l.error_message}
                          </p>
                        ) : null}
                      </TD>
                      <TD className="tabular hidden text-xs sm:table-cell">{l.http_status ?? "—"}</TD>
                      <TD className="tabular hidden text-xs sm:table-cell">{formatDuration(l.duration_ms)}</TD>
                      <TD className="tabular text-right">{formatNumber(l.items_new)}</TD>
                      <TD className="tabular hidden text-right text-muted md:table-cell">{formatNumber(l.items_duplicate)}</TD>
                      <TD className="tabular hidden text-right text-muted md:table-cell">{formatNumber(l.items_skipped)}</TD>
                      <TD className="tabular hidden text-right text-xs text-muted lg:table-cell">{formatBytes(l.bytes_received)}</TD>
                    </TR>
                  ))}
                </tbody>
              </Table>
              <Pagination page={logPage} pageSize={LOG_PAGE} total={logs.data.total} onPage={setLogPage} />
            </>
          )}
        </Card>
      </div>

      <Card className="mt-4">
        <CardHeader
          title="Latest stories from this source"
          icon={Newspaper}
          actions={
            <Link href={`/live?source_id=${s.id}`} className="text-xs text-accent hover:underline">
              Open in live feed
            </Link>
          }
        />
        {recent.isLoading ? (
          <SkeletonRows rows={4} />
        ) : recent.isError ? (
          <ErrorState error={recent.error} onRetry={() => recent.refetch()} />
        ) : recent.data?.items.length ? (
          recent.data.items.map((n) => <NewsRow key={n.id} item={n} onOpen={() => router.push(`/live?item=${n.id}`)} />)
        ) : (
          <EmptyState icon={Newspaper} title="No stories collected yet" />
        )}
      </Card>

      <SourceDialog open={editOpen} onOpenChange={setEditOpen} source={s} />
    </div>
  );
}
