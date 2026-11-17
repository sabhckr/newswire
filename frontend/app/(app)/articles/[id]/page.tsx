"use client";

import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import type { Editor } from "@tiptap/react";
import {
  ArrowLeft,
  Eye,
  History,
  ImagePlus,
  Link2,
  MoreHorizontal,
  RefreshCw,
  Save,
  Search as SearchIcon,
  ShieldAlert,
  Tags,
  Target,
  Trash2,
  X,
} from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";

import { type Draft, diffDraft, REVIEW_FIELDS, sameDraft, toDraft } from "@/components/articles/article-draft";
import { ChecksList, asChecks } from "@/components/articles/checks-list";
import { ImagePickerDialog, Thumb, imageWarnings } from "@/components/articles/image-picker";
import { PublishPanel } from "@/components/articles/publish-panel";
import { RichEditor } from "@/components/articles/rich-editor";
import { SourcesEditor } from "@/components/articles/sources-editor";
import { TagInput } from "@/components/articles/tag-input";
import { VideoCard } from "@/components/articles/video-card";
import { WorkflowPanel } from "@/components/articles/workflow-panel";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { useConfirm } from "@/components/ui/confirm";
import { Dropdown, DropdownContent, DropdownItem, DropdownTrigger } from "@/components/ui/dropdown";
import { Field, Input, Select, Textarea } from "@/components/ui/input";
import { Kbd } from "@/components/ui/page";
import { EmptyState, ErrorState, InlineAlert, Skeleton, SkeletonRows } from "@/components/ui/states";
import { StatusBadge } from "@/components/ui/status-badge";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Time } from "@/components/ui/time";
import { api, ApiError, errorMessage } from "@/lib/api";
import { PERMS, useSession } from "@/lib/session";
import type { ArticleIn, ArticleOut, AssignmentOut, AuditOut, CategoryOut, ImageOut, TagOut, VideoOut } from "@/lib/types";
import { cn } from "@/lib/utils";

function Counter({ value, max }: { value: string; max: number }) {
  const n = value.length;
  return (
    <span className={cn("tabular", n > max ? "text-warning" : "text-muted")}>
      {n}/{max}
      {n > max ? " (long)" : ""}
    </span>
  );
}

export default function ArticleEditorPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const qc = useQueryClient();
  const confirm = useConfirm();
  const { can } = useSession();
  const canWrite = can(PERMS.articlesWrite);
  const canApprove = can(PERMS.articlesApprove);

  const article = useQuery({ queryKey: ["article", id], queryFn: () => api<ArticleOut>(`/articles/${id}`) });
  const categories = useQuery({ queryKey: ["categories"], queryFn: () => api<CategoryOut[]>("/categories") });
  const allTags = useQuery({ queryKey: ["article-tags"], queryFn: () => api<TagOut[]>("/articles/tags"), staleTime: 300_000 });

  // --- editor state ------------------------------------------------------------------------
  const [baseDraft, setBaseDraft] = useState<Draft | null>(null);
  const [baseVersion, setBaseVersion] = useState<number | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [editor, setEditor] = useState<Editor | null>(null);
  const [bodyDirty, setBodyDirty] = useState(false);
  const [conflict, setConflict] = useState(false);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [picked, setPicked] = useState<ImageOut | null>(null);
  const [uploadedVideo, setUploadedVideo] = useState<VideoOut | null>(null);
  const editCount = useRef(0);
  const initialHtml = useRef<string>("");

  const fieldsDirty = !!draft && !!baseDraft && !sameDraft(draft, baseDraft);
  const dirty = fieldsDirty || bodyDirty;
  const dirtyRef = useRef(dirty);
  dirtyRef.current = dirty;

  const adopt = useCallback(
    (a: ArticleOut) => {
      const d = toDraft(a);
      initialHtml.current = a.body_html ?? "";
      setBaseDraft(d);
      setDraft(d);
      setBaseVersion(a.version);
      setPicked(null);
      setUploadedVideo(null);
      editCount.current += 1;
      setBodyDirty(false);
      setConflict(false);
      editor?.commands.setContent(a.body_html ?? "", false);
    },
    [editor],
  );

  const status = article.data?.status;
  const readOnly = !canWrite || status === "archived";

  const save = useMutation({
    mutationFn: (v: { patch: ArticleIn; mark: number; draftAtSave: Draft; statusBefore: string }) =>
      api<ArticleOut>(`/articles/${id}`, { method: "PUT", body: v.patch }),
    onSuccess: (a, v) => {
      const nd = toDraft(a);
      setBaseDraft(nd);
      setBaseVersion(a.version);
      setDraft((cur) => (cur && sameDraft(cur, v.draftAtSave) ? nd : cur));
      if (editCount.current === v.mark) setBodyDirty(false);
      setConflict(false);
      qc.setQueryData(["article", id], a);
      qc.invalidateQueries({ queryKey: ["articles"] });
      qc.invalidateQueries({ queryKey: ["article-history", id] });
      if (v.patch.tags) qc.invalidateQueries({ queryKey: ["article-tags"] });
      if (v.statusBefore !== a.status && a.status === "in_review") {
        toast.warning(`Saved as v${a.version}. Because the text changed, the article went back to review.`);
      } else toast.success(`Saved v${a.version}`);
    },
    onError: (e) => {
      if (e instanceof ApiError && e.code === "version_conflict") {
        setConflict(true);
        toast.error("Someone else saved this article meanwhile. Your changes were not saved.");
      } else toast.error(errorMessage(e));
    },
  });

  // Initialise once, then follow newer server versions as long as nothing local is unsaved.
  useEffect(() => {
    const a = article.data;
    if (!a) return;
    if (baseVersion === null) adopt(a);
    else if (a.version !== baseVersion && !dirtyRef.current && !save.isPending) adopt(a);
  }, [article.data, baseVersion, adopt, save.isPending]);

  const headlineError = draft && !draft.headline.trim() ? "A headline is required" : undefined;

  const doSave = () => {
    if (!draft || !baseDraft || baseVersion === null || readOnly || save.isPending) return;
    if (!dirty) return;
    if (headlineError) {
      toast.error(headlineError);
      return;
    }
    const patch: ArticleIn = diffDraft(draft, baseDraft);
    if (bodyDirty && editor) patch.body_html = editor.getHTML();
    patch.version = baseVersion;
    save.mutate({ patch, mark: editCount.current, draftAtSave: draft, statusBefore: status ?? "" });
  };
  const saveRef = useRef(doSave);
  saveRef.current = doSave;

  const onEdit = useCallback(() => {
    editCount.current += 1;
    setBodyDirty(true);
  }, []);

  // Cmd/Ctrl+S
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && !e.shiftKey && !e.altKey && e.key.toLowerCase() === "s") {
        e.preventDefault();
        saveRef.current();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // Warn before leaving with unsaved changes: tab close/reload, and in-app link clicks.
  useEffect(() => {
    if (!dirty) return;
    const onUnload = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = "";
    };
    const onClick = (e: MouseEvent) => {
      if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
      const a = (e.target as HTMLElement | null)?.closest?.("a[href]") as HTMLAnchorElement | null;
      if (!a || a.target === "_blank" || a.hasAttribute("download")) return;
      const url = new URL(a.href, window.location.href);
      if (url.origin !== window.location.origin || url.pathname === window.location.pathname) return;
      if (!window.confirm("You have unsaved changes. Leave this page and discard them?")) {
        e.preventDefault();
        e.stopPropagation();
      }
    };
    window.addEventListener("beforeunload", onUnload);
    document.addEventListener("click", onClick, true);
    return () => {
      window.removeEventListener("beforeunload", onUnload);
      document.removeEventListener("click", onClick, true);
    };
  }, [dirty]);

  const set = <K extends keyof Draft>(k: K, v: Draft[K]) => setDraft((d) => (d ? { ...d, [k]: v } : d));
  const onArticle = useCallback((a: ArticleOut) => qc.setQueryData(["article", id], a), [qc, id]);

  const reloadLatest = async () => {
    if (dirty) {
      const ok = await confirm({
        title: "Discard your changes and load the latest version?",
        body: "Copy anything you want to keep first: your unsaved edits will be replaced.",
        confirmLabel: "Load latest",
        destructive: true,
      });
      if (!ok) return;
    }
    const r = await article.refetch();
    if (r.data) {
      adopt(r.data);
      toast.success(`Loaded v${r.data.version}`);
    }
  };

  const del = useMutation({
    mutationFn: () => api(`/articles/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      dirtyRef.current = false;
      toast.success("Article deleted");
      qc.invalidateQueries({ queryKey: ["articles"] });
      router.push("/articles");
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  const assignments = useQueries({
    queries: (article.data?.assignment_ids ?? []).map((aid) => ({
      queryKey: ["assignment", aid],
      queryFn: () => api<AssignmentOut>(`/assignments/${aid}`),
      staleTime: 60_000,
    })),
  });

  const featured = useMemo(() => {
    if (!draft?.featured_image_id) return null;
    if (picked?.id === draft.featured_image_id) return picked;
    const fi = article.data?.featured_image;
    return fi && fi.id === draft.featured_image_id ? fi : null;
  }, [draft?.featured_image_id, picked, article.data?.featured_image]);
  const video = useMemo(() => {
    if (!draft?.video_id) return null;
    if (uploadedVideo?.id === draft.video_id) return uploadedVideo;
    const v = article.data?.video;
    return v && v.id === draft.video_id ? v : null;
  }, [draft?.video_id, uploadedVideo, article.data?.video]);

  // --- render ------------------------------------------------------------------------------
  if (article.isLoading || (article.data && !draft)) {
    return (
      <div className="mx-auto max-w-7xl">
        <Skeleton className="mb-4 h-8 w-64" />
        <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_340px]">
          <SkeletonRows rows={10} />
          <SkeletonRows rows={6} />
        </div>
      </div>
    );
  }
  if (article.isError || !article.data || !draft || !baseDraft) {
    return (
      <div className="mx-auto max-w-3xl">
        <Link href="/articles" className="mb-3 inline-flex items-center gap-1 text-sm text-muted hover:text-accent">
          <ArrowLeft className="h-4 w-4" /> Articles
        </Link>
        <ErrorState error={article.error} onRetry={() => article.refetch()} />
      </div>
    );
  }

  const a = article.data;
  const newerOnServer = baseVersion !== null && a.version > baseVersion && dirty;
  const pendingReset =
    ["approved", "scheduled"].includes(a.status) &&
    !canApprove &&
    (bodyDirty || Object.keys(diffDraft(draft, baseDraft)).some((k) => REVIEW_FIELDS.has(k)));
  const checks = asChecks(a.checks);
  const tagSuggestions = (allTags.data ?? []).map((t) => t.name).filter((n) => !draft.tags.includes(n));
  const featuredWarnings = imageWarnings(featured);

  return (
    <div className="mx-auto max-w-7xl">
      {/* Title bar */}
      <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="min-w-0">
          <Link href="/articles" className="inline-flex items-center gap-1 text-xs text-muted hover:text-accent">
            <ArrowLeft className="h-3.5 w-3.5" /> Articles
          </Link>
          <div className="mt-1 flex min-w-0 items-center gap-2">
            <h1 className="truncate text-lg font-semibold tracking-tight">{draft.headline.trim() || "Untitled article"}</h1>
            <StatusBadge status={a.status} className="shrink-0" />
          </div>
          <p className="mt-0.5 flex flex-wrap items-center gap-x-2 text-xs text-muted" aria-live="polite">
            {save.isPending ? (
              <span>Saving…</span>
            ) : dirty ? (
              <span className="inline-flex items-center gap-1.5 font-medium text-warning">
                <span className="h-2 w-2 rounded-full bg-warning" aria-hidden /> Unsaved changes
              </span>
            ) : (
              <span>
                Saved v{a.version} · <Time value={a.updated_at} />
              </span>
            )}
            {a.author_name ? <span>· by {a.author_name}</span> : null}
            <span>· {a.word_count.toLocaleString()} words saved</span>
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button
            variant="outline"
            onClick={() => window.open(`/articles/${id}/preview`, "_blank", "noopener")}
            title={dirty ? "Preview shows the last saved version" : "Open the preview in a new tab"}
          >
            <Eye className="h-4 w-4" /> Preview
          </Button>
          {!readOnly ? (
            <Button variant="primary" onClick={doSave} disabled={!dirty || !!headlineError} loading={save.isPending}>
              <Save className="h-4 w-4" /> Save <Kbd>⌘S</Kbd>
            </Button>
          ) : null}
          {canWrite ? (
            <Dropdown>
              <DropdownTrigger asChild>
                <Button variant="ghost" size="icon" aria-label="More actions">
                  <MoreHorizontal className="h-4 w-4" />
                </Button>
              </DropdownTrigger>
              <DropdownContent>
                <DropdownItem icon={RefreshCw} onSelect={() => void reloadLatest()}>
                  Reload latest version
                </DropdownItem>
                <DropdownItem
                  icon={Trash2}
                  danger
                  disabled={a.status === "published" || a.status === "scheduled"}
                  onSelect={async () => {
                    const ok = await confirm({
                      title: "Delete this article?",
                      body: "It is removed from every list. Publishing history stays in the activity log.",
                      confirmLabel: "Delete",
                      destructive: true,
                    });
                    if (ok) del.mutate();
                  }}
                >
                  {a.status === "published" || a.status === "scheduled" ? "Delete (unpublish first)" : "Delete"}
                </DropdownItem>
              </DropdownContent>
            </Dropdown>
          ) : null}
        </div>
      </div>

      {conflict || newerOnServer ? (
        <InlineAlert tone="danger" title="This article was changed elsewhere" className="mb-4">
          <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
            <span>
              {conflict ? "Your save was rejected because" : "Heads up:"} a newer version (v{a.version}) exists on the server.
              Load it, then re-apply your edits.
            </span>
            <Button size="sm" variant="danger-outline" onClick={() => void reloadLatest()}>
              <RefreshCw className="h-3.5 w-3.5" /> Load latest
            </Button>
          </div>
        </InlineAlert>
      ) : null}
      {a.status === "archived" ? (
        <InlineAlert tone="info" title="Archived" className="mb-4">
          This article is read-only. Restore it to draft from the Workflow panel to edit it.
        </InlineAlert>
      ) : !canWrite ? (
        <InlineAlert tone="info" className="mb-4">
          You have read-only access to articles.
        </InlineAlert>
      ) : null}
      {pendingReset ? (
        <InlineAlert tone="warning" className="mb-4" title="Saving will send this article back to review">
          It is currently {a.status === "scheduled" ? "scheduled" : "approved"}. Changes to the headline, subtitle, body, video or
          featured image need an editor’s approval again{a.status === "scheduled" ? ", and the scheduled publication is cancelled" : ""}.
        </InlineAlert>
      ) : null}

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_340px]">
        {/* Main column */}
        <div className="flex min-w-0 flex-col gap-4">
          <Card>
            <CardBody className="flex flex-col gap-3">
              <Field label="Headline" htmlFor="a-headline" required error={headlineError}>
                <Textarea
                  id="a-headline"
                  rows={2}
                  value={draft.headline}
                  onChange={(e) => set("headline", e.target.value.replace(/\n/g, " "))}
                  disabled={readOnly}
                  aria-invalid={!!headlineError}
                  className="min-h-0 resize-none text-lg font-semibold leading-snug"
                  maxLength={500}
                />
              </Field>
              <Field label="Subtitle" htmlFor="a-subtitle" hint="Optional standfirst shown under the headline.">
                <Input id="a-subtitle" value={draft.subtitle} onChange={(e) => set("subtitle", e.target.value)} disabled={readOnly} maxLength={500} />
              </Field>
            </CardBody>
            <RichEditor key={id} initialHtml={initialHtml.current} onReady={setEditor} onEdit={onEdit} readOnly={readOnly} />
          </Card>

          <Card>
            <CardHeader title="Details" icon={Tags} />
            <CardBody className="grid gap-3 sm:grid-cols-2">
              <Field label="Category" htmlFor="a-category">
                <Select id="a-category" value={draft.category_id} onChange={(e) => set("category_id", e.target.value)} disabled={readOnly}>
                  <option value="">No category</option>
                  {(categories.data ?? []).map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.name}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field
                label="Slug"
                htmlFor="a-slug"
                hint={a.published_at ? "Changing the slug changes the public URL of a published article." : "Lower-case words joined by hyphens."}
              >
                <Input id="a-slug" value={draft.slug} onChange={(e) => set("slug", e.target.value)} disabled={readOnly} className="font-mono text-xs" />
              </Field>
              <Field label="Tags" htmlFor="a-tags" className="sm:col-span-2" hint="Press Enter or comma to add a tag.">
                <TagInput id="a-tags" value={draft.tags} onChange={(t) => set("tags", t)} suggestions={tagSuggestions} disabled={readOnly} />
              </Field>
              <Field
                label="Excerpt"
                htmlFor="a-excerpt"
                className="sm:col-span-2"
                hint={
                  <span className="flex justify-between gap-2">
                    <span>Summary for lists, RSS and social cards. Left empty, the first paragraph is used.</span>
                    <Counter value={draft.excerpt} max={300} />
                  </span>
                }
              >
                <Textarea id="a-excerpt" rows={3} value={draft.excerpt} onChange={(e) => set("excerpt", e.target.value)} disabled={readOnly} />
              </Field>
            </CardBody>
          </Card>

          <Card>
            <CardHeader
              title="Featured image"
              icon={ImagePlus}
              description="Credits and licence lines are added to the article automatically."
              actions={
                !readOnly ? (
                  <Button size="sm" variant="outline" onClick={() => setPickerOpen(true)}>
                    {draft.featured_image_id ? "Change" : "Choose image"}
                  </Button>
                ) : null
              }
            />
            <CardBody>
              {draft.featured_image_id ? (
                <div className="flex flex-col gap-3 sm:flex-row">
                  {featured ? (
                    <Thumb img={featured} className="aspect-[4/3] w-full shrink-0 sm:w-48" />
                  ) : (
                    <Skeleton className="aspect-[4/3] w-full shrink-0 sm:w-48" />
                  )}
                  <div className="flex min-w-0 flex-1 flex-col gap-2">
                    {featured ? (
                      <div className="text-xs text-muted">
                        <p className="truncate text-sm font-medium text-text">{featured.title || "Untitled image"}</p>
                        <p className="truncate">
                          {featured.creator || "Unknown creator"} · {featured.license?.name ?? "Unknown licence"} · {featured.provider}
                        </p>
                      </div>
                    ) : null}
                    <Field label="Caption" htmlFor="a-caption">
                      <Input
                        id="a-caption"
                        value={draft.featured_image_caption}
                        onChange={(e) => set("featured_image_caption", e.target.value)}
                        disabled={readOnly}
                        placeholder="What the image shows"
                      />
                    </Field>
                    {featuredWarnings.length ? <ChecksList checks={featuredWarnings} compact /> : null}
                    {!readOnly ? (
                      <div>
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => {
                            set("featured_image_id", null);
                            set("featured_image_caption", "");
                          }}
                        >
                          <X className="h-3.5 w-3.5" /> Remove image
                        </Button>
                      </div>
                    ) : null}
                  </div>
                </div>
              ) : (
                <p className="text-sm text-muted">No featured image. Choose one from the approved images in the library.</p>
              )}
            </CardBody>
          </Card>

          <VideoCard
            video={video}
            videoId={draft.video_id}
            readOnly={readOnly}
            onChange={(v) => {
              setUploadedVideo(v);
              set("video_id", v ? v.id : null);
            }}
          />

          <Card>
            <CardHeader title="Sources" icon={Link2} description="Listed at the end of the article. Link to the reporting you relied on." />
            <CardBody>
              <SourcesEditor value={draft.sources} onChange={(s) => set("sources", s)} disabled={readOnly} />
            </CardBody>
          </Card>

          <Card>
            <CardHeader title="Search and sharing" icon={SearchIcon} description="Optional. Headline and excerpt are used when these are empty." />
            <CardBody className="grid gap-3">
              <Field label="SEO title" htmlFor="a-seo-title" hint={<Counter value={draft.seo_title} max={60} />}>
                <Input id="a-seo-title" value={draft.seo_title} onChange={(e) => set("seo_title", e.target.value)} disabled={readOnly} placeholder={draft.headline} />
              </Field>
              <Field label="SEO description" htmlFor="a-seo-desc" hint={<Counter value={draft.seo_description} max={160} />}>
                <Textarea id="a-seo-desc" rows={2} value={draft.seo_description} onChange={(e) => set("seo_description", e.target.value)} disabled={readOnly} />
              </Field>
            </CardBody>
          </Card>

          <Card>
            <CardHeader title="Linked headlines" icon={Target} description="Research assignments this article was written from." />
            <CardBody>
              {(a.assignment_ids ?? []).length ? (
                <ul className="flex flex-col divide-y divide-border/70">
                  {assignments.map((q, i) => {
                    const aid = (a.assignment_ids ?? [])[i];
                    return (
                      <li key={aid} className="flex items-center gap-2 py-2 first:pt-0 last:pb-0">
                        {q.isLoading ? (
                          <Skeleton className="h-5 w-2/3" />
                        ) : q.data ? (
                          <>
                            <Link href={`/research/${aid}`} className="min-w-0 flex-1 truncate text-sm font-medium hover:text-accent">
                              {q.data.headline}
                            </Link>
                            <StatusBadge status={q.data.research_status} />
                          </>
                        ) : (
                          <span className="text-sm text-muted">Headline no longer available</span>
                        )}
                      </li>
                    );
                  })}
                </ul>
              ) : (
                <p className="text-sm text-muted">Not linked to a headline. Articles started from the Research Workspace are linked automatically.</p>
              )}
            </CardBody>
          </Card>
        </div>

        {/* Side panel */}
        <aside className="flex min-w-0 flex-col gap-4 lg:sticky lg:top-16 lg:max-h-[calc(100vh-5rem)] lg:overflow-y-auto lg:pb-4">
          <Tabs defaultValue="publish">
            <TabsList className="w-full">
              <TabsTrigger value="publish" className="flex-1">
                Workflow
              </TabsTrigger>
              <TabsTrigger value="history" className="flex-1">
                History
              </TabsTrigger>
            </TabsList>
            <TabsContent value="publish" className="mt-3 flex flex-col gap-4">
              <WorkflowPanel article={a} dirty={dirty} canApprove={canApprove} onArticle={onArticle} />
              <Card>
                <CardHeader
                  title="Pre-publication checks"
                  icon={ShieldAlert}
                  description={dirty ? "Based on the last saved version. Save to re-check." : "For a commercial publication. Destination-specific checks run when you publish."}
                />
                <CardBody>
                  <ChecksList checks={checks} />
                </CardBody>
              </Card>
              <PublishPanel article={a} dirty={dirty} onArticle={onArticle} />
            </TabsContent>
            <TabsContent value="history" className="mt-3">
              <HistoryPanel id={id} version={a.version} updatedAt={a.updated_at} />
            </TabsContent>
          </Tabs>
        </aside>
      </div>

      <ImagePickerDialog
        open={pickerOpen}
        onOpenChange={setPickerOpen}
        selectedId={draft.featured_image_id}
        onPick={(img) => {
          setPicked(img);
          set("featured_image_id", img.id);
          if (!draft.featured_image_caption.trim() && img.title) set("featured_image_caption", img.title);
        }}
      />
    </div>
  );
}

function HistoryPanel({ id, version, updatedAt }: { id: string; version: number; updatedAt: string }) {
  const history = useQuery({
    queryKey: ["article-history", id, version, updatedAt],
    queryFn: () => api<AuditOut[]>(`/articles/${id}/history`),
  });
  return (
    <Card>
      <CardHeader title="History" icon={History} description="Saves, status changes and publishing actions, newest first." />
      {history.isLoading ? (
        <SkeletonRows rows={5} />
      ) : history.isError ? (
        <ErrorState error={history.error} onRetry={() => history.refetch()} />
      ) : !history.data?.length ? (
        <EmptyState icon={History} title="No history yet" />
      ) : (
        <ol className="flex flex-col divide-y divide-border/70">
          {history.data.map((h) => (
            <li key={h.id} className="flex flex-col gap-0.5 px-4 py-2">
              <p className="text-sm">{h.summary}</p>
              <p className="flex flex-wrap items-center gap-x-1.5 text-2xs text-muted">
                <span>{h.actor_label}</span>
                <span>·</span>
                <Time value={h.created_at} />
                <Badge tone="neutral" className="ml-auto">
                  {h.action.replace(/^article\./, "")}
                </Badge>
              </p>
            </li>
          ))}
        </ol>
      )}
    </Card>
  );
}
