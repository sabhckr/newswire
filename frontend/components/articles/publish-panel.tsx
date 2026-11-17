"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CalendarClock, ExternalLink, Globe, RefreshCw, Send, Undo2 } from "lucide-react";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import { ChecksList, asChecks } from "@/components/articles/checks-list";
import { ACTION_LABEL, JobActions, JobStatus, useInvalidatePublishing } from "@/components/publishing/jobs";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { useConfirm } from "@/components/ui/confirm";
import { Field, Input, Select } from "@/components/ui/input";
import { InlineAlert, Spinner } from "@/components/ui/states";
import { StatusBadge } from "@/components/ui/status-badge";
import { Time } from "@/components/ui/time";
import { api, ApiError, errorMessage } from "@/lib/api";
import { PERMS, useSession } from "@/lib/session";
import type { ArticleOut, DestinationOut, JobOut, Page, PublicationCheck, PublicationOut } from "@/lib/types";
import { formatDateTime, fromLocalInput, hostOf } from "@/lib/utils";

type Mode = "immediate" | "draft" | "scheduled";
type PublishResponse = { job: JobOut; duplicate_request: boolean; article: ArticleOut };

const PUBLISHABLE = new Set(["approved", "scheduled", "published", "unpublished"]);

function defaultScheduleValue(): string {
  const d = new Date(Date.now() + 60 * 60 * 1000);
  d.setMinutes(0, 0, 0);
  const pad = (x: number) => String(x).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function reportJob(res: PublishResponse, dest: DestinationOut, action: "publish" | "unpublish") {
  const { job } = res;
  if (res.duplicate_request) {
    toast.info(`This exact request was already made (${job.status}). Nothing new was sent.`);
    return;
  }
  if (job.status === "succeeded") {
    toast.success(action === "unpublish" ? `Unpublished from ${dest.name}` : job.action === "draft" ? `Sent to ${dest.name} as a draft` : `Published to ${dest.name}`);
  } else if (job.status === "pending" && job.scheduled_for && job.attempts === 0) {
    toast.success(`Scheduled for ${formatDateTime(job.scheduled_for)} at ${dest.name}`);
  } else if (job.status === "pending") {
    toast.warning(`${dest.name} did not accept it yet; NEWSWIRE will retry. ${job.last_error ?? ""}`);
  } else if (job.status === "failed") {
    toast.error(`Failed: ${job.last_error ?? "unknown error"}`);
  }
}

export function PublishPanel({
  article,
  dirty,
  onArticle,
}: {
  article: ArticleOut;
  dirty: boolean;
  onArticle: (a: ArticleOut) => void;
}) {
  const { can } = useSession();
  const confirm = useConfirm();
  const qc = useQueryClient();
  const invalidate = useInvalidatePublishing();
  const canPublish = can(PERMS.articlesPublish);

  const destinations = useQuery({
    queryKey: ["publishing-destinations"],
    queryFn: () => api<DestinationOut[]>("/publishing/destinations"),
  });
  const active = useMemo(() => (destinations.data ?? []).filter((d) => d.is_active), [destinations.data]);
  const [destId, setDestId] = useState<string>("");
  const [mode, setMode] = useState<Mode>("immediate");
  const [when, setWhen] = useState<string>(defaultScheduleValue);
  const [blocked, setBlocked] = useState<PublicationCheck[] | null>(null);

  useEffect(() => {
    if (!destId && active.length) setDestId((active.find((d) => d.kind === "builtin") ?? active[0]).id);
  }, [active, destId]);
  const dest = active.find((d) => d.id === destId) ?? null;

  const checks = useQuery({
    queryKey: ["article-checks", article.id, destId, article.version, article.status, article.updated_at],
    queryFn: () => api<PublicationCheck[]>(`/articles/${article.id}/checks`, { query: { destination_id: destId } }),
    enabled: !!destId,
  });
  const jobs = useQuery({
    queryKey: ["publishing-jobs", { article_id: article.id }],
    queryFn: () => api<Page<JobOut>>("/publishing/jobs", { query: { article_id: article.id, page_size: 6 } }),
    refetchInterval: (q) => ((q.state.data?.items ?? []).some((j) => j.status === "pending" || j.status === "running") ? 15_000 : false),
  });

  const publish = useMutation({
    mutationFn: (body: { destination_id: string; mode: Mode; scheduled_for: string | null }) =>
      api<PublishResponse>(`/articles/${article.id}/publish`, { body }),
    onSuccess: (res) => {
      setBlocked(null);
      onArticle(res.article);
      if (dest) reportJob(res, dest, "publish");
      invalidate();
    },
    onError: (e) => {
      if (e instanceof ApiError && e.code === "checks_failed") {
        setBlocked(asChecks(e.details));
        toast.error(e.message);
      } else toast.error(errorMessage(e));
    },
  });
  const unpublish = useMutation({
    mutationFn: (p: PublicationOut) => api<PublishResponse>(`/articles/${article.id}/unpublish`, { body: { destination_id: p.destination_id } }),
    onSuccess: (res, p) => {
      onArticle(res.article);
      const d = destinations.data?.find((x) => x.id === p.destination_id);
      if (d) reportJob(res, d, "unpublish");
      invalidate();
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const sync = useMutation({
    mutationFn: (p: PublicationOut) => api<PublicationOut>(`/publishing/publications/${p.id}/sync`, { method: "POST" }),
    onSuccess: (p) => {
      if (p.sync_error) toast.warning(`Status check failed: ${p.sync_error}`);
      else toast.success(`Remote status: ${p.remote_status}`);
      qc.invalidateQueries({ queryKey: ["article", article.id] });
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  const statusOk = PUBLISHABLE.has(article.status) || (mode === "draft" && article.status === "in_review");
  const errors = (checks.data ?? []).filter((c) => c.level === "error");
  const reason = !canPublish
    ? "You do not have permission to publish."
    : dirty
      ? "Save your changes first; only saved versions can be published."
      : !statusOk
        ? mode === "draft"
          ? "Submit the article for review before sending a draft."
          : "An editor must approve the article before it can be published."
        : !dest
          ? "Choose a destination."
          : errors.length && mode !== "draft"
            ? "Resolve the blocking checks below."
            : mode === "scheduled" && !(fromLocalInput(when) && new Date(when).getTime() > Date.now())
              ? "Pick a time in the future."
              : null;

  const submit = async () => {
    if (!dest) return;
    const scheduled = mode === "scheduled" ? fromLocalInput(when) : null;
    const verb = mode === "draft" ? "Send a draft" : mode === "scheduled" ? "Schedule publication" : "Publish now";
    const ok = await confirm({
      title: `${verb} to ${dest.name}?`,
      body: (
        <>
          Version {article.version} of “{article.headline}”{" "}
          {mode === "scheduled" ? `will go live at ${formatDateTime(scheduled)}.` : mode === "draft" ? "will be sent as an unpublished draft." : "will go live immediately."}
          {dest.kind !== "builtin" && dest.base_url ? <> Destination: {hostOf(dest.base_url)}.</> : null}
        </>
      ),
      confirmLabel: mode === "draft" ? "Send draft" : mode === "scheduled" ? "Schedule" : "Publish",
    });
    if (ok) publish.mutate({ destination_id: dest.id, mode, scheduled_for: scheduled });
  };

  const pubs = article.publications ?? [];

  return (
    <Card>
      <CardHeader title="Publishing" icon={Send} description="Explicit approval is always required; nothing is sent until you confirm." />
      <CardBody className="flex flex-col gap-4">
        {pubs.length ? (
          <section aria-label="Publications" className="flex flex-col gap-2">
            {article.has_unpublished_changes ? (
              <InlineAlert tone="info" title="Saved changes are not live yet">
                The live version is older than v{article.version}. Publish again to update each destination.
              </InlineAlert>
            ) : null}
            <ul className="flex flex-col divide-y divide-border/70 rounded-lg border border-border">
              {pubs.map((p) => (
                <li key={p.id} className="flex flex-col gap-1.5 px-3 py-2">
                  <div className="flex items-center gap-2">
                    <Globe className="h-4 w-4 shrink-0 text-muted" aria-hidden />
                    <span className="min-w-0 flex-1 truncate text-sm font-medium">{p.destination_name ?? "Destination"}</span>
                    <StatusBadge status={p.remote_status} />
                  </div>
                  <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted">
                    {p.published_version ? <span>v{p.published_version} live</span> : null}
                    {p.last_synced_at ? (
                      <span>
                        checked <Time value={p.last_synced_at} />
                      </span>
                    ) : null}
                    {p.remote_url ? (
                      <a href={p.remote_url} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 text-accent hover:underline">
                        View <ExternalLink className="h-3 w-3" aria-hidden />
                      </a>
                    ) : null}
                  </div>
                  {p.sync_error ? <p className="text-xs text-danger">Last status check failed: {p.sync_error}</p> : null}
                  {canPublish ? (
                    <div className="flex flex-wrap gap-1">
                      {p.destination_kind === "wordpress" ? (
                        <Button size="sm" variant="ghost" loading={sync.isPending && sync.variables?.id === p.id} onClick={() => sync.mutate(p)}>
                          <RefreshCw className="h-3.5 w-3.5" /> Check status
                        </Button>
                      ) : null}
                      {p.remote_status === "published" || p.remote_status === "draft" ? (
                        <Button
                          size="sm"
                          variant="ghost"
                          className="text-danger"
                          loading={unpublish.isPending && unpublish.variables?.id === p.id}
                          onClick={async () => {
                            const ok = await confirm({
                              title: `Unpublish from ${p.destination_name}?`,
                              body:
                                p.destination_kind === "wordpress"
                                  ? "The WordPress post is switched back to draft. It is not deleted."
                                  : p.destination_kind === "webhook"
                                    ? "An article.unpublished event is sent to the webhook."
                                    : "The article disappears from the public API and RSS feed.",
                              confirmLabel: "Unpublish",
                              destructive: true,
                            });
                            if (ok) unpublish.mutate(p);
                          }}
                        >
                          <Undo2 className="h-3.5 w-3.5" /> Unpublish
                        </Button>
                      ) : null}
                    </div>
                  ) : null}
                </li>
              ))}
            </ul>
          </section>
        ) : null}

        {canPublish ? (
          <section aria-label="Publish" className="flex flex-col gap-3">
            {destinations.isLoading ? (
              <Spinner label="Loading destinations" />
            ) : !active.length ? (
              <InlineAlert tone="warning" title="No active destinations">
                <Link href="/publishing?tab=destinations" className="underline">
                  Configure a destination
                </Link>{" "}
                to publish.
              </InlineAlert>
            ) : (
              <>
                <Field label="Destination" htmlFor="pub-dest">
                  <Select id="pub-dest" value={destId} onChange={(e) => setDestId(e.target.value)}>
                    {active.map((d) => (
                      <option key={d.id} value={d.id}>
                        {d.name}
                        {d.kind === "builtin" ? " (built-in site)" : ` (${d.kind})`}
                        {d.is_commercial ? "" : " · non-commercial"}
                      </option>
                    ))}
                  </Select>
                </Field>
                <fieldset className="flex flex-col gap-1.5">
                  <legend className="mb-1 text-xs font-medium">When</legend>
                  {(
                    [
                      ["immediate", "Publish now"],
                      ["scheduled", "Schedule"],
                      ["draft", "Send as draft (not public)"],
                    ] as [Mode, string][]
                  ).map(([m, label]) => (
                    <label key={m} className="inline-flex cursor-pointer items-center gap-2 text-sm">
                      <input type="radio" name="pub-mode" value={m} checked={mode === m} onChange={() => setMode(m)} className="accent-[rgb(var(--accent))]" />
                      {label}
                    </label>
                  ))}
                </fieldset>
                {mode === "scheduled" ? (
                  <Field label="Publication time" htmlFor="pub-when" hint="Your local time. The worker publishes within a minute of this time.">
                    <Input id="pub-when" type="datetime-local" value={when} onChange={(e) => setWhen(e.target.value)} />
                  </Field>
                ) : null}

                <div>
                  <p className="mb-1.5 text-xs font-medium">Checks for {dest?.name ?? "this destination"}</p>
                  {checks.isLoading ? (
                    <Spinner label="Checking" />
                  ) : (
                    <ChecksList checks={(checks.data ?? []).filter((c) => c.level !== "info")} compact emptyText="Ready to publish." />
                  )}
                  {mode === "draft" && errors.some((c) => c.code === "body_too_short" || c.code === "template_markers") ? (
                    <p className="mt-1 text-xs text-muted">Drafts may still contain template markers or short text; licence errors always block.</p>
                  ) : null}
                </div>
                {blocked?.length ? (
                  <InlineAlert tone="danger" title="The server refused to publish">
                    <ChecksList checks={blocked} compact />
                  </InlineAlert>
                ) : null}
                {reason ? <p className="text-xs text-muted">{reason}</p> : null}
                <Button variant="primary" disabled={!!reason} loading={publish.isPending} onClick={submit}>
                  {mode === "scheduled" ? <CalendarClock className="h-4 w-4" /> : <Send className="h-4 w-4" />}
                  {mode === "draft" ? "Send draft" : mode === "scheduled" ? "Schedule" : pubs.some((p) => p.destination_id === destId && p.remote_status === "published") ? "Publish update" : "Publish"}
                </Button>
              </>
            )}
          </section>
        ) : !pubs.length ? (
          <p className="text-sm text-muted">Not published anywhere yet. An editor with publishing rights can publish approved articles.</p>
        ) : null}

        {jobs.data?.items.length ? (
          <section aria-label="Recent publishing jobs">
            <div className="mb-1.5 flex items-center justify-between">
              <p className="text-xs font-medium">Recent jobs</p>
              <Link href="/publishing" className="text-xs text-accent hover:underline">
                Queue
              </Link>
            </div>
            <ul className="flex flex-col divide-y divide-border/70 rounded-lg border border-border">
              {jobs.data.items.map((j) => (
                <li key={j.id} className="flex flex-col gap-1 px-3 py-2">
                  <div className="flex items-center gap-2 text-xs">
                    <JobStatus job={j} />
                    <span className="min-w-0 flex-1 truncate">
                      {ACTION_LABEL[j.action] ?? j.action} · {j.destination_name} · v{j.article_version}
                    </span>
                  </div>
                  <div className="flex items-center gap-2 text-2xs text-muted">
                    <span className="flex-1">
                      {j.status === "pending" && j.next_attempt_at ? (
                        <>
                          next attempt <Time value={j.next_attempt_at} />
                        </>
                      ) : (
                        <Time value={j.completed_at ?? j.created_at} />
                      )}
                    </span>
                    <JobActions job={j} canAct={canPublish} />
                  </div>
                  {j.last_error ? <p className="line-clamp-2 text-2xs text-danger" title={j.last_error}>{j.last_error}</p> : null}
                </li>
              ))}
            </ul>
          </section>
        ) : null}
        {!canPublish && pubs.length ? <Badge tone="neutral">Read-only: publishing requires the editor role</Badge> : null}
      </CardBody>
    </Card>
  );
}
