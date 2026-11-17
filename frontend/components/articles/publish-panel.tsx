"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, CalendarClock, ExternalLink, FileText, RefreshCw, Send, Undo2 } from "lucide-react";
import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";

import { ChecksList, asChecks } from "@/components/articles/checks-list";
import { ACTION_LABEL, JobActions, JobStatus, useInvalidatePublishing } from "@/components/publishing/jobs";
import { KIND_ICON, KIND_LABEL, isSocial } from "@/components/publishing/kinds";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { useConfirm } from "@/components/ui/confirm";
import { Field, Input, Select, Textarea } from "@/components/ui/input";
import { InlineAlert, Spinner } from "@/components/ui/states";
import { StatusBadge } from "@/components/ui/status-badge";
import { Time } from "@/components/ui/time";
import { api, ApiError, errorMessage } from "@/lib/api";
import { PERMS, useSession } from "@/lib/session";
import type { ArticleOut, DestinationOut, JobOut, Page, PublicationCheck, PublicationOut } from "@/lib/types";
import { cn, formatDateTime, fromLocalInput, hostOf } from "@/lib/utils";

type Mode = "immediate" | "draft" | "scheduled";
type FbFormat = "post" | "reel";
type Privacy = "public" | "unlisted" | "private";
type Choice = { on: boolean; format: FbFormat; privacy: Privacy };
type PublishManyResponse = { jobs: JobOut[]; duplicate_request: boolean; in_progress: string[]; article: ArticleOut };
type UnpublishResponse = { job: JobOut; duplicate_request: boolean; article: ArticleOut };

const PUBLISHABLE = new Set(["approved", "scheduled", "published", "unpublished"]);
const REMOVABLE = new Set(["published", "draft", "private", "processing"]);
const WEBSITE_KINDS = new Set(["builtin", "wordpress", "webhook"]);

function defaultScheduleValue(): string {
  const d = new Date(Date.now() + 60 * 60 * 1000);
  d.setMinutes(0, 0, 0);
  const pad = (x: number) => String(x).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function defaultCaption(a: ArticleOut): string {
  const summary = (a.excerpt || a.seo_description || "").trim();
  return summary && summary !== a.headline.trim() ? `${a.headline.trim()}\n\n${summary}` : a.headline.trim();
}

/** Why a destination cannot be ticked for this article (null when it can). */
function unavailable(d: DestinationOut, article: ArticleOut): string | null {
  if (isSocial(d.kind) && !d.connection?.connected) return "Not connected yet";
  if (d.kind === "youtube" && !article.video_id) return "Add a video to the article first";
  return null;
}

function reportResults(res: PublishManyResponse, mode: Mode) {
  if (res.duplicate_request) {
    toast.info("This exact request was already made. Nothing new was sent.");
    return;
  }
  const scheduled = res.jobs.filter((j) => j.status === "pending" && j.scheduled_for && j.attempts === 0);
  if (mode === "scheduled" && scheduled.length) {
    toast.success(`Scheduled for ${formatDateTime(scheduled[0].scheduled_for)}: ${scheduled.map((j) => j.destination_name).join(", ")}`);
  }
  for (const j of res.jobs) {
    if (mode === "scheduled" && j.scheduled_for) continue;
    const name = j.destination_name ?? "destination";
    if (j.status === "succeeded") {
      toast.success(j.action === "draft" ? `Sent to ${name} as a draft` : `Published to ${name}`);
    } else if (j.status === "failed") {
      toast.error(`${name}: ${j.last_error ?? "failed"}`, { duration: 15_000 });
    } else if (res.in_progress.includes(name)) {
      toast.info(
        j.destination_kind === "youtube" || (j.options as { format?: string } | null)?.format === "reel"
          ? `Uploading the video to ${name}. This can take a few minutes; the status below updates by itself.`
          : `${name} will post as soon as your website has the article, so it can link to it.`,
        { duration: 10_000 },
      );
    } else if (j.status === "pending") {
      toast.warning(`${name} did not accept it yet; NEWSWIRE will retry. ${j.last_error ?? ""}`);
    }
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
  const [choices, setChoices] = useState<Record<string, Choice>>({});
  const [mode, setMode] = useState<Mode>("immediate");
  const [when, setWhen] = useState<string>(defaultScheduleValue);
  const [caption, setCaption] = useState<string>("");
  const [blocked, setBlocked] = useState<PublicationCheck[] | null>(null);
  const initialised = useRef(false);

  // Start with the user's website ticked (or the built-in site when there is none); social is opt-in.
  useEffect(() => {
    if (initialised.current || !active.length) return;
    initialised.current = true;
    const site = active.find((d) => d.kind === "wordpress" || d.kind === "webhook") ?? active.find((d) => d.kind === "builtin");
    if (site) setChoices({ [site.id]: { on: true, format: "post", privacy: "public" } });
  }, [active]);

  const choice = (id: string): Choice => choices[id] ?? { on: false, format: "post", privacy: "public" };
  const update = (id: string, patch: Partial<Choice>) => setChoices((c) => ({ ...c, [id]: { ...choice(id), ...patch } }));

  const video = article.video ?? null;
  const reelProblems: string[] = video ? (video.reel_problems ?? []) : ["Add a vertical video (3–90 seconds) to post a Reel."];
  const selected = active.filter((d) => choice(d.id).on && !unavailable(d, article));
  const hasFacebook = selected.some((d) => d.kind === "facebook");
  const hasSocial = selected.some((d) => isSocial(d.kind));
  const badReel = selected.some((d) => d.kind === "facebook" && choice(d.id).format === "reel" && reelProblems.length > 0);
  const strictest = selected.find((d) => d.is_commercial) ?? selected[0] ?? null;

  useEffect(() => {
    if (hasFacebook && mode === "draft") setMode("immediate");
  }, [hasFacebook, mode]);

  const checks = useQuery({
    queryKey: ["article-checks", article.id, strictest?.id ?? "", article.version, article.status, article.updated_at],
    queryFn: () => api<PublicationCheck[]>(`/articles/${article.id}/checks`, { query: { destination_id: strictest?.id } }),
    enabled: !!strictest,
  });
  const jobs = useQuery({
    queryKey: ["publishing-jobs", { article_id: article.id }],
    queryFn: () => api<Page<JobOut>>("/publishing/jobs", { query: { article_id: article.id, page_size: 8 } }),
    refetchInterval: (q) => {
      const items = q.state.data?.items ?? [];
      const busy = items.some((j) => j.status === "running" || (j.status === "pending" && !(j.scheduled_for && new Date(j.scheduled_for).getTime() > Date.now())));
      return busy ? 4_000 : items.some((j) => j.status === "pending") ? 30_000 : false;
    },
  });
  // When a background upload finishes, refresh the article so its publications list is current.
  const finished = (jobs.data?.items ?? []).filter((j) => j.status === "succeeded" || j.status === "failed").map((j) => j.id).join(",");
  const lastFinished = useRef<string | null>(null);
  useEffect(() => {
    if (lastFinished.current !== null && lastFinished.current !== finished) {
      qc.invalidateQueries({ queryKey: ["article", article.id] });
    }
    lastFinished.current = finished;
  }, [finished, qc, article.id]);

  const publish = useMutation({
    mutationFn: (body: unknown) => api<PublishManyResponse>(`/articles/${article.id}/publish-many`, { body }),
    onSuccess: (res, body) => {
      setBlocked(null);
      onArticle(res.article);
      reportResults(res, (body as { mode: Mode }).mode);
      invalidate();
    },
    onError: (e) => {
      if (e instanceof ApiError && e.code === "checks_failed") {
        setBlocked(asChecks(e.details));
        toast.error(e.message);
      } else toast.error(errorMessage(e), { duration: 12_000 });
    },
  });
  const unpublish = useMutation({
    mutationFn: (p: PublicationOut) => api<UnpublishResponse>(`/articles/${article.id}/unpublish`, { body: { destination_id: p.destination_id } }),
    onSuccess: (res, p) => {
      onArticle(res.article);
      if (res.duplicate_request) toast.info("This was already requested.");
      else if (res.job.status === "succeeded") toast.success(`Removed from ${p.destination_name}`);
      else if (res.job.status === "failed") toast.error(`${p.destination_name}: ${res.job.last_error ?? "failed"}`);
      else toast.warning(`${p.destination_name} did not respond yet; NEWSWIRE will retry.`);
      invalidate();
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const sync = useMutation({
    mutationFn: (p: PublicationOut) => api<PublicationOut>(`/publishing/publications/${p.id}/sync`, { method: "POST" }),
    onSuccess: (p) => {
      if (p.sync_error && p.remote_status !== "failed") toast.warning(`Status check failed: ${p.sync_error}`);
      else toast.success(`${p.destination_name}: ${p.remote_status}`);
      qc.invalidateQueries({ queryKey: ["article", article.id] });
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  const statusOk = PUBLISHABLE.has(article.status) || (mode === "draft" && article.status === "in_review");
  const errors = (checks.data ?? []).filter((c) => c.level === "error");
  const scheduledIso = mode === "scheduled" ? fromLocalInput(when) : null;
  const reason = !canPublish
    ? "You do not have permission to publish."
    : dirty
      ? "Save your changes first; only saved versions can be published."
      : !statusOk
        ? mode === "draft"
          ? "Submit the article for review before sending a draft."
          : "An editor must approve the article before it can be published."
        : !selected.length
          ? "Tick at least one place to publish to."
          : badReel
            ? "This video cannot be a Reel. Choose Post, or change the video."
            : errors.length && mode !== "draft"
              ? "Resolve the blocking checks below."
              : mode === "scheduled" && !(scheduledIso && new Date(scheduledIso).getTime() > Date.now() + 30_000)
                ? "Pick a time in the future."
                : null;

  const describe = (d: DestinationOut) => {
    const c = choice(d.id);
    if (d.kind === "facebook") return `${d.name} (Facebook ${c.format === "reel" ? "Reel" : "post"})`;
    if (d.kind === "youtube") return `${d.name} (YouTube, ${mode === "draft" ? "private draft" : c.privacy})`;
    return d.kind !== "builtin" && d.base_url ? `${d.name} (${hostOf(d.base_url)})` : d.name;
  };

  const submit = async () => {
    const verb = mode === "draft" ? "Send drafts" : mode === "scheduled" ? "Schedule" : "Publish now";
    const ok = await confirm({
      title: `${verb}: ${selected.length === 1 ? selected[0].name : `${selected.length} places`}?`,
      body: (
        <div className="flex flex-col gap-2">
          <p>
            Version {article.version} of “{article.headline}”{" "}
            {mode === "scheduled"
              ? `goes out at ${formatDateTime(scheduledIso)}.`
              : mode === "draft"
                ? "is sent as an unpublished draft."
                : "goes live now."}
          </p>
          <ul className="list-disc pl-5">
            {selected.map((d) => (
              <li key={d.id}>{describe(d)}</li>
            ))}
          </ul>
          {hasSocial && selected.some((d) => d.kind === "wordpress" || d.kind === "webhook") ? (
            <p className="text-xs text-muted">Your website goes first; Facebook and YouTube then link to the article there.</p>
          ) : null}
        </div>
      ),
      confirmLabel: mode === "draft" ? "Send drafts" : mode === "scheduled" ? "Schedule" : "Publish",
    });
    if (!ok) return;
    const message = caption.trim() && caption.trim() !== defaultCaption(article) ? caption.trim() : null;
    publish.mutate({
      mode,
      scheduled_for: scheduledIso,
      targets: selected.map((d) => ({
        destination_id: d.id,
        options:
          d.kind === "facebook"
            ? { format: choice(d.id).format, message }
            : d.kind === "youtube"
              ? { privacy: choice(d.id).privacy, message }
              : {},
      })),
    });
  };

  const pubs = article.publications ?? [];
  const removeText = (p: PublicationOut) =>
    p.destination_kind === "wordpress"
      ? "The WordPress post is switched back to draft. It is not deleted."
      : p.destination_kind === "webhook"
        ? "An article.unpublished event is sent to your website."
        : p.destination_kind === "facebook"
          ? "The post is deleted from the Facebook Page. This cannot be undone; publishing again creates a new post."
          : p.destination_kind === "youtube"
            ? "The YouTube video is made private. Publishing again makes it visible again."
            : "The article disappears from the public API and RSS feed.";

  return (
    <Card>
      <CardHeader title="Publish" icon={Send} description="Choose where, then when. Nothing is sent until you confirm." />
      <CardBody className="flex flex-col gap-4">
        {pubs.length ? (
          <section aria-label="Where it is published" className="flex flex-col gap-2">
            {article.has_unpublished_changes ? (
              <InlineAlert tone="info" title="Saved changes are not live yet">
                The live version is older than v{article.version}. Publish again to update each place.
              </InlineAlert>
            ) : null}
            <ul className="flex flex-col divide-y divide-border/70 rounded-lg border border-border">
              {pubs.map((p) => {
                const Icon = KIND_ICON[p.destination_kind ?? ""] ?? FileText;
                return (
                  <li key={p.id} className="flex flex-col gap-1.5 px-3 py-2">
                    <div className="flex items-center gap-2">
                      <Icon className="h-4 w-4 shrink-0 text-muted" />
                      <span className="min-w-0 flex-1 truncate text-sm font-medium">{p.destination_name ?? "Destination"}</span>
                      <StatusBadge status={p.remote_status} />
                    </div>
                    <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted">
                      {p.published_version ? <span>v{p.published_version}</span> : null}
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
                    {p.remote_status === "processing" ? (
                      <p className="text-xs text-muted">{p.destination_kind === "youtube" ? "YouTube" : "Facebook"} is processing the video; this updates by itself.</p>
                    ) : null}
                    {p.warning ? (
                      <p className="flex items-start gap-1 text-xs text-warning">
                        <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden /> {p.warning}
                      </p>
                    ) : null}
                    {p.sync_error && p.remote_status !== "failed" ? <p className="text-xs text-danger">Last status check failed: {p.sync_error}</p> : null}
                    {canPublish ? (
                      <div className="flex flex-wrap gap-1">
                        {p.destination_kind && p.destination_kind !== "builtin" && p.destination_kind !== "webhook" ? (
                          <Button size="sm" variant="ghost" loading={sync.isPending && sync.variables?.id === p.id} onClick={() => sync.mutate(p)}>
                            <RefreshCw className="h-3.5 w-3.5" /> Check status
                          </Button>
                        ) : null}
                        {REMOVABLE.has(p.remote_status) ? (
                          <Button
                            size="sm"
                            variant="ghost"
                            className="text-danger"
                            loading={unpublish.isPending && unpublish.variables?.id === p.id}
                            onClick={async () => {
                              const ok = await confirm({
                                title: `Remove from ${p.destination_name}?`,
                                body: removeText(p),
                                confirmLabel: p.destination_kind === "facebook" ? "Delete post" : "Unpublish",
                                destructive: true,
                              });
                              if (ok) unpublish.mutate(p);
                            }}
                          >
                            <Undo2 className="h-3.5 w-3.5" /> {p.destination_kind === "facebook" ? "Delete post" : "Unpublish"}
                          </Button>
                        ) : null}
                      </div>
                    ) : null}
                  </li>
                );
              })}
            </ul>
          </section>
        ) : null}

        {canPublish ? (
          <section aria-label="Publish" className="flex flex-col gap-3">
            {destinations.isLoading ? (
              <Spinner label="Loading destinations" />
            ) : !active.length ? (
              <InlineAlert tone="warning" title="Nowhere to publish yet">
                <Link href="/publishing?tab=destinations" className="underline">
                  Add your website, Facebook Page or YouTube channel
                </Link>{" "}
                first.
              </InlineAlert>
            ) : (
              <>
                <fieldset className="flex flex-col gap-1.5">
                  <legend className="mb-1 text-xs font-medium">Where</legend>
                  {active.map((d) => {
                    const Icon = KIND_ICON[d.kind] ?? FileText;
                    const why = unavailable(d, article);
                    const c = choice(d.id);
                    const on = c.on && !why;
                    return (
                      <div key={d.id} className={cn("rounded-lg border p-2", on ? "border-accent" : "border-border")}>
                        <label className={cn("flex items-center gap-2 text-sm", why ? "cursor-not-allowed opacity-70" : "cursor-pointer")}>
                          <input
                            type="checkbox"
                            checked={on}
                            disabled={!!why}
                            onChange={(e) => update(d.id, { on: e.target.checked })}
                            className="h-4 w-4 accent-[rgb(var(--accent))]"
                            data-testid={`dest-${d.kind}`}
                          />
                          <Icon className="h-4 w-4 shrink-0 text-muted" />
                          <span className="min-w-0 flex-1 truncate">
                            <span className="font-medium">{d.name}</span>
                            <span className="text-xs text-muted">
                              {" "}
                              · {d.connection?.account_name ?? (d.kind !== "builtin" && d.base_url ? hostOf(d.base_url) : KIND_LABEL[d.kind] ?? d.kind)}
                            </span>
                          </span>
                        </label>
                        {why ? (
                          <p className="ml-6 mt-1 text-xs text-muted">
                            {why}
                            {isSocial(d.kind) && !d.connection?.connected ? (
                              <>
                                {" "}
                                ·{" "}
                                <Link href="/publishing?tab=destinations" className="text-accent hover:underline">
                                  Connect
                                </Link>
                              </>
                            ) : null}
                          </p>
                        ) : null}
                        {on && d.kind === "facebook" ? (
                          <div className="ml-6 mt-2 flex flex-col gap-1">
                            <div role="radiogroup" aria-label={`${d.name} format`} className="inline-flex w-fit rounded-md border border-border p-0.5">
                              {(["post", "reel"] as FbFormat[]).map((f) => (
                                <button
                                  key={f}
                                  type="button"
                                  role="radio"
                                  aria-checked={c.format === f}
                                  onClick={() => update(d.id, { format: f })}
                                  className={cn(
                                    "rounded px-3 py-1 text-xs",
                                    c.format === f ? "bg-accent text-accent-fg" : "text-muted hover:text-text",
                                  )}
                                >
                                  {f === "post" ? "Post" : "Reel"}
                                </button>
                              ))}
                            </div>
                            <p className={cn("text-xs", c.format === "reel" && reelProblems.length ? "text-danger" : "text-muted")}>
                              {c.format === "post"
                                ? "A post with the link to your article (or the featured image when there is no website link)."
                                : reelProblems.length
                                  ? reelProblems.join(" ")
                                  : "The article's video as a Reel, with the caption below."}
                            </p>
                          </div>
                        ) : null}
                        {on && d.kind === "youtube" ? (
                          <div className="ml-6 mt-2 flex flex-wrap items-center gap-2">
                            <label htmlFor={`yt-privacy-${d.id}`} className="text-xs text-muted">
                              Visibility
                            </label>
                            <Select
                              id={`yt-privacy-${d.id}`}
                              value={mode === "draft" ? "private" : c.privacy}
                              disabled={mode === "draft"}
                              onChange={(e) => update(d.id, { privacy: e.target.value as Privacy })}
                              className="w-auto"
                            >
                              <option value="public">Public</option>
                              <option value="unlisted">Unlisted (only people with the link)</option>
                              <option value="private">Private</option>
                            </Select>
                            {video?.is_short ? <Badge tone="info">Short</Badge> : null}
                          </div>
                        ) : null}
                      </div>
                    );
                  })}
                </fieldset>

                {hasSocial ? (
                  <Field
                    label="Caption for Facebook and YouTube"
                    htmlFor="pub-caption"
                    hint="Leave empty to use the headline and excerpt. The link to your article is added automatically."
                  >
                    <Textarea
                      id="pub-caption"
                      rows={3}
                      value={caption}
                      maxLength={5000}
                      placeholder={defaultCaption(article)}
                      onChange={(e) => setCaption(e.target.value)}
                    />
                  </Field>
                ) : null}

                <fieldset className="flex flex-col gap-1.5">
                  <legend className="mb-1 text-xs font-medium">When</legend>
                  {(
                    [
                      ["immediate", "Now"],
                      ["scheduled", "At a set time"],
                      ["draft", "Save as draft (not public)"],
                    ] as [Mode, string][]
                  ).map(([m, label]) => {
                    const off = m === "draft" && hasFacebook;
                    return (
                      <label key={m} className={cn("inline-flex items-center gap-2 text-sm", off ? "cursor-not-allowed text-muted" : "cursor-pointer")}>
                        <input
                          type="radio"
                          name="pub-mode"
                          value={m}
                          checked={mode === m}
                          disabled={off}
                          onChange={() => setMode(m)}
                          className="accent-[rgb(var(--accent))]"
                        />
                        {label}
                        {off ? <span className="text-xs">(Facebook has no drafts)</span> : null}
                      </label>
                    );
                  })}
                </fieldset>
                {mode === "scheduled" ? (
                  <Field label="Publication time" htmlFor="pub-when" hint="Your local time. NEWSWIRE must be running then; it publishes within a minute of this time.">
                    <Input id="pub-when" type="datetime-local" value={when} onChange={(e) => setWhen(e.target.value)} />
                  </Field>
                ) : null}

                {strictest ? (
                  <div>
                    <p className="mb-1.5 text-xs font-medium">Checks</p>
                    {checks.isLoading ? (
                      <Spinner label="Checking" />
                    ) : (
                      <ChecksList checks={(checks.data ?? []).filter((c) => c.level !== "info")} compact emptyText="Ready to publish." />
                    )}
                    {mode === "draft" && errors.some((c) => c.code === "body_too_short" || c.code === "template_markers") ? (
                      <p className="mt-1 text-xs text-muted">Drafts may still contain template markers or short text; licence errors always block.</p>
                    ) : null}
                  </div>
                ) : null}
                {blocked?.length ? (
                  <InlineAlert tone="danger" title="The server refused to publish">
                    <ChecksList checks={blocked} compact />
                  </InlineAlert>
                ) : null}
                {reason ? <p className="text-xs text-muted">{reason}</p> : null}
                <Button variant="primary" disabled={!!reason} loading={publish.isPending} onClick={submit} data-testid="publish-submit">
                  {mode === "scheduled" ? <CalendarClock className="h-4 w-4" /> : <Send className="h-4 w-4" />}
                  {mode === "draft" ? "Save drafts" : mode === "scheduled" ? "Schedule" : "Publish"}
                  {selected.length > 1 ? ` to ${selected.length} places` : selected.length === 1 ? ` to ${selected[0].name}` : ""}
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
              <p className="text-xs font-medium">Recent activity</p>
              <Link href="/publishing" className="text-xs text-accent hover:underline">
                Queue
              </Link>
            </div>
            <ul className="flex flex-col divide-y divide-border/70 rounded-lg border border-border">
              {jobs.data.items.map((j) => {
                const Icon = KIND_ICON[j.destination_kind ?? ""] ?? FileText;
                return (
                  <li key={j.id} className="flex flex-col gap-1 px-3 py-2">
                    <div className="flex items-center gap-2 text-xs">
                      <JobStatus job={j} />
                      <Icon className="h-3.5 w-3.5 shrink-0 text-muted" />
                      <span className="min-w-0 flex-1 truncate">
                        {ACTION_LABEL[j.action] ?? j.action} · {j.destination_name}
                        {(j.options as { format?: string } | null)?.format === "reel" ? " (Reel)" : ""} · v{j.article_version}
                      </span>
                    </div>
                    <div className="flex items-center gap-2 text-2xs text-muted">
                      <span className="flex-1">
                        {j.status === "pending" && j.scheduled_for && new Date(j.scheduled_for).getTime() > Date.now() ? (
                          <>for {formatDateTime(j.scheduled_for)}</>
                        ) : j.status === "pending" && j.next_attempt_at ? (
                          <>
                            next attempt <Time value={j.next_attempt_at} />
                          </>
                        ) : (
                          <Time value={j.completed_at ?? j.created_at} />
                        )}
                      </span>
                      <JobActions job={j} canAct={canPublish} />
                    </div>
                    {j.last_error ? (
                      <p className="line-clamp-3 text-2xs text-danger" title={j.last_error}>
                        {j.last_error}
                      </p>
                    ) : null}
                  </li>
                );
              })}
            </ul>
          </section>
        ) : null}
        {!canPublish && pubs.length ? <Badge tone="neutral">Read-only: publishing requires the editor role</Badge> : null}
      </CardBody>
    </Card>
  );
}
