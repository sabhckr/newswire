"use client";

import { useMutation } from "@tanstack/react-query";
import { Archive, ArchiveRestore, CalendarX, CheckCircle2, FileEdit, GitBranch, Send } from "lucide-react";
import { toast } from "sonner";

import { useInvalidatePublishing } from "@/components/publishing/jobs";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { useConfirm } from "@/components/ui/confirm";
import { StatusBadge, statusLabel } from "@/components/ui/status-badge";
import { Time } from "@/components/ui/time";
import { api, errorMessage } from "@/lib/api";
import type { ArticleOut } from "@/lib/types";
import { formatDateTime } from "@/lib/utils";

const PUBLICATION_STATES = new Set(["published", "unpublished", "scheduled"]);

type Spec = { label: string; icon: React.ComponentType<{ className?: string }>; variant: "primary" | "secondary" | "outline" | "ghost" };

function spec(from: string, to: string): Spec {
  if (to === "in_review") return { label: "Submit for review", icon: Send, variant: "primary" };
  if (to === "approved") return from === "scheduled" ? { label: "Cancel schedule", icon: CalendarX, variant: "outline" } : { label: "Approve", icon: CheckCircle2, variant: "primary" };
  if (to === "draft") return from === "archived" ? { label: "Restore to draft", icon: ArchiveRestore, variant: "secondary" } : { label: "Return to draft", icon: FileEdit, variant: "outline" };
  if (to === "archived") return { label: "Archive", icon: Archive, variant: "ghost" };
  return { label: statusLabel(to), icon: GitBranch, variant: "outline" };
}

const STAGE_HELP: Record<string, string> = {
  draft: "Being written. Submit it for review when the text is ready.",
  in_review: "Waiting for an editor to approve it.",
  approved: "Approved and ready to publish. Nothing goes out until someone with publishing rights publishes it.",
  scheduled: "Will be published automatically at the scheduled time.",
  published: "Live on at least one destination.",
  unpublished: "Taken down from every destination. You can publish again or return it to draft.",
  archived: "Archived and read-only. Restore it to draft to edit.",
};

export function WorkflowPanel({
  article,
  dirty,
  canApprove,
  onArticle,
}: {
  article: ArticleOut;
  dirty: boolean;
  canApprove: boolean;
  onArticle: (a: ArticleOut) => void;
}) {
  const confirm = useConfirm();
  const invalidate = useInvalidatePublishing();
  const targets = (article.transitions ?? []).filter((t) => !PUBLICATION_STATES.has(t));
  const move = useMutation({
    mutationFn: (target: string) => api<ArticleOut>(`/articles/${article.id}/transition`, { body: { target } }),
    onSuccess: (a, target) => {
      onArticle(a);
      invalidate();
      toast.success(target === "approved" && a.status === "approved" ? "Approved. It is ready to publish." : `Moved to ${statusLabel(a.status)}`);
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  const go = async (to: string) => {
    if (to === "archived") {
      const ok = await confirm({
        title: "Archive this article?",
        body: "Archived articles are hidden from the default list and cannot be edited until restored to draft.",
        confirmLabel: "Archive",
        destructive: true,
      });
      if (!ok) return;
    }
    if (article.status === "scheduled" && to !== "published") {
      const ok = await confirm({
        title: "Cancel the scheduled publication?",
        body: `It was due to go live ${formatDateTime(article.scheduled_at)}. The pending job is cancelled.`,
        confirmLabel: spec(article.status, to).label,
      });
      if (!ok) return;
    }
    move.mutate(to);
  };

  return (
    <Card>
      <CardHeader title="Workflow" icon={GitBranch} />
      <CardBody className="flex flex-col gap-3">
        <div className="flex flex-wrap items-center gap-2">
          <StatusBadge status={article.status} />
          {article.status === "scheduled" && article.scheduled_at ? (
            <span className="text-xs text-muted">for {formatDateTime(article.scheduled_at)}</span>
          ) : null}
          {article.approved_at && ["approved", "scheduled", "published"].includes(article.status) ? (
            <span className="text-xs text-muted">
              approved <Time value={article.approved_at} />
            </span>
          ) : null}
        </div>
        <p className="text-xs text-muted">{STAGE_HELP[article.status] ?? null}</p>
        {["approved", "scheduled"].includes(article.status) ? (
          <p className="rounded border border-border bg-surface-2 px-2 py-1.5 text-xs text-muted">
            {canApprove
              ? "You can approve articles, so your edits keep it approved. Re-read it before publishing."
              : "Editing the headline, subtitle, body or featured image sends it back to review. Tags, sources and SEO fields do not."}
          </p>
        ) : null}
        {targets.length ? (
          <div className="flex flex-wrap gap-2">
            {targets.map((t) => {
              const s = spec(article.status, t);
              return (
                <Button
                  key={t}
                  size="sm"
                  variant={s.variant}
                  disabled={dirty}
                  loading={move.isPending && move.variables === t}
                  onClick={() => go(t)}
                >
                  <s.icon className="h-3.5 w-3.5" /> {s.label}
                </Button>
              );
            })}
          </div>
        ) : (
          <p className="text-xs text-muted">No workflow actions are available to you at this stage.</p>
        )}
        {dirty && targets.length ? <p className="text-xs text-warning">Save your changes before changing the status.</p> : null}
      </CardBody>
    </Card>
  );
}
