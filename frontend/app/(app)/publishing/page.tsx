"use client";

import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  CheckCircle2,
  ExternalLink,
  Globe,
  KeyRound,
  ListChecks,
  LogIn,
  Pencil,
  Play,
  Plug,
  Plus,
  ShieldCheck,
  Trash2,
  Unplug,
  Webhook,
  Zap,
} from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { DestinationDialog, startConnect } from "@/components/publishing/destination-dialog";
import { KIND_ICON, KIND_LABEL, isSocial } from "@/components/publishing/kinds";
import { ACTION_LABEL, JobActions, JobStatus, useInvalidatePublishing } from "@/components/publishing/jobs";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { useConfirm } from "@/components/ui/confirm";
import { Select } from "@/components/ui/input";
import { PageHeader, Pagination, Toolbar } from "@/components/ui/page";
import { EmptyState, ErrorState, InlineAlert, SkeletonRows } from "@/components/ui/states";
import { StatusBadge } from "@/components/ui/status-badge";
import { Switch } from "@/components/ui/switch";
import { Table, TD, TH, THead, TR } from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Time } from "@/components/ui/time";
import { api, errorMessage } from "@/lib/api";
import { PERMS, useSession } from "@/lib/session";
import type { CountedPage, DestinationOut, JobOut, SettingsMap } from "@/lib/types";
import { cn, formatNumber, hostOf } from "@/lib/utils";

const TAB_KEYS = ["queue", "destinations", "policy", "webhooks"] as const;
type TabKey = (typeof TAB_KEYS)[number];

export default function PublishingPage() {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const raw = params.get("tab");
  const tab: TabKey = (TAB_KEYS as readonly string[]).includes(raw ?? "") ? (raw as TabKey) : "queue";

  return (
    <div className="mx-auto max-w-7xl">
      <PageHeader
        title="Publishing"
        description="Where approved articles go, and what happened when they were sent. Nothing is published without an explicit request unless you enable auto-publish."
      />
      <Tabs value={tab} onValueChange={(v) => router.replace(v === "queue" ? pathname : `${pathname}?tab=${v}`, { scroll: false })}>
        <TabsList className="mb-4">
          <TabsTrigger value="queue">Queue</TabsTrigger>
          <TabsTrigger value="destinations">Destinations</TabsTrigger>
          <TabsTrigger value="policy">Policy</TabsTrigger>
          <TabsTrigger value="webhooks">Webhook format</TabsTrigger>
        </TabsList>
        <TabsContent value="queue">
          <QueueTab />
        </TabsContent>
        <TabsContent value="destinations">
          <DestinationsTab />
        </TabsContent>
        <TabsContent value="policy">
          <PolicyTab />
        </TabsContent>
        <TabsContent value="webhooks">
          <WebhookDocs />
        </TabsContent>
      </Tabs>
    </div>
  );
}

// --- Queue --------------------------------------------------------------------------------------

const JOB_FILTERS = [
  { key: "", label: "All" },
  { key: "pending", label: "Pending" },
  { key: "running", label: "Running" },
  { key: "failed", label: "Failed" },
  { key: "succeeded", label: "Succeeded" },
  { key: "cancelled", label: "Cancelled" },
];
const PAGE_SIZE = 50;

function useDestinations() {
  return useQuery({ queryKey: ["publishing-destinations"], queryFn: () => api<DestinationOut[]>("/publishing/destinations") });
}

function QueueTab() {
  const { can } = useSession();
  const canPublish = can(PERMS.articlesPublish);
  const invalidate = useInvalidatePublishing();
  const [status, setStatus] = useState("");
  const [dest, setDest] = useState("");
  const [page, setPage] = useState(1);
  useEffect(() => setPage(1), [status, dest]);
  const destinations = useDestinations();

  const query = { status: status ? [status] : undefined, destination_id: dest || undefined, page, page_size: PAGE_SIZE };
  const jobs = useQuery({
    queryKey: ["publishing-jobs", "list", query],
    queryFn: ({ signal }) => api<CountedPage<JobOut>>("/publishing/jobs", { query, signal }),
    placeholderData: keepPreviousData,
    refetchInterval: 15_000,
  });
  const run = useMutation({
    mutationFn: () => api<{ processed: number; succeeded: number; failed: number; retrying: number }>("/publishing/jobs/run", { method: "POST" }),
    onSuccess: (s) => {
      if (!s.processed) toast.info("Nothing was due. Scheduled jobs wait for their time; retries wait for their back-off.");
      else toast.success(`Processed ${s.processed}: ${s.succeeded} succeeded, ${s.failed} failed, ${s.retrying} will retry`);
      invalidate();
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  const counts = jobs.data?.status_counts ?? {};
  const total = Object.values(counts).reduce((a, b) => a + b, 0);
  const items = jobs.data?.items ?? [];

  return (
    <Card>
      <nav aria-label="Filter jobs by status" className="flex gap-1 overflow-x-auto border-b border-border px-2 pt-2">
        {JOB_FILTERS.map((f) => (
          <button
            key={f.key || "all"}
            type="button"
            aria-current={status === f.key ? "page" : undefined}
            onClick={() => setStatus(f.key)}
            className={cn(
              "-mb-px inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap border-b-2 px-2.5 pb-2 pt-1 text-sm",
              status === f.key ? "border-accent font-medium text-text" : "border-transparent text-muted hover:text-text",
            )}
          >
            {f.label}
            <span className={cn("tabular rounded px-1.5 text-2xs", f.key === "failed" && counts.failed ? "bg-danger/10 text-danger" : "bg-surface-2 text-muted")}>
              {jobs.data ? formatNumber(f.key ? counts[f.key] ?? 0 : total) : "–"}
            </span>
          </button>
        ))}
      </nav>
      <Toolbar>
        <label className="flex items-center gap-2 text-xs text-muted">
          Destination
          <Select value={dest} onChange={(e) => setDest(e.target.value)} className="w-auto" aria-label="Filter by destination">
            <option value="">All destinations</option>
            {(destinations.data ?? []).map((d) => (
              <option key={d.id} value={d.id}>
                {d.name}
              </option>
            ))}
          </Select>
        </label>
        <span className="flex-1" />
        <span className="hidden text-xs text-muted md:inline">The background worker runs due jobs every minute.</span>
        {canPublish ? (
          <Button size="sm" variant="outline" loading={run.isPending} onClick={() => run.mutate()}>
            <Play className="h-3.5 w-3.5" /> Run queue now
          </Button>
        ) : null}
      </Toolbar>
      {jobs.isLoading ? (
        <SkeletonRows rows={6} />
      ) : jobs.isError ? (
        <ErrorState error={jobs.error} onRetry={() => jobs.refetch()} />
      ) : !items.length ? (
        <EmptyState
          icon={ListChecks}
          title={status || dest ? "No jobs match this filter" : "No publishing jobs yet"}
          description={status || dest ? undefined : "Jobs appear here when an approved article is published, scheduled or unpublished from the article editor."}
          action={
            !status && !dest ? (
              <Link href="/articles?status=approved" className="text-sm text-accent hover:underline">
                Approved articles
              </Link>
            ) : null
          }
        />
      ) : (
        <>
          <Table className={cn(jobs.isPlaceholderData && "opacity-60")}>
            <THead>
              <tr>
                <TH>Article</TH>
                <TH className="hidden md:table-cell">Destination</TH>
                <TH>Status</TH>
                <TH className="hidden sm:table-cell">When</TH>
                <TH className="hidden lg:table-cell text-right">Attempts</TH>
                <TH className="w-px">
                  <span className="sr-only">Actions</span>
                </TH>
              </tr>
            </THead>
            <tbody>
              {items.map((j) => {
                const when =
                  j.status === "pending" && j.attempts === 0 && j.scheduled_for
                    ? { label: "scheduled", value: j.scheduled_for }
                    : j.status === "pending" && j.next_attempt_at
                      ? { label: "next try", value: j.next_attempt_at }
                      : j.completed_at
                        ? { label: "finished", value: j.completed_at }
                        : { label: "requested", value: j.created_at };
                return (
                  <TR key={j.id}>
                    <TD className="max-w-0 py-2">
                      <Link href={`/articles/${j.article_id}`} className="block truncate font-medium hover:text-accent" title={j.article_headline ?? undefined}>
                        {j.article_headline ?? "Deleted article"}
                      </Link>
                      <div className="mt-0.5 flex items-center gap-1.5 truncate text-xs text-muted">
                        <span>{ACTION_LABEL[j.action] ?? j.action}</span>
                        <span>· v{j.article_version}</span>
                        <span className="md:hidden">· {j.destination_name}</span>
                        {j.remote_url ? (
                          <a href={j.remote_url} target="_blank" rel="noopener noreferrer" className="truncate text-accent hover:underline">
                            · {hostOf(j.remote_url)}
                          </a>
                        ) : null}
                      </div>
                      {j.last_error ? (
                        <p className={cn("mt-0.5 line-clamp-2 text-xs", j.status === "failed" ? "text-danger" : "text-muted")} title={j.last_error}>
                          {j.last_error}
                        </p>
                      ) : null}
                    </TD>
                    <TD className="hidden whitespace-nowrap text-sm md:table-cell">{j.destination_name}</TD>
                    <TD className="whitespace-nowrap">
                      <JobStatus job={j} />
                    </TD>
                    <TD className="hidden whitespace-nowrap text-xs text-muted sm:table-cell">
                      {when.label} <Time value={when.value} />
                    </TD>
                    <TD className="tabular hidden text-right text-xs text-muted lg:table-cell">
                      {j.attempts}/{j.max_attempts}
                    </TD>
                    <TD className="whitespace-nowrap">
                      <JobActions job={j} canAct={canPublish} />
                    </TD>
                  </TR>
                );
              })}
            </tbody>
          </Table>
          <Pagination page={page} pageSize={PAGE_SIZE} total={jobs.data?.total ?? 0} onPage={setPage} />
        </>
      )}
    </Card>
  );
}

// --- Destinations -------------------------------------------------------------------------------

/** Shown after Facebook / Google send the browser back: report the result once, then tidy the address. */
function useConnectResult(destinations: DestinationOut[] | undefined) {
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const qc = useQueryClient();
  const handled = useRef<string | null>(null);
  const connected = params.get("connected");
  const failed = params.get("connect_error");
  useEffect(() => {
    if (!connected && !failed) return;
    const key = `${connected ?? ""}|${failed ?? ""}`;
    if (handled.current === key) return;
    if (failed) {
      handled.current = key;
      toast.error(failed, { duration: 15000 });
    } else {
      // Wait for fresh data so the message says whether a Facebook Page still has to be chosen.
      if (!destinations) return;
      handled.current = key;
      const d = destinations.find((x) => x.id === connected);
      if (d?.connection?.connected) toast.success(`${d.name} is connected${d.connection.account_name ? ` to ${d.connection.account_name}` : ""}.`);
      else if (d?.connection?.pages?.length) toast.info(`Signed in. Now choose which Facebook Page ${d.name} posts to.`);
      else toast.success("Connected.");
    }
    qc.invalidateQueries({ queryKey: ["publishing-destinations"] });
    router.replace(`${pathname}?tab=destinations`, { scroll: false });
  }, [connected, failed, destinations, qc, router, pathname]);
}

function PageChooser({ d }: { d: DestinationOut }) {
  const qc = useQueryClient();
  const pages = d.connection?.pages ?? [];
  const [pageId, setPageId] = useState(pages[0]?.id ?? "");
  const choose = useMutation({
    mutationFn: () => api<DestinationOut>(`/publishing/destinations/${d.id}/facebook-page`, { body: { page_id: pageId } }),
    onSuccess: (r) => {
      toast.success(`${r.name} now posts to ${r.connection?.account_name ?? "the Page"}.`);
      qc.invalidateQueries({ queryKey: ["publishing-destinations"] });
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  return (
    <div className="mx-4 mb-2 flex flex-col gap-2 rounded-lg border border-warning/40 bg-warning/5 p-2.5 sm:flex-row sm:items-end">
      <label className="flex min-w-0 flex-1 flex-col gap-1 text-xs">
        <span className="font-medium">Choose the Facebook Page to post to</span>
        <Select value={pageId} onChange={(e) => setPageId(e.target.value)} aria-label="Facebook Page">
          {pages.map((pg) => (
            <option key={pg.id} value={pg.id}>
              {pg.name}
            </option>
          ))}
        </Select>
      </label>
      <Button size="sm" variant="primary" disabled={!pageId} loading={choose.isPending} onClick={() => choose.mutate()}>
        Use this Page
      </Button>
    </div>
  );
}

function DestinationsTab() {
  const { can } = useSession();
  const manage = can(PERMS.publishingManage);
  const qc = useQueryClient();
  const confirm = useConfirm();
  const destinations = useDestinations();
  const policy = useQuery({ queryKey: ["settings"], queryFn: () => api<{ values: SettingsMap }>("/settings") });
  const globalAuto = !!policy.data?.values.publishing?.auto_publish_policy;
  const [editing, setEditing] = useState<DestinationOut | null>(null);
  const [open, setOpen] = useState(false);
  useConnectResult(destinations.isFetching ? undefined : destinations.data);
  const [connecting, setConnecting] = useState<string | null>(null);
  const connect = async (d: DestinationOut) => {
    setConnecting(d.id);
    try {
      await startConnect(d);
    } catch (e) {
      toast.error(errorMessage(e));
      setConnecting(null);
    }
  };
  const disconnect = useMutation({
    mutationFn: (d: DestinationOut) => api<DestinationOut>(`/publishing/destinations/${d.id}/disconnect`, { method: "POST" }),
    onSuccess: (_, d) => {
      toast.success(`Disconnected ${d.name}`);
      qc.invalidateQueries({ queryKey: ["publishing-destinations"] });
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  const test = useMutation({
    mutationFn: (d: DestinationOut) => api<{ ok: boolean; message: string }>(`/publishing/destinations/${d.id}/test`, { method: "POST" }),
    onSuccess: (r, d) => {
      if (r.ok) toast.success(`${d.name}: ${r.message}`);
      else toast.error(`${d.name}: ${r.message}`);
      qc.invalidateQueries({ queryKey: ["publishing-destinations"] });
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const del = useMutation({
    mutationFn: (d: DestinationOut) => api(`/publishing/destinations/${d.id}`, { method: "DELETE" }),
    onSuccess: (_, d) => {
      toast.success(`Deleted ${d.name}`);
      qc.invalidateQueries({ queryKey: ["publishing-destinations"] });
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        <p className="text-sm text-muted">
          Add your website (WordPress username and application password), a Facebook Page or a YouTube channel. Credentials are stored
          encrypted on the server and are never sent back to the browser.
        </p>
        {manage ? (
          <Button
            variant="primary"
            onClick={() => {
              setEditing(null);
              setOpen(true);
            }}
          >
            <Plus className="h-4 w-4" /> Add destination
          </Button>
        ) : null}
      </div>

      {destinations.isLoading ? (
        <SkeletonRows rows={3} />
      ) : destinations.isError ? (
        <ErrorState error={destinations.error} onRetry={() => destinations.refetch()} />
      ) : !destinations.data?.length ? (
        <Card>
          <EmptyState icon={Plug} title="No destinations" description="Add your website, a Facebook Page or a YouTube channel to publish outside the built-in site." />
        </Card>
      ) : (
        <ul className="grid gap-3 md:grid-cols-2">
          {destinations.data.map((d) => {
            const Icon = KIND_ICON[d.kind] ?? Globe;
            const checkOk = d.last_check_status?.startsWith("OK");
            const social = isSocial(d.kind);
            const conn = d.connection;
            const choosingPage = social && !conn?.connected && !!conn?.pages?.length;
            return (
              <li key={d.id}>
                <Card className="flex h-full flex-col">
                  <div className="flex items-start gap-3 px-4 pt-3">
                    <span className="mt-0.5 rounded border border-border bg-surface-2 p-1.5 text-muted">
                      <Icon className="h-4 w-4" aria-hidden />
                    </span>
                    <div className="min-w-0 flex-1">
                      <h2 className="truncate text-sm font-semibold">{d.name}</h2>
                      <p className="truncate text-xs text-muted">
                        {KIND_LABEL[d.kind] ?? d.kind}
                        {!social && d.base_url ? <> · {hostOf(d.base_url)}</> : null}
                        {!social && d.username ? <> · {d.username}</> : null}
                        {social && conn?.connected && conn.account_name ? (
                          <>
                            {" · "}
                            {conn.account_url ? (
                              <a href={conn.account_url} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-0.5 hover:underline">
                                {conn.account_name}
                                <ExternalLink className="h-3 w-3" aria-hidden />
                              </a>
                            ) : (
                              conn.account_name
                            )}
                          </>
                        ) : null}
                      </p>
                    </div>
                    <StatusBadge status={d.is_active ? "active" : "disabled"} label={d.is_active ? "Active" : "Inactive"} />
                  </div>
                  <div className="flex flex-wrap gap-1.5 px-4 pt-2">
                    <Badge tone={d.is_commercial ? "neutral" : "info"} title="Commercial destinations block NonCommercial-licensed images">
                      {d.is_commercial ? "Commercial" : "Non-commercial"}
                    </Badge>
                    {social ? (
                      <Badge tone={conn?.connected ? "success" : "warning"} icon={conn?.connected ? CheckCircle2 : Unplug}>
                        {conn?.connected ? "Connected" : choosingPage ? "Choose a Page" : "Not connected"}
                      </Badge>
                    ) : d.kind !== "builtin" ? (
                      <Badge tone={d.has_secret ? "neutral" : d.kind === "wordpress" ? "danger" : "warning"} icon={KeyRound}>
                        {d.has_secret ? "Credential stored" : d.kind === "wordpress" ? "No credential" : "Unsigned"}
                      </Badge>
                    ) : null}
                    {d.auto_publish ? (
                      <Badge tone={globalAuto ? "warning" : "neutral"} icon={Zap} title={globalAuto ? "Approved articles are published here automatically" : "Inactive while the global policy is off"}>
                        Auto-publish {globalAuto ? "on" : "(policy off)"}
                      </Badge>
                    ) : null}
                  </div>
                  <div className="flex-1 px-4 py-2 text-xs text-muted">
                    {d.last_checked_at && d.last_check_status ? (
                      <p className={cn("line-clamp-2", checkOk ? "text-success" : "text-danger")} title={d.last_check_status ?? undefined}>
                        <span className="sr-only">Last connection test: </span>
                        {d.last_check_status} · <Time value={d.last_checked_at} />
                      </p>
                    ) : choosingPage ? (
                      <p>Signed in to Facebook. Choose which Page NEWSWIRE posts to.</p>
                    ) : social && !conn?.connected ? (
                      <p>
                        {manage
                          ? `Click Connect and sign in with the ${d.kind === "facebook" ? "Facebook account that manages the Page" : "Google account that owns the channel"}.`
                          : "Not connected yet. An administrator has to connect it."}
                      </p>
                    ) : d.kind !== "builtin" ? (
                      <p>Connection not tested yet.</p>
                    ) : (
                      <p>Serves the public API (/api/v1/public) and RSS feed.</p>
                    )}
                  </div>
                  {manage && choosingPage ? <PageChooser key={conn?.pages?.map((x) => x.id).join()} d={d} /> : null}
                  {manage ? (
                    <div className="flex flex-wrap gap-1 border-t border-border px-3 py-2">
                      {social ? (
                        <Button
                          size="sm"
                          variant={conn?.connected || choosingPage ? "ghost" : "primary"}
                          loading={connecting === d.id}
                          onClick={() => connect(d)}
                        >
                          <LogIn className="h-3.5 w-3.5" /> {conn?.connected || choosingPage ? "Reconnect" : "Connect"}
                        </Button>
                      ) : null}
                      {d.kind !== "builtin" && (!social || conn?.connected) ? (
                        <Button size="sm" variant="ghost" loading={test.isPending && test.variables?.id === d.id} onClick={() => test.mutate(d)}>
                          <Plug className="h-3.5 w-3.5" /> Test connection
                        </Button>
                      ) : null}
                      {social && (conn?.connected || choosingPage) ? (
                        <Button
                          size="sm"
                          variant="ghost"
                          loading={disconnect.isPending && disconnect.variables?.id === d.id}
                          onClick={async () => {
                            const ok = await confirm({
                              title: `Disconnect ${d.name}?`,
                              body: "NEWSWIRE forgets the sign-in. Posts already published stay online. Scheduled posts here will fail until you connect again.",
                              confirmLabel: "Disconnect",
                              destructive: true,
                            });
                            if (ok) disconnect.mutate(d);
                          }}
                        >
                          <Unplug className="h-3.5 w-3.5" /> Disconnect
                        </Button>
                      ) : null}
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => {
                          setEditing(d);
                          setOpen(true);
                        }}
                      >
                        <Pencil className="h-3.5 w-3.5" /> Edit
                      </Button>
                      {d.kind !== "builtin" ? (
                        <Button
                          size="sm"
                          variant="ghost"
                          className="ml-auto text-danger"
                          loading={del.isPending && del.variables?.id === d.id}
                          onClick={async () => {
                            const ok = await confirm({
                              title: `Delete ${d.name}?`,
                              body: "The stored credential is erased. Articles still published here must be unpublished first.",
                              confirmLabel: "Delete",
                              destructive: true,
                            });
                            if (ok) del.mutate(d);
                          }}
                        >
                          <Trash2 className="h-3.5 w-3.5" /> Delete
                        </Button>
                      ) : null}
                    </div>
                  ) : null}
                </Card>
              </li>
            );
          })}
        </ul>
      )}
      {!manage ? <p className="text-xs text-muted">Managing destinations requires the administrator role.</p> : null}
      <DestinationDialog open={open} onOpenChange={setOpen} destination={editing} />
    </div>
  );
}

// --- Policy -------------------------------------------------------------------------------------

type Policy = { auto_publish_policy: boolean; block_on_template_markers: boolean };

function PolicyTab() {
  const { can } = useSession();
  const canEdit = can(PERMS.settingsWrite);
  const qc = useQueryClient();
  const confirm = useConfirm();
  const settings = useQuery({ queryKey: ["settings"], queryFn: () => api<{ values: SettingsMap }>("/settings") });
  const destinations = useDestinations();
  const server: Policy | null = settings.data
    ? {
        auto_publish_policy: !!settings.data.values.publishing?.auto_publish_policy,
        block_on_template_markers: settings.data.values.publishing?.block_on_template_markers !== false,
      }
    : null;
  const [form, setForm] = useState<Policy | null>(null);
  useEffect(() => {
    if (server && !form) setForm(server);
  }, [server, form]);

  const save = useMutation({
    mutationFn: (p: Policy) => api<{ value: Policy }>("/settings/publishing", { method: "PUT", body: p }),
    onSuccess: (r) => {
      setForm({ auto_publish_policy: !!r.value.auto_publish_policy, block_on_template_markers: r.value.block_on_template_markers !== false });
      qc.invalidateQueries({ queryKey: ["settings"] });
      toast.success("Publishing policy saved");
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  if (settings.isLoading || !form || !server) {
    return settings.isError ? <ErrorState error={settings.error} onRetry={() => settings.refetch()} /> : <SkeletonRows rows={4} />;
  }
  const dirty = form.auto_publish_policy !== server.auto_publish_policy || form.block_on_template_markers !== server.block_on_template_markers;
  const autoDests = (destinations.data ?? []).filter((d) => d.auto_publish && d.is_active);

  const submit = async () => {
    if (form.auto_publish_policy && !server.auto_publish_policy) {
      const ok = await confirm({
        title: "Turn on auto-publish?",
        body: autoDests.length
          ? `Approving an article will publish it immediately to: ${autoDests.map((d) => d.name).join(", ")}.`
          : "No active destination allows auto-publish yet, so nothing changes until you enable it on one.",
        confirmLabel: "Turn on",
        destructive: autoDests.length > 0,
      });
      if (!ok) return;
    }
    save.mutate(form);
  };

  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_320px]">
      <Card>
        <CardHeader title="Publishing policy" icon={ShieldCheck} description="Applies to every destination." />
        <CardBody className="flex flex-col gap-4">
          <div>
            <Switch
              id="pol-auto"
              label="Allow auto-publish"
              checked={form.auto_publish_policy}
              disabled={!canEdit}
              onCheckedChange={(v) => setForm({ ...form, auto_publish_policy: v })}
            />
            <p className="ml-10 mt-1 text-xs text-muted">
              Off by default. When on, approving an article also queues it for every active destination that allows auto-publish.
            </p>
          </div>
          <div>
            <Switch
              id="pol-markers"
              label="Block publishing while template markers remain"
              checked={form.block_on_template_markers}
              disabled={!canEdit}
              onCheckedChange={(v) => setForm({ ...form, block_on_template_markers: v })}
            />
            <p className="ml-10 mt-1 text-xs text-muted">
              Drafts started from research contain [EDITOR: …] prompts. Keep this on so they never go live.
            </p>
          </div>
          {form.auto_publish_policy && !form.block_on_template_markers ? (
            <InlineAlert tone="warning">Auto-publish without the template-marker check can publish unfinished drafts.</InlineAlert>
          ) : null}
          {canEdit ? (
            <div className="flex items-center gap-2">
              <Button variant="primary" disabled={!dirty} loading={save.isPending} onClick={submit}>
                Save policy
              </Button>
              {dirty ? (
                <Button variant="ghost" onClick={() => setForm(server)}>
                  Discard
                </Button>
              ) : null}
            </div>
          ) : (
            <p className="text-xs text-muted">Changing the policy requires the settings permission (administrators).</p>
          )}
        </CardBody>
      </Card>
      <Card>
        <CardHeader title="When does anything publish by itself?" icon={Zap} />
        <CardBody className="flex flex-col gap-2 text-sm">
          <p>Only when all of these are true at the moment an article is approved:</p>
          <ol className="ml-4 list-decimal space-y-1 text-muted">
            <li>
              The global policy is on <Badge tone={server.auto_publish_policy ? "warning" : "neutral"}>{server.auto_publish_policy ? "on" : "off"}</Badge>
            </li>
            <li>
              The destination allows auto-publish and is active{" "}
              <Badge tone={autoDests.length ? "warning" : "neutral"}>{autoDests.length ? autoDests.map((d) => d.name).join(", ") : "none"}</Badge>
            </li>
            <li>The article passes every blocking check, including image licences for that destination.</li>
          </ol>
          <p className="text-muted">
            Otherwise an editor publishes by hand from the article. Images with an unknown licence, NonCommercial images on a commercial
            destination and modified NoDerivatives images are always blocked.
          </p>
        </CardBody>
      </Card>
    </div>
  );
}

// --- Webhook documentation ----------------------------------------------------------------------

const NODE_SNIPPET = `import crypto from "node:crypto";

// rawBody: the exact bytes received (do not re-serialise the JSON)
function verify(rawBody, header, secret) {
  if (!header?.startsWith("sha256=")) return false;
  const expected = crypto.createHmac("sha256", secret).update(rawBody).digest("hex");
  const given = header.slice("sha256=".length);
  return given.length === expected.length &&
    crypto.timingSafeEqual(Buffer.from(given), Buffer.from(expected));
}`;

const PY_SNIPPET = `import hashlib, hmac

def verify(raw_body: bytes, header: str | None, secret: str) -> bool:
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.split("=", 1)[1])`;

const PAYLOAD = `POST <your endpoint>
Content-Type: application/json
X-Newswire-Event: article.published
X-Newswire-Delivery: <article id>:<version>:article.published
X-Newswire-Signature: sha256=<hex HMAC-SHA256 of the raw body>

{
  "event": "article.published",
  "delivery_id": "…",
  "article": {
    "id": "…", "slug": "…", "headline": "…", "subtitle": "…",
    "excerpt": "…", "seo_title": "…", "seo_description": "…",
    "category": "…", "tags": ["…"],
    "html": "<full article with header and credits>",
    "body_html": "<body only>",
    "featured_image": { "url": "…", "caption": "…", "credit": "…",
                        "license": "…", "license_url": "…", "source_page_url": "…" },
    "sources": [{ "title": "…", "url": "…", "publisher": "…" }],
    "version": 3, …
  }
}`;

function Code({ children, label }: { children: string; label: string }) {
  return (
    <pre aria-label={label} className="overflow-x-auto rounded border border-border bg-surface-2 p-3 font-mono text-xs leading-5">
      <code>{children}</code>
    </pre>
  );
}

function WebhookDocs() {
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card>
        <CardHeader title="Request format" icon={Webhook} description="One POST per publish, update, draft or unpublish." />
        <CardBody className="flex flex-col gap-3 text-sm">
          <Code label="Example webhook request">{PAYLOAD}</Code>
          <ul className="ml-4 list-disc space-y-1 text-muted">
            <li>
              Events: <code className="font-mono text-xs">article.published</code>, <code className="font-mono text-xs">article.updated</code>,{" "}
              <code className="font-mono text-xs">article.draft</code>, <code className="font-mono text-xs">article.unpublished</code> (id and slug
              only) and <code className="font-mono text-xs">ping</code> from the connection test.
            </li>
            <li>The delivery id is stable for a given version and event, so retries can be de-duplicated.</li>
            <li>
              Reply with a 2xx status. Optionally return <code className="font-mono text-xs">{`{"id": "…", "url": "…"}`}</code> to record the
              remote id and public URL. 5xx and 429 responses are retried with back-off; other 4xx responses fail the job.
            </li>
          </ul>
        </CardBody>
      </Card>
      <Card>
        <CardHeader title="Verifying the signature" icon={KeyRound} description="Requests are signed when the destination has a signing secret." />
        <CardBody className="flex flex-col gap-3 text-sm">
          <p className="text-muted">
            <code className="font-mono text-xs">X-Newswire-Signature</code> is <code className="font-mono text-xs">sha256=</code> followed by the
            lowercase hex HMAC-SHA256 of the raw request body, keyed with the destination’s signing secret. Compare in constant time and reject
            anything that does not match.
          </p>
          <p className="text-xs font-medium">Node.js</p>
          <Code label="Node.js verification example">{NODE_SNIPPET}</Code>
          <p className="text-xs font-medium">Python</p>
          <Code label="Python verification example">{PY_SNIPPET}</Code>
        </CardBody>
      </Card>
    </div>
  );
}
