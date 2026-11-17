"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, ExternalLink, ShieldCheck, Trash2, X } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import { imageWarnings, LicenceBadge, previewSrc, Thumb, WarningList } from "@/components/images/media";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useConfirm } from "@/components/ui/confirm";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { Field, Input, Textarea } from "@/components/ui/input";
import { ErrorState, InlineAlert, Skeleton } from "@/components/ui/states";
import { StatusBadge } from "@/components/ui/status-badge";
import { Switch } from "@/components/ui/switch";
import { Time } from "@/components/ui/time";
import { api, ApiError, errorMessage } from "@/lib/api";
import { PERMS, useSession } from "@/lib/session";
import type { ImageOut } from "@/lib/types";
import { formatDateTime, hostOf } from "@/lib/utils";

function yesNo(v: boolean | null | undefined): string {
  return v === true ? "Yes" : v === false ? "No" : "Not documented";
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[8.5rem_minmax(0,1fr)] gap-2 py-1 text-sm">
      <dt className="text-muted">{label}</dt>
      <dd className="min-w-0 break-words">{children}</dd>
    </div>
  );
}

function ExtLink({ href, children }: { href: string; children?: React.ReactNode }) {
  return (
    <a href={href} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 text-accent hover:underline">
      {children ?? hostOf(href)} <ExternalLink className="h-3 w-3" aria-hidden />
    </a>
  );
}

type Draft = { title: string; creator: string; attribution_text: string; notes: string };
const toDraft = (i: ImageOut): Draft => ({
  title: i.title ?? "",
  creator: i.creator ?? "",
  attribution_text: i.attribution_text ?? "",
  notes: i.notes ?? "",
});

function DetailBody({ id, onDeleted }: { id: string; onDeleted: () => void }) {
  const qc = useQueryClient();
  const confirm = useConfirm();
  const { can } = useSession();
  const canWrite = can(PERMS.imagesWrite);
  const canApprove = can(PERMS.imagesApprove);
  const [commercial, setCommercial] = useState(true);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [rejecting, setRejecting] = useState(false);
  const [rejectNote, setRejectNote] = useState("");
  const [approveError, setApproveError] = useState<string | null>(null);

  const q = useQuery({
    queryKey: ["image", id, commercial],
    queryFn: () => api<ImageOut>(`/images/${id}`, { query: { commercial } }),
  });

  useEffect(() => {
    setDraft(null);
    setRejecting(false);
    setRejectNote("");
    setApproveError(null);
  }, [id]);

  const done = (img: ImageOut, msg: string) => {
    qc.setQueryData(["image", id, commercial], img);
    qc.invalidateQueries({ queryKey: ["image", id] });
    qc.invalidateQueries({ queryKey: ["images"] });
    qc.invalidateQueries({ queryKey: ["image-search"] });
    toast.success(msg);
  };

  const save = useMutation({
    mutationFn: (d: Draft) =>
      api<ImageOut>(`/images/${id}`, {
        method: "PATCH",
        body: {
          title: d.title.trim() || null,
          creator: d.creator.trim() || null,
          notes: d.notes.trim() || null,
          ...(attributionLocked ? {} : { attribution_text: d.attribution_text.trim() || null }),
        },
      }),
    onSuccess: (img) => {
      setDraft(null);
      done(img, "Image details saved");
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  const approve = useMutation({
    mutationFn: () => api<ImageOut>(`/images/${id}/approve`, { method: "POST" }),
    onSuccess: (img) => {
      setApproveError(null);
      done(img, "Image approved and stored locally");
    },
    onError: (e) => {
      setApproveError(errorMessage(e));
      // a failed download is recorded on the asset, so refresh it
      qc.invalidateQueries({ queryKey: ["image", id] });
    },
  });

  const reject = useMutation({
    mutationFn: () => api<ImageOut>(`/images/${id}/reject`, { body: { note: rejectNote.trim() || null } }),
    onSuccess: (img) => {
      setRejecting(false);
      setRejectNote("");
      done(img, "Image rejected");
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  const remove = useMutation({
    mutationFn: () => api(`/images/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["images"] });
      qc.invalidateQueries({ queryKey: ["image-search"] });
      qc.removeQueries({ queryKey: ["image", id] });
      toast.success("Image deleted");
      onDeleted();
    },
    onError: (e) =>
      toast.error(e instanceof ApiError && e.status === 409 ? "This image is used in an article. Remove it from the article first." : errorMessage(e)),
  });

  const img = q.data;
  const attributionLocked = Boolean(img && img.approval_status === "approved" && !canApprove);

  if (q.isLoading) {
    return (
      <div className="grid gap-4 md:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
        <Skeleton className="aspect-[4/3] w-full" />
        <div className="space-y-2">
          <Skeleton className="h-5 w-2/3" />
          <Skeleton className="h-4 w-full" />
          <Skeleton className="h-4 w-5/6" />
        </div>
      </div>
    );
  }
  if (q.isError || !img) return <ErrorState error={q.error} onRetry={() => q.refetch()} />;

  const warnings = imageWarnings(img);
  const lic = img.license;
  const d = draft ?? toDraft(img);
  const dirty = draft !== null && JSON.stringify(draft) !== JSON.stringify(toDraft(img));
  const set = (k: keyof Draft) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => setDraft({ ...d, [k]: e.target.value });

  const del = async () => {
    const ok = await confirm({
      title: "Delete this image?",
      body: "The record and any stored copy are removed. This cannot be undone. Images used in an article cannot be deleted.",
      confirmLabel: "Delete image",
      destructive: true,
    });
    if (ok) remove.mutate();
  };

  return (
    <div className="flex flex-col gap-4">
      <div className="grid gap-4 md:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
        <div className="flex flex-col gap-2">
          <Thumb src={previewSrc(img, true)} alt={img.title ?? "Image preview"} className="aspect-[4/3] w-full rounded border border-border" imgClassName="object-contain" />
          <div className="flex flex-wrap items-center gap-1.5">
            <StatusBadge status={img.approval_status} />
            <LicenceBadge code={lic?.code} name={lic ? `${lic.name}${lic.version && lic.code.startsWith("by") ? ` ${lic.version}` : ""}` : img.license_raw} />
            {img.download_status === "failed" ? (
              <Badge tone="danger" icon={X}>
                Download failed
              </Badge>
            ) : null}
          </div>
          {img.download_status === "downloaded" ? (
            <p className="text-2xs text-muted">Stored locally{img.width && img.height ? ` · ${img.width}×${img.height}` : ""}</p>
          ) : (
            <p className="text-2xs text-muted">Not stored yet. The original is downloaded when an approver approves it.</p>
          )}
        </div>

        <div className="min-w-0">
          <dl className="divide-y divide-border/60">
            <Row label="Source">
              {img.provider === "upload" ? "Uploaded" : img.repository || img.provider}
              {img.source_page_url ? (
                <>
                  {" · "}
                  <ExtLink href={img.source_page_url}>Source page</ExtLink>
                </>
              ) : null}
            </Row>
            <Row label="Creator">
              {img.creator ? img.creator_url ? <ExtLink href={img.creator_url}>{img.creator}</ExtLink> : img.creator : <span className="text-muted">Not recorded</span>}
            </Row>
            <Row label="Licence">
              {lic ? (
                <>
                  {lic.name} {lic.version}
                  {(img.license_url ?? lic.url) ? (
                    <>
                      {" · "}
                      <ExtLink href={(img.license_url ?? lic.url)!}>Licence terms</ExtLink>
                    </>
                  ) : null}
                </>
              ) : (
                <span className="text-danger">Unknown</span>
              )}
              {img.license_raw && img.license_raw !== lic?.code ? <span className="block text-xs text-muted">As published: {img.license_raw}</span> : null}
            </Row>
            <Row label="Commercial use">{yesNo(lic?.allows_commercial)}</Row>
            <Row label="Modifications">{yesNo(lic?.allows_derivatives)}</Row>
            <Row label="Attribution">{lic?.requires_attribution ? "Required" : lic?.requires_attribution === false ? "Not required" : "Not documented"}</Row>
            {lic?.share_alike ? <Row label="Share-alike">Adaptations must use the same licence</Row> : null}
            <Row label="Size">
              {img.width && img.height ? `${img.width}×${img.height}` : "Unknown"}
              {img.mime ? ` · ${img.mime}` : ""}
            </Row>
            {img.search_query ? <Row label="Found with">“{img.search_query}”</Row> : null}
            <Row label="Retrieved">
              <Time value={img.retrieved_at} absolute />
            </Row>
            {img.approved_at ? <Row label="Approved">{formatDateTime(img.approved_at)}</Row> : null}
            {img.download_error ? (
              <Row label="Download">
                <span className="text-danger">{img.download_error}</span>
              </Row>
            ) : null}
          </dl>
        </div>
      </div>

      <section aria-labelledby="img-warn" className="rounded border border-border p-3">
        <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
          <h3 id="img-warn" className="text-sm font-semibold">
            Rights check
          </h3>
          <Switch id="img-commercial" label="Commercial publication" checked={commercial} onCheckedChange={setCommercial} />
        </div>
        {warnings.length ? <WarningList warnings={warnings} /> : <p className="text-xs text-success">No licence issues for this use.</p>}
      </section>

      <section aria-labelledby="img-meta" className="flex flex-col gap-3">
        <h3 id="img-meta" className="text-sm font-semibold">
          Credit and notes
        </h3>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Title" htmlFor="img-title">
            <Input id="img-title" value={d.title} onChange={set("title")} disabled={!canWrite} />
          </Field>
          <Field label="Creator" htmlFor="img-creator">
            <Input id="img-creator" value={d.creator} onChange={set("creator")} disabled={!canWrite} />
          </Field>
        </div>
        <Field
          label="Attribution text"
          htmlFor="img-attr"
          hint={
            attributionLocked
              ? "Only an approver can change the attribution of an approved image."
              : lic?.requires_attribution
                ? "Required by this licence. It is printed with the image wherever it is used."
                : "Shown with the image when published."
          }
        >
          <Textarea id="img-attr" rows={2} value={d.attribution_text} onChange={set("attribution_text")} disabled={!canWrite || attributionLocked} />
        </Field>
        <Field label="Internal notes" htmlFor="img-notes">
          <Textarea id="img-notes" rows={2} value={d.notes} onChange={set("notes")} disabled={!canWrite} />
        </Field>
        {canWrite && dirty ? (
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setDraft(null)}>
              Discard
            </Button>
            <Button variant="primary" onClick={() => save.mutate(d)} loading={save.isPending}>
              Save details
            </Button>
          </div>
        ) : null}
      </section>

      {approveError ? (
        <InlineAlert tone="danger" title="This image cannot be approved yet">
          {approveError}
        </InlineAlert>
      ) : null}

      {rejecting ? (
        <div className="rounded border border-border p-3">
          <Field label="Reason for rejecting (optional)" htmlFor="img-reject">
            <Textarea id="img-reject" rows={2} value={rejectNote} onChange={(e) => setRejectNote(e.target.value)} autoFocus placeholder="e.g. Licence does not allow commercial use" />
          </Field>
          <div className="mt-2 flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setRejecting(false)}>
              Cancel
            </Button>
            <Button variant="danger" onClick={() => reject.mutate()} loading={reject.isPending}>
              <X className="h-4 w-4" /> Reject image
            </Button>
          </div>
        </div>
      ) : null}

      {canApprove ? (
        <div className="flex flex-wrap items-center gap-2 border-t border-border pt-3">
          <Button variant="danger-outline" onClick={del} loading={remove.isPending}>
            <Trash2 className="h-4 w-4" /> Delete
          </Button>
          <span className="flex-1" />
          {img.approval_status !== "rejected" && !rejecting ? (
            <Button variant="outline" onClick={() => setRejecting(true)}>
              <X className="h-4 w-4" /> Reject…
            </Button>
          ) : null}
          {img.approval_status !== "approved" ? (
            <Button variant="primary" onClick={() => approve.mutate()} loading={approve.isPending} disabled={dirty}>
              {approve.isPending ? null : <ShieldCheck className="h-4 w-4" />} Approve for use
            </Button>
          ) : (
            <span className="inline-flex items-center gap-1 text-sm text-success">
              <Check className="h-4 w-4" aria-hidden /> Approved
            </span>
          )}
        </div>
      ) : (
        <p className="border-t border-border pt-3 text-xs text-muted">An approver must review the licence before this image can be used in an article.</p>
      )}
      {canApprove && dirty && img.approval_status !== "approved" ? <p className="-mt-2 text-right text-2xs text-muted">Save your changes before approving.</p> : null}
    </div>
  );
}

export function ImageDetailDialog({ id, onOpenChange }: { id: string | null; onOpenChange: (open: boolean) => void }) {
  return (
    <Dialog open={Boolean(id)} onOpenChange={onOpenChange}>
      {id ? (
        <DialogContent wide title="Image" description="Provenance, licence and approval">
          <DetailBody id={id} onDeleted={() => onOpenChange(false)} />
        </DialogContent>
      ) : null}
    </Dialog>
  );
}
