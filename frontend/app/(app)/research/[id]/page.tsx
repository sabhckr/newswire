"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  BarChart3,
  CheckCircle2,
  Clock,
  FileText,
  Globe2,
  Inbox,
  Layers,
  Lock,
  Ban,
  MapPin,
  Newspaper,
  Pencil,
  PenLine,
  Users,
} from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { toast } from "sonner";

import { AssignmentRowMenu } from "@/components/assignments/assignment-actions";
import { LinkButton } from "@/components/assignments/link-button";
import { kindLabel } from "@/components/assignments/place-picker";
import { NewsDetailDrawer } from "@/components/live/news-detail";
import { ImagesTab } from "@/components/research/images-tab";
import { MatchesTab } from "@/components/research/matches-tab";
import { NotesTab, useResearchPatch } from "@/components/research/notes-tab";
import { useUrlState, type Workspace } from "@/components/research/types";
import { VolumeChart } from "@/components/charts";
import { Button } from "@/components/ui/button";
import { Card, CardBody } from "@/components/ui/card";
import { useConfirm } from "@/components/ui/confirm";
import { Select } from "@/components/ui/input";
import { PageHeader, Stat } from "@/components/ui/page";
import { EmptyState, ErrorState, Skeleton, SkeletonRows } from "@/components/ui/states";
import { PriorityBadge, StatusBadge } from "@/components/ui/status-badge";
import { Table, TD, TH, THead, TR } from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Time } from "@/components/ui/time";
import { api, errorMessage } from "@/lib/api";
import { PERMS, useSession } from "@/lib/session";
import { cn, flag, formatDateTime, formatNumber } from "@/lib/utils";

const TABS = ["matches", "timeline", "publishers", "geography", "clusters", "images", "notes"] as const;
type Tab = (typeof TABS)[number];

const ROLE_LABEL: Record<string, string> = { event: "Event location", article: "Article location", mentioned: "Mentioned", publisher: "Publisher location" };
const ROLE_ORDER = ["event", "article", "mentioned", "publisher"];

export default function ResearchWorkspacePage() {
  const { id } = useParams<{ id: string }>();
  const { get, update } = useUrlState();
  const tab: Tab = (TABS as readonly string[]).includes(get("tab")) ? (get("tab") as Tab) : "matches";
  const itemId = get("item") || null;

  const ws = useQuery({
    queryKey: ["research", id],
    queryFn: ({ signal }) => api<Workspace>(`/research/${id}`, { signal }),
  });

  if (ws.isLoading) {
    return (
      <div className="mx-auto max-w-7xl">
        <Skeleton className="mb-2 h-7 w-2/3" />
        <Skeleton className="mb-6 h-4 w-1/3" />
        <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-7">
          {Array.from({ length: 7 }).map((_, i) => (
            <Skeleton key={i} className="h-20" />
          ))}
        </div>
        <SkeletonRows rows={8} />
      </div>
    );
  }
  if (ws.isError || !ws.data) {
    return (
      <div className="mx-auto max-w-7xl">
        <PageHeader title="Research" />
        <Card>
          <ErrorState error={ws.error} onRetry={() => ws.refetch()} />
        </Card>
      </div>
    );
  }

  const data = ws.data;
  const openItem = (nid: string) => update({ item: nid });
  const filterMatches = (patch: Record<string, string | null>) => update({ tab: null, m_page: null, ...patch });

  return (
    <div className="mx-auto max-w-7xl">
      <WorkspaceHeader ws={data} />
      <StatsRow ws={data} onFilter={filterMatches} />

      <Card>
        <Tabs value={tab} onValueChange={(v) => update({ tab: v === "matches" ? null : v })}>
          <TabsList className="px-2" aria-label="Research sections">
            <TabsTrigger value="matches">
              Matches <span className="tabular ml-1 text-xs text-muted">{formatNumber(data.assignment.match_count)}</span>
            </TabsTrigger>
            <TabsTrigger value="timeline">Timeline</TabsTrigger>
            <TabsTrigger value="publishers">
              Publishers <span className="tabular ml-1 text-xs text-muted">{data.publishers.length}</span>
            </TabsTrigger>
            <TabsTrigger value="geography">Geography</TabsTrigger>
            <TabsTrigger value="clusters">
              Clusters <span className="tabular ml-1 text-xs text-muted">{data.clusters.length}</span>
            </TabsTrigger>
            <TabsTrigger value="images">
              Images <span className="tabular ml-1 text-xs text-muted">{data.images.length}</span>
            </TabsTrigger>
            <TabsTrigger value="notes">Notes &amp; checklist</TabsTrigger>
          </TabsList>
          <TabsContent value="matches">
            <MatchesTab ws={data} onOpenItem={openItem} />
          </TabsContent>
          <TabsContent value="timeline">
            <TimelineTab ws={data} />
          </TabsContent>
          <TabsContent value="publishers">
            <PublishersTab ws={data} onPick={(p) => filterMatches({ m_publisher: p })} />
          </TabsContent>
          <TabsContent value="geography">
            <GeographyTab ws={data} onPick={(cc) => filterMatches({ m_country: cc })} />
          </TabsContent>
          <TabsContent value="clusters">
            <ClustersTab ws={data} />
          </TabsContent>
          <TabsContent value="images">
            <ImagesTab ws={data} />
          </TabsContent>
          <TabsContent value="notes">
            <NotesTab ws={data} />
          </TabsContent>
        </Tabs>
      </Card>

      <NewsDetailDrawer id={itemId} onClose={() => update({ item: null })} onOpenItem={openItem} />
    </div>
  );
}

function WorkspaceHeader({ ws }: { ws: Workspace }) {
  const a = ws.assignment;
  const router = useRouter();
  const qc = useQueryClient();
  const confirm = useConfirm();
  const { can } = useSession();
  const patch = useResearchPatch(a.id);

  const start = useMutation({
    mutationFn: () => api<{ id: string }>(`/research/${a.id}/article`, { method: "POST" }),
    onSuccess: (art) => {
      toast.success("Draft article created from this research");
      qc.invalidateQueries({ queryKey: ["research", a.id] });
      qc.invalidateQueries({ queryKey: ["articles"] });
      router.push(`/articles/${art.id}`);
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const startArticle = async () => {
    if (ws.articles.length) {
      const ok = await confirm({
        title: "Start another article?",
        body: `${ws.articles.length} article${ws.articles.length === 1 ? " is" : "s are"} already linked to this research. A new draft will be created with verified sources and notes.`,
        confirmLabel: "Create draft",
      });
      if (!ok) return;
    }
    start.mutate();
  };

  const kw = (kind: string) => a.keywords.filter((k) => k.kind === kind);
  const required = kw("required");
  const core = [...kw("primary"), ...kw("synonym")];
  const excluded = kw("excluded");

  return (
    <>
      <PageHeader
        title={a.headline}
        description={
          <span className="flex flex-wrap items-center gap-2">
            <StatusBadge status={a.status} />
            <PriorityBadge priority={a.priority} />
            {a.category ? <span>{a.category.name}</span> : null}
            <span>
              Last match <Time value={a.last_matched_at} />
            </span>
          </span>
        }
        actions={
          <>
            <label htmlFor="research-status" className="sr-only">
              Research status
            </label>
            <Select
              id="research-status"
              value={a.research_status}
              disabled={!can(PERMS.researchWrite) || patch.isPending}
              onChange={(e) =>
                patch.mutate(
                  { research_status: e.target.value },
                  {
                    onSuccess: (r) => toast.success(`Research marked ${r.research_status.replace("_", " ")}`),
                    onError: (err) => toast.error(errorMessage(err)),
                  },
                )
              }
              className="w-auto"
            >
              <option value="open">Research: open</option>
              <option value="in_progress">Research: in progress</option>
              <option value="done">Research: done</option>
            </Select>
            {can(PERMS.assignmentsWrite) ? (
              <LinkButton href={`/headlines/${a.id}/edit`}>
                <Pencil className="h-4 w-4" /> Edit rules
              </LinkButton>
            ) : null}
            {can(PERMS.articlesWrite) ? (
              <Button variant="primary" onClick={startArticle} loading={start.isPending}>
                <PenLine className="h-4 w-4" /> Start article
              </Button>
            ) : null}
            <AssignmentRowMenu a={a} showResearch={false} />
          </>
        }
      />

      <Card className="mb-4">
        <CardBody className="flex flex-col gap-2 py-3 text-sm">
          {a.description ? <p className="whitespace-pre-line">{a.description}</p> : null}
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 text-xs text-muted">
            {core.length ? (
              <span>
                <span className="font-medium text-text">Keywords:</span> {core.map((k) => k.term).join(", ")}
              </span>
            ) : null}
            {required.length ? (
              <span className="inline-flex items-center gap-1">
                <Lock className="h-3 w-3" aria-hidden />
                <span className="font-medium text-text">Must contain:</span> {required.map((k) => k.term).join(", ")}
              </span>
            ) : null}
            {excluded.length ? (
              <span className="inline-flex items-center gap-1">
                <Ban className="h-3 w-3" aria-hidden />
                <span className="font-medium text-text">Excluding:</span> {excluded.map((k) => k.term).join(", ")}
              </span>
            ) : null}
            {a.geographies.length ? (
              <span className="inline-flex items-center gap-1">
                <MapPin className="h-3 w-3" aria-hidden />
                <span className="font-medium text-text">{a.geo_mode === "require" ? "Only in:" : "Places:"}</span>{" "}
                {a.geographies.map((g) => `${g.name} (${kindLabel(g.kind)})`).join(", ")}
              </span>
            ) : null}
            <span>
              <span className="font-medium text-text">Threshold:</span> {Math.round(a.min_score)}
            </span>
            {a.sources.length ? (
              <span>
                <span className="font-medium text-text">{a.restrict_to_sources ? "Only from:" : "Favouring:"}</span> {a.sources.map((s) => s.name).join(", ")}
              </span>
            ) : null}
          </div>
          {ws.articles.length ? (
            <div className="flex flex-wrap items-center gap-2 border-t border-border pt-2 text-xs">
              <FileText className="h-3.5 w-3.5 text-muted" aria-hidden />
              <span className="text-muted">Articles from this research:</span>
              {ws.articles.map((art) => (
                <Link key={art.id} href={`/articles/${art.id}`} className="inline-flex items-center gap-1.5 rounded border border-border px-1.5 py-0.5 hover:border-accent/50">
                  <span className="max-w-[16rem] truncate">{art.headline || "Untitled draft"}</span>
                  <StatusBadge status={art.status} />
                </Link>
              ))}
            </div>
          ) : null}
        </CardBody>
      </Card>
    </>
  );
}

function StatsRow({ ws, onFilter }: { ws: Workspace; onFilter: (patch: Record<string, string | null>) => void }) {
  const s = ws.stats;
  const unreviewed = s.by_review_status.unreviewed ?? 0;
  const verified = s.by_review_status.verified ?? 0;
  const statButton = (label: string, patch: Record<string, string | null>, child: React.ReactNode) => (
    <button type="button" className="text-left" onClick={() => onFilter(patch)} aria-label={label}>
      {child}
    </button>
  );
  return (
    <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-7">
      <Stat label="Matched reports" value={formatNumber(s.matched_items)} icon={Newspaper} hint="Unreviewed + verified" />
      {statButton(
        "Show matches to review",
        { m_status: "unreviewed" },
        <Stat label="To review" value={formatNumber(unreviewed)} icon={Inbox} tone={unreviewed ? "warning" : undefined} hint={unreviewed ? "Click to review" : "All reviewed"} />,
      )}
      {statButton(
        "Show verified matches",
        { m_status: "verified" },
        <Stat label="Verified" value={formatNumber(verified)} icon={CheckCircle2} tone={verified ? "success" : undefined} />,
      )}
      <Stat label="Publishers" value={formatNumber(s.distinct_publishers)} icon={Users} />
      <Stat label="First report" value={<Time value={s.first_report_at} className="text-base" />} icon={Clock} hint={s.first_report_at ? formatDateTime(s.first_report_at) : "—"} />
      <Stat label="Latest report" value={<Time value={s.latest_report_at} className="text-base" />} icon={Clock} hint={s.latest_report_at ? formatDateTime(s.latest_report_at) : "—"} />
      <Stat label="Average score" value={s.average_score != null ? Math.round(s.average_score) : "—"} icon={BarChart3} hint={`Threshold ${Math.round(ws.assignment.min_score)}`} />
    </div>
  );
}

function TimelineTab({ ws }: { ws: Workspace }) {
  const t = ws.timeline;
  if (!t.buckets.length) {
    return <EmptyState icon={BarChart3} title="No reports to chart yet" description="The timeline fills in as matching reports are collected." />;
  }
  const peak = t.buckets.reduce((m, b) => (b.items > m.items ? b : m), t.buckets[0]);
  return (
    <div className="p-4">
      <p className="mb-3 text-xs text-muted">
        Matched reports per {t.granularity === "hour" ? "hour" : "day"} (unreviewed and verified). Peak: {formatNumber(peak.items)} reports from {formatNumber(peak.publishers)} sources.
      </p>
      <VolumeChart data={t.buckets} x="bucket" bars={[{ key: "items", name: "Reports" }, { key: "publishers", name: "Sources reporting" }]} height={280} kind="bar" />
    </div>
  );
}

function PublishersTab({ ws, onPick }: { ws: Workspace; onPick: (publisher: string) => void }) {
  if (!ws.publishers.length) {
    return <EmptyState icon={Users} title="No publishers yet" description="Publishers appear once matching reports are collected." />;
  }
  const max = Math.max(...ws.publishers.map((p) => p.items));
  return (
    <div>
      <p className="px-3 pt-3 text-xs text-muted">In order of first report — the first row broke the story among your sources. Rejected matches are excluded.</p>
      <Table>
        <THead>
          <tr>
            <TH className="w-10">#</TH>
            <TH>Publisher</TH>
            <TH className="w-[30%]">Reports</TH>
            <TH>First report</TH>
            <TH className="hidden md:table-cell">Latest</TH>
            <TH className="hidden text-right sm:table-cell">Top score</TH>
          </tr>
        </THead>
        <tbody>
          {ws.publishers.map((p, i) => (
            <TR key={p.publisher}>
              <TD className="tabular text-muted">{i + 1}</TD>
              <TD>
                <button type="button" className="text-left font-medium hover:text-accent" onClick={() => onPick(p.publisher)} title="Show this publisher’s matches">
                  {p.publisher}
                </button>
                {i === 0 ? <span className="ml-2 rounded bg-accent/10 px-1.5 py-0.5 text-2xs font-medium text-accent">First</span> : null}
              </TD>
              <TD>
                <div className="flex items-center gap-2">
                  <div className="h-1.5 flex-1 rounded bg-surface-2" aria-hidden>
                    <div className="h-1.5 rounded bg-accent/60" style={{ width: `${(p.items / max) * 100}%` }} />
                  </div>
                  <span className="tabular w-8 text-right text-xs">{p.items}</span>
                </div>
              </TD>
              <TD className="whitespace-nowrap text-xs">
                <Time value={p.first_report_at} absolute />
              </TD>
              <TD className="hidden whitespace-nowrap text-xs text-muted md:table-cell">
                <Time value={p.latest_report_at} />
              </TD>
              <TD className="tabular hidden text-right sm:table-cell">{p.top_score != null ? Math.round(p.top_score) : "—"}</TD>
            </TR>
          ))}
        </tbody>
      </Table>
    </div>
  );
}

function GeographyTab({ ws, onPick }: { ws: Workspace; onPick: (cc: string) => void }) {
  if (!ws.geography.length) {
    return <EmptyState icon={Globe2} title="No places detected yet" description="Countries appear when matched reports mention or are datelined in a known place (confidence ≥ 50%)." />;
  }
  const roles = ROLE_ORDER.filter((r) => ws.geography.some((g) => g.roles[r]));
  for (const g of ws.geography) for (const r of Object.keys(g.roles)) if (!roles.includes(r)) roles.push(r);
  const rows = [...ws.geography].sort((x, y) => (y.roles.event ?? 0) + (y.roles.article ?? 0) - ((x.roles.event ?? 0) + (x.roles.article ?? 0)));
  return (
    <div>
      <p className="px-3 pt-3 text-xs text-muted">Reports per country by how the place was detected. Select a country to filter matches.</p>
      <Table>
        <THead>
          <tr>
            <TH>Country</TH>
            {roles.map((r) => (
              <TH key={r} className="text-right">
                {ROLE_LABEL[r] ?? r}
              </TH>
            ))}
          </tr>
        </THead>
        <tbody>
          {rows.map((g) => (
            <TR key={g.country_code}>
              <TD>
                <button type="button" className="font-medium hover:text-accent" onClick={() => onPick(g.country_code)}>
                  {flag(g.country_code)} {g.country_code}
                </button>
              </TD>
              {roles.map((r) => (
                <TD key={r} className={cn("tabular text-right", !g.roles[r] && "text-muted")}>
                  {g.roles[r] ? formatNumber(g.roles[r]) : "—"}
                </TD>
              ))}
            </TR>
          ))}
        </tbody>
      </Table>
    </div>
  );
}

function ClustersTab({ ws }: { ws: Workspace }) {
  if (!ws.clusters.length) {
    return <EmptyState icon={Layers} title="No story clusters yet" description="Clusters group reports about the same event across publishers; they form as similar reports arrive." />;
  }
  return (
    <ul className="divide-y divide-border">
      {ws.clusters.map((c) => (
        <li key={c.id} className="flex flex-wrap items-center gap-x-4 gap-y-1 px-3 py-2">
          <Link href={`/clusters?id=${c.id}`} className="min-w-0 flex-1 truncate text-sm font-medium hover:text-accent" title={c.title}>
            {c.title}
          </Link>
          <span className="tabular text-xs text-muted">
            {formatNumber(c.matched_items)} of {formatNumber(c.item_count)} reports match · {formatNumber(c.publisher_count)} publishers
          </span>
          <span className="whitespace-nowrap text-xs text-muted">
            <Time value={c.first_seen_at} /> → <Time value={c.last_seen_at} />
          </span>
        </li>
      ))}
    </ul>
  );
}
