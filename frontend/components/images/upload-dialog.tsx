"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Upload } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { Field, Input, Select, Textarea } from "@/components/ui/input";
import { InlineAlert } from "@/components/ui/states";
import { api, errorMessage } from "@/lib/api";
import type { ImageOut } from "@/lib/types";
import { formatBytes } from "@/lib/utils";

/** Licences the upload endpoint accepts (app/images/licenses.py TERMS, minus "unknown"). */
export const UPLOAD_LICENCES: { code: string; label: string; attribution: boolean }[] = [
  { code: "own", label: "Own work / rights held by the publication", attribution: false },
  { code: "cc0", label: "CC0 Public Domain Dedication", attribution: false },
  { code: "pdm", label: "Public Domain Mark", attribution: false },
  { code: "by", label: "CC BY 4.0", attribution: true },
  { code: "by-sa", label: "CC BY-SA 4.0", attribution: true },
  { code: "by-nd", label: "CC BY-ND 4.0 (no modifications)", attribution: true },
  { code: "by-nc", label: "CC BY-NC 4.0 (non-commercial)", attribution: true },
  { code: "by-nc-sa", label: "CC BY-NC-SA 4.0 (non-commercial)", attribution: true },
  { code: "by-nc-nd", label: "CC BY-NC-ND 4.0 (non-commercial, no modifications)", attribution: true },
  { code: "other", label: "Other licence (cannot be approved until reviewed)", attribution: false },
];

const ACCEPT = "image/jpeg,image/png,image/webp,image/gif";

export function UploadDialog({
  open,
  onOpenChange,
  assignmentId,
  onUploaded,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  assignmentId?: string | null;
  onUploaded?: (img: ImageOut) => void;
}) {
  const qc = useQueryClient();
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [licence, setLicence] = useState("");
  const [title, setTitle] = useState("");
  const [creator, setCreator] = useState("");
  const [attribution, setAttribution] = useState("");
  const [sourceUrl, setSourceUrl] = useState("");
  const [touched, setTouched] = useState(false);

  useEffect(() => {
    if (!open) {
      setFile(null);
      setLicence("");
      setTitle("");
      setCreator("");
      setAttribution("");
      setSourceUrl("");
      setTouched(false);
    }
  }, [open]);

  useEffect(() => {
    if (!file) {
      setPreview(null);
      return;
    }
    const url = URL.createObjectURL(file);
    setPreview(url);
    return () => URL.revokeObjectURL(url);
  }, [file]);

  const lic = UPLOAD_LICENCES.find((l) => l.code === licence);
  const sourceInvalid = Boolean(sourceUrl.trim()) && !/^https?:\/\/\S+$/i.test(sourceUrl.trim());
  const errors = {
    file: !file ? "Choose an image file" : !ACCEPT.split(",").includes(file.type) ? "Use a JPEG, PNG, WebP or GIF image" : null,
    licence: !licence ? "Choose the licence under which you may use this image" : null,
    source: sourceInvalid ? "Enter a full http(s) address" : null,
  };
  const valid = !errors.file && !errors.licence && !errors.source;

  const upload = useMutation({
    mutationFn: () => {
      const form = new FormData();
      form.append("file", file!);
      form.append("license_code", licence);
      if (title.trim()) form.append("title", title.trim());
      if (creator.trim()) form.append("creator", creator.trim());
      if (attribution.trim()) form.append("attribution_text", attribution.trim());
      if (sourceUrl.trim()) form.append("source_page_url", sourceUrl.trim());
      if (assignmentId) form.append("assignment_id", assignmentId);
      return api<ImageOut>("/images/upload", { form });
    },
    onSuccess: (img) => {
      qc.invalidateQueries({ queryKey: ["images"] });
      toast.success("Image uploaded. It awaits approval before use.");
      onOpenChange(false);
      onUploaded?.(img);
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  const submit = () => {
    setTouched(true);
    if (valid) upload.mutate();
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      {open ? (
        <DialogContent
          wide
          title="Upload an image"
          description="Only upload images you have the right to use. Record who made it and the licence it comes with."
          footer={
            <>
              <Button variant="outline" onClick={() => onOpenChange(false)}>
                Cancel
              </Button>
              <Button variant="primary" onClick={submit} loading={upload.isPending}>
                <Upload className="h-4 w-4" /> Upload
              </Button>
            </>
          }
        >
          <form
            className="grid gap-4 md:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]"
            onSubmit={(e) => {
              e.preventDefault();
              submit();
            }}
            noValidate
          >
            <div className="flex flex-col gap-2">
              <Field label="Image file" htmlFor="up-file" required error={touched ? (errors.file ?? undefined) : undefined} hint="JPEG, PNG, WebP or GIF">
                <Input id="up-file" type="file" accept={ACCEPT} onChange={(e) => setFile(e.target.files?.[0] ?? null)} className="h-auto py-1" />
              </Field>
              <div className="flex aspect-[4/3] items-center justify-center overflow-hidden rounded border border-dashed border-border bg-surface-2">
                {preview ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img src={preview} alt="Selected image preview" className="h-full w-full object-contain" />
                ) : (
                  <span className="text-xs text-muted">Preview</span>
                )}
              </div>
              {file ? (
                <p className="truncate text-2xs text-muted">
                  {file.name} · {formatBytes(file.size)}
                </p>
              ) : null}
            </div>
            <div className="flex flex-col gap-3">
              <Field label="Licence" htmlFor="up-licence" required error={touched ? (errors.licence ?? undefined) : undefined}>
                <Select id="up-licence" value={licence} onChange={(e) => setLicence(e.target.value)} className="w-full">
                  <option value="">Choose a licence…</option>
                  {UPLOAD_LICENCES.map((l) => (
                    <option key={l.code} value={l.code}>
                      {l.label}
                    </option>
                  ))}
                </Select>
              </Field>
              {licence.startsWith("by-nc") ? (
                <InlineAlert tone="warning">Non-commercial licences block use in commercial publications.</InlineAlert>
              ) : null}
              <Field label="Title" htmlFor="up-title">
                <Input id="up-title" value={title} onChange={(e) => setTitle(e.target.value)} maxLength={500} />
              </Field>
              <Field label="Creator" htmlFor="up-creator">
                <Input id="up-creator" value={creator} onChange={(e) => setCreator(e.target.value)} maxLength={255} />
              </Field>
              <Field
                label="Attribution text"
                htmlFor="up-attr"
                required={Boolean(lic?.attribution)}
                hint={lic?.attribution ? "This licence requires attribution. The image cannot be approved without it." : "Shown with the image when published."}
              >
                <Textarea id="up-attr" rows={2} value={attribution} onChange={(e) => setAttribution(e.target.value)} placeholder="e.g. Photo: Jane Doe / CC BY 4.0" />
              </Field>
              <Field label="Source page" htmlFor="up-source" error={touched ? (errors.source ?? undefined) : undefined}>
                <Input id="up-source" type="url" inputMode="url" value={sourceUrl} onChange={(e) => setSourceUrl(e.target.value)} placeholder="https://" />
              </Field>
            </div>
          </form>
        </DialogContent>
      ) : null}
    </Dialog>
  );
}
