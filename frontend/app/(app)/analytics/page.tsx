"use client";

import { useQuery } from "@tanstack/react-query";
import { BarChart3, Download, Globe2, Newspaper, Rss, Send, Target, Users } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { HBarChart, VolumeChart } from "@/components/charts";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Field, Input, Select } from "@/components/ui/input";
import { PageHeader } from "@/components/ui/page";
import { EmptyState, ErrorState, SkeletonRows } from "@/components/ui/states";
import { StatusBadge } from "@/components/ui/status-badge";
import { Table, TD, TH, THead, TR } from "@/components/ui/table";
import { Time } from "@/components/ui/time";
import { api, downloadUrl } from "@/lib/api";
import { browserTimeZone, flag, formatDuration, formatNumber } from "@/lib/utils";

type ReportName = "volume" | "countries" | "publishers" | "categories" | "assignments" | "sources" | "publications" | "throughput";

const iso = (d: Date) => d.toISOString().slice(0, 10);
const daysAgo = (n: number) => iso(new Date(Date.now() - n * 86400000));

const PRESETS = [
  { label: "Last 24 hours", days: 1 },
  { label: "Last 7 days", days: 7 },
  { label: "Last 30 days", days: 30 },
  { label: "Last 90 days", days: 90 },
];

function useReport<T>(name: ReportName, start: string, end: string) {
  return useQuery({
    queryKey: ["analytics", name, start, end],
    queryFn: () => api<{ rows: T[] }>(`/analytics/reports/${name}`, { query: { start, end, tz: browserTimeZone() } }),
  });
}

function CsvLink({ name, start, end }: { name: ReportName; start: string; end: string }) {
  return (
    <a
      href={downloadUrl(`/analytics/reports/${name}`, { start, end, format: "csv", tz: browserTimeZone() })}
      className="inline-flex h-7 items-center gap-1.5 rounded border border-border px-2 text-xs text-muted hover:bg-surface-2 hover:text-text"
      download
    >
      <Download className="h-3.5 w-3.5" /> CSV
    </a>
  );
}

function ReportCard<T>({
  name,
  title,
  description,
  icon,
  start,
  end,
  render,
  className,
  emptyText,
}: {
  name: ReportName;
  title: string;
  description?: string;
  icon: React.ComponentType<{ className?: string }>;
  start: string;
  end: string;
  render: (rows: T[]) => React.ReactNode;
  className?: string;
  emptyText: string;
}) {
  const q = useReport<T>(name, start, end);
  return (
    <Card className={className}>
      <CardHeader title={title} description={description} icon={icon} actions={<CsvLink name={name} start={start} end={end} />} />
      {q.isLoading ? (
        <SkeletonRows rows={4} />
      ) : q.isError ? (
        <ErrorState error={q.error} onRetry={() => q.refetch()} />
      ) : q.data?.rows.length ? (
        render(q.data.rows)
      ) : (
        <EmptyState title="No data for this period" description={emptyText} />
      )}
    </Card>
  );
}

type Assignment = { assignment_id: string; headline: string; status: string; matches: number; verified: number; rejected: number; precision: number | null; avg_score: number | null; last_match: string | null };
type SourceRow = { source_id: string; name: string; publisher: string; is_active: boolean; consecutive_failures: number; fetches: number; successes: number; failures: number; success_rate: number | null; avg_duration_ms: number | null; new_items: number };

export default function AnalyticsPage() {
  const [start, setStart] = useState(daysAgo(7));
  const [end, setEnd] = useState(iso(new Date()));
  const preset = PRESETS.find((p) => start === daysAgo(p.days) && end === iso(new Date()));

  return (
    <div className="mx-auto max-w-7xl">
      <PageHeader
        title="Analytics"
        description="Every figure is counted from the database for the selected period, in days of your own time zone. Export any report as CSV."
      />
      <div className="mb-4 flex flex-wrap items-end gap-2" role="group" aria-label="Reporting period">
        <Field label="Period" htmlFor="preset">
          <Select
            id="preset"
            value={preset ? String(preset.days) : "custom"}
            onChange={(e) => {
              if (e.target.value === "custom") return;
              setStart(daysAgo(Number(e.target.value)));
              setEnd(iso(new Date()));
            }}
            className="w-40"
          >
            {PRESETS.map((p) => (
              <option key={p.days} value={p.days}>
                {p.label}
              </option>
            ))}
            <option value="custom">Custom range</option>
          </Select>
        </Field>
        <Field label="From" htmlFor="start">
          <Input id="start" type="date" value={start} max={end} onChange={(e) => e.target.value && setStart(e.target.value)} className="w-36" />
        </Field>
        <Field label="To" htmlFor="end">
          <Input id="end" type="date" value={end} min={start} onChange={(e) => e.target.value && setEnd(e.target.value)} className="w-36" />
        </Field>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <ReportCard<{ bucket: string; items: number; duplicates: number }>
          name="volume"
          title="News volume"
          description="Items retrieved, with duplicates flagged by the dedup pipeline"
          icon={Newspaper}
          start={start}
          end={end}
          className="lg:col-span-2"
          emptyText="No items were retrieved in this period."
          render={(rows) => (
            <CardBody>
              <VolumeChart
                data={rows.map((r) => ({ ...r, unique: r.items - r.duplicates }))}
                x="bucket"
                bars={[
                  { key: "unique", name: "Unique" },
                  { key: "duplicates", name: "Duplicates" },
                ]}
                stacked
                kind="bar"
              />
            </CardBody>
          )}
        />

        <ReportCard<{ country_code: string; items: number }>
          name="countries"
          title="Coverage by country"
          description="Stories with a confident event, article or mentioned location (publisher location excluded)"
          icon={Globe2}
          start={start}
          end={end}
          emptyText="No stories had a confident country in this period."
          render={(rows) => (
            <CardBody>
              <HBarChart data={rows.slice(0, 15).map((r) => ({ ...r, label: `${flag(r.country_code)} ${r.country_code}` }))} label="label" value="items" name="Stories" />
            </CardBody>
          )}
        />

        <ReportCard<{ publisher: string; items: number; duplicates: number; latest: string | null }>
          name="publishers"
          title="Publishers"
          description="Items per publisher and how many were duplicates"
          icon={Users}
          start={start}
          end={end}
          emptyText="No publisher activity in this period."
          render={(rows) => (
            <Table>
              <THead>
                <tr>
                  <TH>Publisher</TH>
                  <TH className="text-right">Items</TH>
                  <TH className="text-right">Duplicates</TH>
                  <TH>Latest</TH>
                </tr>
              </THead>
              <tbody>
                {rows.slice(0, 20).map((r) => (
                  <TR key={r.publisher}>
                    <TD className="max-w-[220px] truncate font-medium">{r.publisher}</TD>
                    <TD className="tabular text-right">{formatNumber(r.items)}</TD>
                    <TD className="tabular text-right text-muted">{formatNumber(r.duplicates)}</TD>
                    <TD className="text-muted">
                      <Time value={r.latest} />
                    </TD>
                  </TR>
                ))}
              </tbody>
            </Table>
          )}
        />

        <ReportCard<{ category: string; items: number }>
          name="categories"
          title="Categories"
          description="Items by the category of their source"
          icon={BarChart3}
          start={start}
          end={end}
          emptyText="No categorised items in this period."
          render={(rows) => (
            <CardBody>
              <HBarChart data={rows} label="category" value="items" name="Items" />
            </CardBody>
          )}
        />

        <ReportCard<{ bucket: string; articles_created: number; articles_published: number; matches_reviewed: number }>
          name="throughput"
          title="Editorial throughput"
          description="Articles created and published, matches reviewed"
          icon={Target}
          start={start}
          end={end}
          emptyText="No editorial activity in this period."
          render={(rows) => (
            <CardBody>
              <VolumeChart
                data={rows}
                x="bucket"
                kind="bar"
                bars={[
                  { key: "matches_reviewed", name: "Matches reviewed" },
                  { key: "articles_created", name: "Articles created" },
                  { key: "articles_published", name: "Articles published" },
                ]}
              />
            </CardBody>
          )}
        />

        <ReportCard<Assignment>
          name="assignments"
          title="Headline performance"
          description="Precision = verified ÷ (verified + rejected) among reviewed matches"
          icon={Target}
          start={start}
          end={end}
          className="lg:col-span-2"
          emptyText="No active headlines."
          render={(rows) => (
            <Table>
              <THead>
                <tr>
                  <TH>Headline</TH>
                  <TH>Status</TH>
                  <TH className="text-right">Matches</TH>
                  <TH className="text-right">Verified</TH>
                  <TH className="text-right">Rejected</TH>
                  <TH className="text-right">Precision</TH>
                  <TH className="text-right">Avg score</TH>
                  <TH>Last match</TH>
                </tr>
              </THead>
              <tbody>
                {rows.map((r) => (
                  <TR key={r.assignment_id}>
                    <TD className="max-w-[320px] truncate">
                      <Link className="font-medium hover:text-accent" href={`/research/${r.assignment_id}`}>
                        {r.headline}
                      </Link>
                    </TD>
                    <TD>
                      <StatusBadge status={r.status} />
                    </TD>
                    <TD className="tabular text-right">{formatNumber(r.matches)}</TD>
                    <TD className="tabular text-right">{formatNumber(r.verified)}</TD>
                    <TD className="tabular text-right">{formatNumber(r.rejected)}</TD>
                    <TD className="tabular text-right">{r.precision === null ? "—" : `${Math.round(r.precision * 100)}%`}</TD>
                    <TD className="tabular text-right">{r.avg_score ?? "—"}</TD>
                    <TD className="text-muted">
                      <Time value={r.last_match} />
                    </TD>
                  </TR>
                ))}
              </tbody>
            </Table>
          )}
        />

        <ReportCard<SourceRow>
          name="sources"
          title="Source reliability"
          description="Fetch success rate counts success and not-modified responses; failures include robots.txt blocks"
          icon={Rss}
          start={start}
          end={end}
          className="lg:col-span-2"
          emptyText="No sources yet."
          render={(rows) => (
            <Table>
              <THead>
                <tr>
                  <TH>Source</TH>
                  <TH className="text-right">Fetches</TH>
                  <TH className="text-right">Success rate</TH>
                  <TH className="text-right">Failures</TH>
                  <TH className="text-right">Avg time</TH>
                  <TH className="text-right">New items</TH>
                  <TH>State</TH>
                </tr>
              </THead>
              <tbody>
                {rows.map((r) => (
                  <TR key={r.source_id}>
                    <TD className="max-w-[260px]">
                      <Link href={`/sources/${r.source_id}`} className="block truncate font-medium hover:text-accent">
                        {r.name}
                      </Link>
                      <span className="block truncate text-xs text-muted">{r.publisher}</span>
                    </TD>
                    <TD className="tabular text-right">{formatNumber(r.fetches)}</TD>
                    <TD className="tabular text-right">{r.success_rate === null ? "—" : `${Math.round(r.success_rate * 100)}%`}</TD>
                    <TD className="tabular text-right">{formatNumber(r.failures)}</TD>
                    <TD className="tabular text-right">{formatDuration(r.avg_duration_ms)}</TD>
                    <TD className="tabular text-right">{formatNumber(r.new_items)}</TD>
                    <TD>
                      <StatusBadge status={!r.is_active ? "disabled" : r.consecutive_failures ? "failing" : "healthy"} />
                    </TD>
                  </TR>
                ))}
              </tbody>
            </Table>
          )}
        />

        <ReportCard<{ bucket: string; succeeded: number; failed: number }>
          name="publications"
          title="Publishing activity"
          description="Publishing jobs that succeeded or failed"
          icon={Send}
          start={start}
          end={end}
          className="lg:col-span-2"
          emptyText="Nothing was published in this period."
          render={(rows) => (
            <CardBody>
              <VolumeChart
                data={rows}
                x="bucket"
                kind="bar"
                bars={[
                  { key: "succeeded", name: "Succeeded" },
                  { key: "failed", name: "Failed" },
                ]}
              />
            </CardBody>
          )}
        />
      </div>
    </div>
  );
}
