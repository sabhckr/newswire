"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { Download, ScrollText } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";

import { Card } from "@/components/ui/card";
import { Input, Select } from "@/components/ui/input";
import { PageHeader, Pagination, Toolbar } from "@/components/ui/page";
import { EmptyState, ErrorState, SkeletonRows } from "@/components/ui/states";
import { StatusBadge } from "@/components/ui/status-badge";
import { Table, TD, TH, THead, TR } from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Time } from "@/components/ui/time";
import { api, downloadUrl } from "@/lib/api";
import type { AuditOut, FetchRow, Page } from "@/lib/types";
import { formatBytes, formatDuration } from "@/lib/utils";

const ENTITY_LINKS: Record<string, (id: string) => string> = {
  article: (id) => `/articles/${id}`,
  assignment: (id) => `/research/${id}`,
  source: (id) => `/sources/${id}`,
  cluster: (id) => `/clusters?id=${id}`,
  image: () => `/images`,
  destination: () => `/publishing`,
  backup: () => `/system`,
};

function useDebounced<T>(v: T, ms = 300) {
  const [d, setD] = useState(v);
  useEffect(() => {
    const t = setTimeout(() => setD(v), ms);
    return () => clearTimeout(t);
  }, [v, ms]);
  return d;
}

function AuditTab() {
  const [q, setQ] = useState("");
  const [entity, setEntity] = useState("");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [page, setPage] = useState(1);
  const term = useDebounced(q);
  const query = { q: term, entity_type: entity, start, end };
  const res = useQuery({
    queryKey: ["audit", query, page],
    queryFn: () => api<Page<AuditOut>>("/activity/audit", { query: { ...query, page, page_size: 50 } }),
    placeholderData: keepPreviousData,
  });
  useEffect(() => setPage(1), [term, entity, start, end]);
  return (
    <Card>
      <Toolbar>
        <Input placeholder="Search summary or person…" value={q} onChange={(e) => setQ(e.target.value)} className="w-full sm:w-64" aria-label="Search audit log" />
        <Select value={entity} onChange={(e) => setEntity(e.target.value)} className="w-40" aria-label="Entity type">
          <option value="">All entities</option>
          {["article", "assignment", "source", "cluster", "image", "destination", "user", "setting", "backup", "system", "publishing"].map((t) => (
            <option key={t} value={t}>
              {t}
            </option>
          ))}
        </Select>
        <Input type="date" value={start} onChange={(e) => setStart(e.target.value)} className="w-36" aria-label="From date" />
        <Input type="date" value={end} onChange={(e) => setEnd(e.target.value)} className="w-36" aria-label="To date" />
        <a
          href={downloadUrl("/activity/audit", { ...query, format: "csv" })}
          className="ml-auto inline-flex h-8 items-center gap-1.5 rounded border border-border px-2.5 text-sm hover:bg-surface-2"
          download
        >
          <Download className="h-4 w-4" /> Export CSV
        </a>
      </Toolbar>
      {res.isLoading ? (
        <SkeletonRows />
      ) : res.isError ? (
        <ErrorState error={res.error} onRetry={() => res.refetch()} />
      ) : !res.data?.items.length ? (
        <EmptyState icon={ScrollText} title="No matching activity" description="Every sign-in, edit, approval, publication and setting change is recorded here." />
      ) : (
        <>
          <Table>
            <THead>
              <tr>
                <TH className="w-36">When</TH>
                <TH className="w-48">Who</TH>
                <TH className="w-48">Action</TH>
                <TH>Summary</TH>
              </tr>
            </THead>
            <tbody>
              {res.data.items.map((a) => {
                const link = a.entity_type && a.entity_id ? ENTITY_LINKS[a.entity_type]?.(a.entity_id) : undefined;
                return (
                  <TR key={a.id}>
                    <TD className="whitespace-nowrap text-muted">
                      <Time value={a.created_at} />
                    </TD>
                    <TD className="max-w-[200px] truncate" title={a.actor_label}>
                      {a.actor_label}
                    </TD>
                    <TD>
                      <code className="font-mono text-xs">{a.action}</code>
                    </TD>
                    <TD className="max-w-[520px]">
                      {link ? (
                        <Link href={link} className="line-clamp-2 hover:text-accent">
                          {a.summary}
                        </Link>
                      ) : (
                        <span className="line-clamp-2">{a.summary}</span>
                      )}
                    </TD>
                  </TR>
                );
              })}
            </tbody>
          </Table>
          <Pagination page={page} pageSize={50} total={res.data.total} onPage={setPage} />
        </>
      )}
    </Card>
  );
}

function FetchesTab() {
  const [status, setStatus] = useState("");
  const [page, setPage] = useState(1);
  const res = useQuery({
    queryKey: ["fetches", status, page],
    queryFn: () => api<Page<FetchRow>>("/activity/fetches", { query: { status, page, page_size: 50 } }),
    placeholderData: keepPreviousData,
    refetchInterval: 30_000,
  });
  useEffect(() => setPage(1), [status]);
  return (
    <Card>
      <Toolbar>
        <Select value={status} onChange={(e) => setStatus(e.target.value)} className="w-44" aria-label="Fetch status">
          <option value="">All results</option>
          <option value="success">Success</option>
          <option value="not_modified">Not modified</option>
          <option value="error">Error</option>
          <option value="blocked">Blocked by robots.txt</option>
        </Select>
        <a
          href={downloadUrl("/activity/fetches", { status, format: "csv" })}
          className="ml-auto inline-flex h-8 items-center gap-1.5 rounded border border-border px-2.5 text-sm hover:bg-surface-2"
          download
        >
          <Download className="h-4 w-4" /> Export CSV
        </a>
      </Toolbar>
      {res.isLoading ? (
        <SkeletonRows />
      ) : res.isError ? (
        <ErrorState error={res.error} onRetry={() => res.refetch()} />
      ) : !res.data?.items.length ? (
        <EmptyState icon={ScrollText} title="No fetches recorded" description="The worker logs every feed request: status, timing and how many items were new." />
      ) : (
        <>
          <Table>
            <THead>
              <tr>
                <TH>When</TH>
                <TH>Source</TH>
                <TH>Result</TH>
                <TH className="text-right">HTTP</TH>
                <TH className="text-right">Time</TH>
                <TH className="text-right">Found</TH>
                <TH className="text-right">New</TH>
                <TH className="text-right">Dup.</TH>
                <TH className="text-right">Size</TH>
                <TH>Error</TH>
              </tr>
            </THead>
            <tbody>
              {res.data.items.map((f) => (
                <TR key={f.id}>
                  <TD className="whitespace-nowrap text-muted">
                    <Time value={f.started_at} />
                  </TD>
                  <TD className="max-w-[220px]">
                    <Link href={`/sources/${f.source_id}`} className="block truncate hover:text-accent">
                      {f.source_name}
                    </Link>
                    <span className="block text-2xs text-muted">{f.trigger}</span>
                  </TD>
                  <TD>
                    <StatusBadge status={f.status} />
                  </TD>
                  <TD className="tabular text-right">{f.http_status ?? "—"}</TD>
                  <TD className="tabular text-right">{formatDuration(f.duration_ms)}</TD>
                  <TD className="tabular text-right">{f.items_found}</TD>
                  <TD className="tabular text-right">{f.items_new}</TD>
                  <TD className="tabular text-right">{f.items_duplicate}</TD>
                  <TD className="tabular text-right text-muted">{formatBytes(f.bytes_received)}</TD>
                  <TD className="max-w-[260px] truncate text-xs text-danger" title={f.error_message ?? undefined}>
                    {f.error_message}
                  </TD>
                </TR>
              ))}
            </tbody>
          </Table>
          <Pagination page={page} pageSize={50} total={res.data.total} onPage={setPage} />
        </>
      )}
    </Card>
  );
}

export default function ActivityPage() {
  const params = useSearchParams();
  const router = useRouter();
  const tab = params.get("tab") === "fetches" ? "fetches" : "audit";
  return (
    <div className="mx-auto max-w-7xl">
      <PageHeader title="Activity logs" description="An append-only record of who did what, and every collection request the worker made." />
      <Tabs value={tab} onValueChange={(v) => router.replace(v === "audit" ? "/activity" : `/activity?tab=${v}`)}>
        <TabsList className="mb-3">
          <TabsTrigger value="audit">Audit trail</TabsTrigger>
          <TabsTrigger value="fetches">Fetch history</TabsTrigger>
        </TabsList>
        <TabsContent value="audit">
          <AuditTab />
        </TabsContent>
        <TabsContent value="fetches">
          <FetchesTab />
        </TabsContent>
      </Tabs>
    </div>
  );
}
