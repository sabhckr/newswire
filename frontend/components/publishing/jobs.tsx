"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Ban, RotateCw } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { useConfirm } from "@/components/ui/confirm";
import { StatusBadge } from "@/components/ui/status-badge";
import { api, errorMessage } from "@/lib/api";
import type { JobOut } from "@/lib/types";

export const ACTION_LABEL: Record<string, string> = {
  publish: "Publish",
  draft: "Send as draft",
  unpublish: "Unpublish",
};

export function JobStatus({ job }: { job: JobOut }) {
  if (job.status === "pending" && job.scheduled_for && job.attempts === 0) return <StatusBadge status="scheduled" />;
  if (job.status === "pending" && job.attempts > 0) return <StatusBadge status="pending" label={`Retrying (${job.attempts}/${job.max_attempts})`} />;
  return <StatusBadge status={job.status} />;
}

/** Shared invalidation after any publishing change. */
export function useInvalidatePublishing() {
  const qc = useQueryClient();
  return () => {
    qc.invalidateQueries({ queryKey: ["publishing-jobs"] });
    qc.invalidateQueries({ queryKey: ["article"] });
    qc.invalidateQueries({ queryKey: ["articles"] });
    qc.invalidateQueries({ queryKey: ["overview"] });
  };
}

export function JobActions({ job, canAct }: { job: JobOut; canAct: boolean }) {
  const confirm = useConfirm();
  const invalidate = useInvalidatePublishing();
  const retry = useMutation({
    mutationFn: () => api<JobOut>(`/publishing/jobs/${job.id}/retry`, { method: "POST" }),
    onSuccess: () => {
      toast.success(job.status === "pending" ? "Queued to run now" : "Queued for another attempt");
      invalidate();
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const cancel = useMutation({
    mutationFn: () => api<JobOut>(`/publishing/jobs/${job.id}/cancel`, { method: "POST" }),
    onSuccess: () => {
      toast.success("Job cancelled");
      invalidate();
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  if (!canAct) return null;
  // A job scheduled for later must not be run early (the API refuses it too).
  const future = !!job.scheduled_for && new Date(job.scheduled_for).getTime() > Date.now();
  const canRetry = !future && ["failed", "cancelled", "pending"].includes(job.status);
  return (
    <div className="flex items-center justify-end gap-1">
      {canRetry ? (
        <Button
          size="sm"
          variant="ghost"
          loading={retry.isPending}
          onClick={async () => {
            if (job.status === "cancelled") {
              const ok = await confirm({
                title: "Retry a cancelled job?",
                body: `This sends “${job.article_headline ?? "the article"}” to ${job.destination_name ?? "the destination"} again, using the current saved version.`,
                confirmLabel: "Retry",
              });
              if (!ok) return;
            }
            retry.mutate();
          }}
        >
          <RotateCw className="h-3.5 w-3.5" /> {job.status === "pending" ? "Run now" : "Retry"}
        </Button>
      ) : null}
      {job.status === "pending" ? (
        <Button
          size="sm"
          variant="ghost"
          loading={cancel.isPending}
          onClick={async () => {
            const ok = await confirm({
              title: job.scheduled_for ? "Cancel this scheduled publication?" : "Cancel this job?",
              body: job.scheduled_for
                ? "The article returns to Approved and will not be published at the scheduled time."
                : "The job stops retrying. You can request publication again later.",
              confirmLabel: "Cancel job",
              destructive: true,
            });
            if (ok) cancel.mutate();
          }}
        >
          <Ban className="h-3.5 w-3.5" /> Cancel
        </Button>
      ) : null}
    </div>
  );
}
