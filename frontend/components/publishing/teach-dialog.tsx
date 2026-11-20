"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, MousePointerClick } from "lucide-react";
import { useEffect } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { InlineAlert } from "@/components/ui/states";
import { api, errorMessage } from "@/lib/api";
import type { DestinationOut, TeachStatusOut } from "@/lib/types";

const ACTIVE = new Set(["starting", "running"]);

/**
 * "Teach once" for a website that is not WordPress: Samyabadi opens a browser window on the computer it
 * runs on, signs in, and the person clicks the title box, the text box and the Publish button. The
 * dialog follows along and can cancel.
 */
export function TeachDialog({
  open,
  onOpenChange,
  destination,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  destination: DestinationOut | null;
}) {
  const qc = useQueryClient();
  const id = destination?.id;
  const status = useQuery({
    queryKey: ["teach", id],
    queryFn: () => api<TeachStatusOut>(`/publishing/destinations/${id}/teach`),
    enabled: open && !!id,
    refetchInterval: (q) => (ACTIVE.has(q.state.data?.state ?? "") ? 1000 : false),
  });
  const s = status.data;
  const active = ACTIVE.has(s?.state ?? "");

  const start = useMutation({
    mutationFn: () =>
      api<TeachStatusOut>(`/publishing/destinations/${id}/teach`, {
        method: "POST",
      }),
    onSuccess: (r) => qc.setQueryData(["teach", id], r),
    onError: (e) => toast.error(errorMessage(e)),
  });
  const cancel = useMutation({
    mutationFn: () =>
      api<TeachStatusOut>(`/publishing/destinations/${id}/teach`, {
        method: "DELETE",
      }),
    onSuccess: (r) => qc.setQueryData(["teach", id], r),
    onError: (e) => toast.error(errorMessage(e)),
  });

  useEffect(() => {
    if (s?.state === "done") {
      qc.invalidateQueries({ queryKey: ["publishing-destinations"] });
    }
  }, [s?.state, qc]);

  const pct = s?.total ? Math.round(((s.state === "done" ? s.total : (s.step ?? 0)) / s.total) * 100) : 0;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        title={`Show Samyabadi how to post on ${destination?.name ?? "this website"}`}
        description="Once only. Samyabadi remembers the form and its sign-in, and publishes there by itself from then on."
        footer={
          active ? (
            <Button variant="ghost" loading={cancel.isPending} onClick={() => cancel.mutate()}>
              Cancel teaching
            </Button>
          ) : (
            <>
              <Button variant="ghost" onClick={() => onOpenChange(false)}>
                {s?.state === "done" ? "Close" : "Not now"}
              </Button>
              {s?.state !== "done" ? (
                <Button variant="primary" loading={start.isPending} onClick={() => start.mutate()}>
                  <MousePointerClick className="h-4 w-4" />{" "}
                  {s?.state === "failed" || s?.state === "cancelled" ? "Try again" : "Open the browser window"}
                </Button>
              ) : null}
            </>
          )
        }
      >
        <div className="flex flex-col gap-3 text-sm">
          {s?.state === "done" ? (
            <InlineAlert tone="success" title="Done">
              {s.message ?? "Samyabadi now knows this website's post form."} Publish an article to try it; use Teach again if the site's admin panel
              changes.
            </InlineAlert>
          ) : active ? (
            <>
              <div>
                <div className="mb-1 flex justify-between text-xs text-muted">
                  <span>{s?.mode === "login" ? "Signing in" : s?.total ? `Step ${(s.step ?? 0) + 1} of ${s.total}` : "Starting"}</span>
                  <span>{pct}%</span>
                </div>
                <div className="h-1.5 overflow-hidden rounded bg-surface-2">
                  <div className="h-full bg-accent transition-all" style={{ width: `${pct}%` }} />
                </div>
              </div>
              <InlineAlert tone="info" title="Look at the new browser window">
                {s?.message ?? "Opening a browser window…"}
              </InlineAlert>
              <p className="text-xs text-muted">
                The blue bar at the top of that window says what to click. Clicks there are only recorded, so nothing is published while you teach.
              </p>
            </>
          ) : (
            <>
              {s?.state === "failed" ? (
                <InlineAlert tone="danger" title="Teaching stopped">
                  {s.error ?? "Something went wrong."}
                </InlineAlert>
              ) : null}
              {s?.state === "cancelled" ? <InlineAlert tone="warning">{s.message ?? "Teaching was cancelled."}</InlineAlert> : null}
              <ol className="list-decimal space-y-1.5 pl-5 text-sm">
                <li>A browser window opens on this computer and signs in with the saved admin login.</li>
                <li>If the site asks for a captcha or a code, finish signing in in that window.</li>
                <li>
                  Go to the page for writing a new post, and press <b>This is the page</b> in the blue bar.
                </li>
                <li>
                  Click the headline box, the article text box, the photo upload (optional), and then the <b>Publish</b> button. Nothing is published.
                </li>
              </ol>
              <p className="inline-flex items-center gap-1.5 text-xs text-muted">
                <CheckCircle2 className="h-3.5 w-3.5" /> Takes about a minute. Samyabadi must be open on this computer, not a phone.
              </p>
            </>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
