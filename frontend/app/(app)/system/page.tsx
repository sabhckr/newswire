"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Archive, CheckCircle2, Cpu, Database, Download, HardDrive, Play, RefreshCw, ScrollText, ShieldCheck, Trash2 } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { useConfirm } from "@/components/ui/confirm";
import { Input, Select } from "@/components/ui/input";
import { PageHeader, Stat, Toolbar } from "@/components/ui/page";
import { EmptyState, ErrorState, InlineAlert, SkeletonRows } from "@/components/ui/states";
import { StatusBadge } from "@/components/ui/status-badge";
import { Table, TD, TH, THead, TR } from "@/components/ui/table";
import { Time } from "@/components/ui/time";
import { api, downloadUrl, errorMessage } from "@/lib/api";
import { PERMS, useSession } from "@/lib/session";
import type { BackupRow, SystemStatus } from "@/lib/types";
import { formatBytes, formatDuration, formatInterval, formatNumber } from "@/lib/utils";

const JOB_LABELS: Record<string, string> = {
  collection: "Feed collection",
  publishing: "Publishing queue",
  backups: "Scheduled backups",
  housekeeping: "Housekeeping",
  publication_sync: "WordPress status sync",
};

function Backups() {
  const qc = useQueryClient();
  const confirm = useConfirm();
  const list = useQuery({ queryKey: ["backups"], queryFn: () => api<{ items: BackupRow[]; directory: string }>("/system/backups") });
  const create = useMutation({
    mutationFn: () => api<BackupRow>("/system/backups", { method: "POST" }),
    onSuccess: (b) => {
      toast.success(`Backup ${b.filename} created and verified`);
      qc.invalidateQueries({ queryKey: ["backups"] });
      qc.invalidateQueries({ queryKey: ["system-status"] });
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const verify = useMutation({
    mutationFn: (id: string) => api<{ ok: boolean; message: string }>(`/system/backups/${id}/verify`, { method: "POST" }),
    onSuccess: (r) => {
      if (r.ok) toast.success(`Verified: ${r.message}`);
      else toast.error(`Verification failed: ${r.message}`);
      qc.invalidateQueries({ queryKey: ["backups"] });
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const remove = useMutation({
    mutationFn: (id: string) => api(`/system/backups/${id}`, { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["backups"] }),
    onError: (e) => toast.error(errorMessage(e)),
  });
  return (
    <Card>
      <CardHeader
        title="Backups"
        description={list.data ? `Stored in ${list.data.directory}. Restore with the command line: python -m app.cli restore <file> --yes` : undefined}
        icon={Archive}
        actions={
          <Button size="sm" variant="primary" loading={create.isPending} onClick={() => create.mutate()}>
            <Archive className="h-3.5 w-3.5" /> Back up now
          </Button>
        }
      />
      {list.isLoading ? (
        <SkeletonRows rows={3} />
      ) : list.isError ? (
        <ErrorState error={list.error} onRetry={() => list.refetch()} />
      ) : !list.data?.items.length ? (
        <EmptyState icon={Archive} title="No backups yet" description="Create one now, or let the worker make scheduled backups (Settings → Collection & backups)." />
      ) : (
        <Table>
          <THead>
            <tr>
              <TH>Created</TH>
              <TH>File</TH>
              <TH>Kind</TH>
              <TH>State</TH>
              <TH className="text-right">Size</TH>
              <TH>Verification</TH>
              <TH className="text-right">Actions</TH>
            </tr>
          </THead>
          <tbody>
            {list.data.items.map((b) => (
              <TR key={b.id}>
                <TD className="whitespace-nowrap text-muted">
                  <Time value={b.created_at} />
                </TD>
                <TD className="max-w-[260px]">
                  <span className="block truncate font-mono text-xs" title={b.sha256 ? `SHA-256 ${b.sha256}` : undefined}>
                    {b.filename}
                  </span>
                  <span className="block text-2xs text-muted">
                    {b.includes?.join(" + ")}
                    {b.encrypted ? " · encrypted" : ""}
                  </span>
                </TD>
                <TD className="capitalize">{b.kind}</TD>
                <TD>
                  <StatusBadge status={b.status} />
                </TD>
                <TD className="tabular text-right">{formatBytes(b.size_bytes)}</TD>
                <TD className="max-w-[220px] truncate text-xs" title={b.error ?? b.verify_status ?? undefined}>
                  {b.error ? <span className="text-danger">{b.error}</span> : b.verify_status ?? "—"}
                </TD>
                <TD className="text-right">
                  <div className="flex justify-end gap-1">
                    {b.status === "completed" ? (
                      <>
                        <Button size="sm" variant="ghost" onClick={() => verify.mutate(b.id)} loading={verify.isPending && verify.variables === b.id}>
                          <ShieldCheck className="h-3.5 w-3.5" /> Verify
                        </Button>
                        <a
                          href={downloadUrl(`/system/backups/${b.id}/download`)}
                          className="inline-flex h-7 items-center gap-1.5 rounded px-2 text-xs hover:bg-surface-2"
                          download
                        >
                          <Download className="h-3.5 w-3.5" /> Download
                        </a>
                      </>
                    ) : null}
                    <Button
                      size="icon-sm"
                      variant="ghost"
                      aria-label={`Delete ${b.filename}`}
                      disabled={b.status === "running"}
                      onClick={async () => {
                        if (
                          await confirm({
                            title: "Delete this backup?",
                            body: `${b.filename} will be removed from disk. This cannot be undone.`,
                            confirmLabel: "Delete backup",
                            destructive: true,
                          })
                        )
                          remove.mutate(b.id);
                      }}
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </Button>
                  </div>
                </TD>
              </TR>
            ))}
          </tbody>
        </Table>
      )}
    </Card>
  );
}

function Logs() {
  const [process, setProcess] = useState<"worker" | "api">("worker");
  const [level, setLevel] = useState("");
  const [q, setQ] = useState("");
  const logs = useQuery({
    queryKey: ["logs", process, level, q],
    queryFn: () => api<{ lines: string[] }>("/system/logs", { query: { process, level, q, lines: 400 } }),
    refetchInterval: 15_000,
  });
  return (
    <Card>
      <CardHeader
        title="Logs"
        description="Most recent lines, newest last. Secrets, tokens and passwords are redacted."
        icon={ScrollText}
        actions={
          <a href={downloadUrl("/system/logs/export", { process })} className="inline-flex h-7 items-center gap-1.5 rounded border border-border px-2 text-xs hover:bg-surface-2" download>
            <Download className="h-3.5 w-3.5" /> Export
          </a>
        }
      />
      <Toolbar>
        <Select value={process} onChange={(e) => setProcess(e.target.value as "worker" | "api")} className="w-32" aria-label="Process">
          <option value="worker">Worker</option>
          <option value="api">API</option>
        </Select>
        <Select value={level} onChange={(e) => setLevel(e.target.value)} className="w-32" aria-label="Level">
          <option value="">All levels</option>
          <option value="WARNING">Warnings</option>
          <option value="ERROR">Errors</option>
        </Select>
        <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Filter text…" className="w-full sm:w-56" aria-label="Filter log text" />
        <Button size="sm" variant="ghost" onClick={() => logs.refetch()} loading={logs.isFetching}>
          <RefreshCw className="h-3.5 w-3.5" /> Refresh
        </Button>
      </Toolbar>
      {logs.isError ? (
        <ErrorState error={logs.error} />
      ) : (
        <pre className="scroll-thin max-h-[420px] overflow-auto whitespace-pre-wrap break-all bg-bg p-3 font-mono text-2xs leading-5">
          {logs.data?.lines.length ? logs.data.lines.join("\n") : logs.isLoading ? "Loading…" : "No log lines yet."}
        </pre>
      )}
    </Card>
  );
}

export default function SystemPage() {
  const { can } = useSession();
  const qc = useQueryClient();
  const status = useQuery({ queryKey: ["system-status"], queryFn: () => api<SystemStatus>("/system/status"), refetchInterval: 15_000 });
  const run = useMutation({
    mutationFn: () => api<Record<string, number>>("/system/collection/run", { method: "POST" }),
    onSuccess: (r) => {
      toast.success(`Collection run: ${Object.entries(r).map(([k, v]) => `${v} ${k}`).join(", ")}`);
      qc.invalidateQueries();
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const s = status.data;
  const fetchTotal = s ? Object.values(s.fetches_24h).reduce((a, b) => a + b, 0) : 0;
  const fetchErrors = s ? (s.fetches_24h.error ?? 0) + (s.fetches_24h.blocked ?? 0) : 0;

  return (
    <div className="mx-auto max-w-7xl">
      <PageHeader
        title="System status"
        description={s ? `Samyabadi ${s.version} · ${s.environment} · Python ${s.python}` : "Worker, scheduler, database, storage and backups"}
        actions={
          can(PERMS.sourcesWrite) ? (
            <Button variant="primary" loading={run.isPending} onClick={() => run.mutate()} title="Fetch every source that is due now">
              <Play className="h-4 w-4" /> Collect due sources now
            </Button>
          ) : null
        }
      />
      {status.isError ? <ErrorState error={status.error} onRetry={() => status.refetch()} /> : null}
      {s && s.worker.state !== "running" ? (
        <InlineAlert tone="danger" className="mb-4" title={s.worker.state === "stale" ? "Worker heartbeat is stale" : "Worker is not running"}>
          Scheduled collection, publishing and backups are paused. Start it with <code className="font-mono">python -m app.worker</code> or the{" "}
          <code className="font-mono">worker</code> container.
          {s.worker.heartbeat_at ? (
            <>
              {" "}
              Last heartbeat <Time value={s.worker.heartbeat_at} />.
            </>
          ) : null}
        </InlineAlert>
      ) : null}

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat
          label="Worker"
          value={s ? <StatusBadge status={s.worker.state === "running" ? "running" : s.worker.state === "stale" ? "degraded" : "failed"} label={s.worker.state.replace("_", " ")} /> : "—"}
          hint={s?.worker.heartbeat_at ? <>Heartbeat <Time value={s.worker.heartbeat_at} /></> : "No heartbeat recorded"}
          icon={Cpu}
        />
        <Stat
          label="Database"
          value={<span className="text-base">{s?.database.dialect ?? "—"}</span>}
          hint={s ? `Migration ${s.database.alembic_revision ?? "not stamped"}` : undefined}
          icon={Database}
        />
        <Stat
          label="Fetches (24 h)"
          value={formatNumber(fetchTotal)}
          hint={s ? `${formatNumber(fetchErrors)} failed · ${s.sources.failing} failing sources` : undefined}
          tone={fetchErrors ? "warning" : undefined}
          icon={RefreshCw}
        />
        <Stat
          label="Disk free"
          value={formatBytes(s?.storage.disk_free_bytes)}
          hint={s ? `Images ${formatBytes(s.storage.images_bytes)} · backups ${formatBytes(s.storage.backups_bytes)} · logs ${formatBytes(s.storage.logs_bytes)}` : undefined}
          icon={HardDrive}
        />
      </div>

      <Card className="mt-4">
        <CardHeader title="Scheduled jobs" description="Run by the worker process; each run is recorded with its outcome and duration." icon={CheckCircle2} />
        {status.isLoading ? (
          <SkeletonRows rows={5} />
        ) : !s?.jobs.length ? (
          <EmptyState title="No job runs recorded" description="Jobs appear after the worker has started at least once." />
        ) : (
          <Table>
            <THead>
              <tr>
                <TH>Job</TH>
                <TH>Every</TH>
                <TH>Last run</TH>
                <TH>Result</TH>
                <TH className="text-right">Duration</TH>
                <TH>Next run</TH>
                <TH className="text-right">Runs / failures</TH>
              </tr>
            </THead>
            <tbody>
              {s.jobs.map((j) => (
                <TR key={j.name}>
                  <TD>
                    <span className="block font-medium">{JOB_LABELS[j.name] ?? j.name}</span>
                    {j.last_error ? (
                      <span className="block max-w-[360px] truncate text-xs text-danger" title={j.last_error}>
                        {j.last_error}
                      </span>
                    ) : null}
                  </TD>
                  <TD className="text-muted">{formatInterval(j.interval_seconds)}</TD>
                  <TD className="text-muted">
                    <Time value={j.last_run_at} />
                  </TD>
                  <TD>{j.last_status ? <StatusBadge status={j.last_status} /> : "—"}</TD>
                  <TD className="tabular text-right">{formatDuration(j.last_duration_ms)}</TD>
                  <TD className="text-muted">
                    <Time value={j.next_run_at} />
                  </TD>
                  <TD className="tabular text-right">
                    {formatNumber(j.run_count)} / {formatNumber(j.failure_count)}
                  </TD>
                </TR>
              ))}
            </tbody>
          </Table>
        )}
      </Card>

      {s ? (
        <Card className="mt-4">
          <CardHeader title="Configuration" description="Read-only. Change these with environment variables (see .env.example) and restart." />
          <CardBody className="grid gap-2 text-sm sm:grid-cols-2 lg:grid-cols-3">
            {Object.entries(s.config).map(([k, v]) => (
              <div key={k} className="flex justify-between gap-3 border-b border-border/60 py-1">
                <span className="text-muted">{k.replace(/_/g, " ")}</span>
                <span className="tabular font-medium">{typeof v === "boolean" ? (v ? "yes" : "no") : String(v)}</span>
              </div>
            ))}
            <div className="flex justify-between gap-3 border-b border-border/60 py-1">
              <span className="text-muted">database version</span>
              <span className="truncate font-medium" title={s.database.version ?? undefined}>
                {s.database.version?.split(" on ")[0] ?? "—"}
              </span>
            </div>
          </CardBody>
        </Card>
      ) : null}

      {can(PERMS.systemManage) ? (
        <div className="mt-4 space-y-4">
          <Backups />
          <Logs />
        </div>
      ) : (
        <InlineAlert tone="info" className="mt-4">
          Backups and logs are available to administrators.
        </InlineAlert>
      )}
    </div>
  );
}
