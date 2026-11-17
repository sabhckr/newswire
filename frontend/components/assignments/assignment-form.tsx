"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CalendarClock, KeyRound, MapPin, Newspaper, Rss, Save } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import { Controller, useForm, useWatch } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";

import { DEFAULT_WEIGHTS, KeywordEditor, type KeywordValue } from "@/components/assignments/keyword-editor";
import { PlaceChip, PlacePicker } from "@/components/assignments/place-picker";
import { PreviewPanel } from "@/components/assignments/preview-panel";
import { SourcePicker } from "@/components/assignments/source-picker";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Checkbox, Field, Input, Select, Textarea } from "@/components/ui/input";
import { InlineAlert } from "@/components/ui/states";
import { api, errorMessage } from "@/lib/api";
import { PERMS, useSession } from "@/lib/session";
import type { AssignmentIn, AssignmentOut, CategoryOut, GeoEntityOut, SettingsMap, SourceBrief } from "@/lib/types";
import { cn } from "@/lib/utils";

const KINDS = ["primary", "synonym", "required", "secondary", "excluded"] as const;

const schema = z
  .object({
    headline: z.string().trim().min(3, "Use at least 3 characters").max(500, "Keep it under 500 characters"),
    description: z.string().max(20000, "Too long"),
    keywords: z.array(z.object({ term: z.string().min(1).max(200), kind: z.enum(KINDS), weight: z.number().min(-200).max(200).nullable() })),
    category_id: z.string(),
    language: z
      .string()
      .trim()
      .max(16, "Use a short language code such as en")
      .refine((v) => v === "" || /^[A-Za-z]{2,3}([-_][A-Za-z0-9]{2,8})?$/.test(v), "Use a language code such as en, fr or pt-BR"),
    priority: z.enum(["low", "normal", "high", "urgent"]),
    status: z.enum(["active", "paused"]),
    start_date: z.string(),
    end_date: z.string(),
    interval_minutes: z
      .string()
      .trim()
      .refine((v) => v === "" || (/^\d+$/.test(v) && Number(v) >= 1 && Number(v) <= 1440), "Whole minutes from 1 to 1440, or leave blank"),
    min_score: z.number({ invalid_type_error: "Enter a number" }).min(0, "Use 0 or more").max(1000, "Use 1000 or less"),
    restrict_to_sources: z.boolean(),
    geo_mode: z.enum(["boost", "require"]),
    sources: z.array(z.custom<SourceBrief>()),
    places: z.array(z.custom<GeoEntityOut>()),
  })
  .superRefine((v, ctx) => {
    if (!v.keywords.some((k) => k.kind === "primary" || k.kind === "synonym" || k.kind === "required")) {
      ctx.addIssue({ code: z.ZodIssueCode.custom, path: ["keywords"], message: "Add at least one primary keyword, synonym or required phrase." });
    }
    if (v.start_date && v.end_date && v.end_date < v.start_date) {
      ctx.addIssue({ code: z.ZodIssueCode.custom, path: ["end_date"], message: "End date must be on or after the start date." });
    }
    if (v.geo_mode === "require" && !v.places.length) {
      ctx.addIssue({ code: z.ZodIssueCode.custom, path: ["places"], message: "Pick at least one place, or switch to “Boost”." });
    }
    if (v.restrict_to_sources && !v.sources.length) {
      ctx.addIssue({ code: z.ZodIssueCode.custom, path: ["sources"], message: "Pick at least one source, or turn off the restriction." });
    }
  });
type Values = z.infer<typeof schema>;

function defaults(a?: AssignmentOut): Values {
  return {
    headline: a?.headline ?? "",
    description: a?.description ?? "",
    keywords: (a?.keywords ?? [])
      .filter((k): k is typeof k & { kind: KeywordValue["kind"] } => (KINDS as readonly string[]).includes(k.kind))
      .map((k) => ({ term: k.term, kind: k.kind, weight: k.weight })),
    category_id: a?.category_id ?? "",
    language: a?.language ?? "",
    priority: (["low", "normal", "high", "urgent"].includes(a?.priority ?? "") ? a!.priority : "normal") as Values["priority"],
    status: a?.status === "active" ? "active" : a ? "paused" : "active",
    start_date: a?.start_date ?? "",
    end_date: a?.end_date ?? "",
    interval_minutes: a?.monitoring_interval_seconds ? String(Math.round(a.monitoring_interval_seconds / 60)) : "",
    min_score: a?.min_score ?? 25,
    restrict_to_sources: a?.restrict_to_sources ?? false,
    geo_mode: a?.geo_mode === "require" ? "require" : "boost",
    sources: a?.sources ?? [],
    places: a?.geographies ?? [],
  };
}

function toBody(v: Values, city: string | null): AssignmentIn {
  return {
    headline: v.headline.trim(),
    description: v.description.trim() || null,
    keywords: v.keywords.map((k) => ({ term: k.term, kind: k.kind, weight: k.weight })),
    category_id: v.category_id || null,
    language: v.language.trim().toLowerCase() || null,
    priority: v.priority,
    status: v.status,
    start_date: v.start_date || null,
    end_date: v.end_date || null,
    monitoring_interval_seconds: v.interval_minutes.trim() ? Number(v.interval_minutes) * 60 : null,
    min_score: v.min_score,
    restrict_to_sources: v.restrict_to_sources,
    geo_mode: v.geo_mode,
    city,
    source_ids: v.sources.map((s) => s.id),
    geography_ids: v.places.map((p) => p.id),
  };
}

/** Builds a dry-run body from the live (possibly incomplete) form, or says why it cannot yet. */
function previewBody(v: Partial<Values>, city: string | null): { body: AssignmentIn | null; reason?: string } {
  const keywords = v.keywords ?? [];
  if (!keywords.some((k) => k.kind === "primary" || k.kind === "synonym" || k.kind === "required")) {
    return { body: null, reason: "Add a primary keyword, synonym or required phrase to see which recent reports would match." };
  }
  if (v.geo_mode === "require" && !v.places?.length) return { body: null, reason: "Pick a place, or switch place matching to “Boost”." };
  if (v.restrict_to_sources && !v.sources?.length) return { body: null, reason: "Pick a source, or turn off “Only these sources”." };
  if (v.start_date && v.end_date && v.end_date < v.start_date) return { body: null, reason: "Fix the date range to preview." };
  const lang = (v.language ?? "").trim();
  const headline = (v.headline ?? "").trim();
  const min = typeof v.min_score === "number" && Number.isFinite(v.min_score) ? Math.min(Math.max(v.min_score, 0), 1000) : 25;
  return {
    body: toBody(
      {
        ...(v as Values),
        // The preview endpoint validates the headline but does not score on it.
        headline: headline.length >= 3 ? headline : "Draft headline",
        description: "",
        language: lang.length <= 16 ? lang : "",
        interval_minutes: "",
        min_score: min,
        keywords,
        sources: v.sources ?? [],
        places: v.places ?? [],
      },
      city,
    ),
  };
}

export function AssignmentForm({ initial }: { initial?: AssignmentOut }) {
  const router = useRouter();
  const qc = useQueryClient();
  const { can } = useSession();
  const write = can(PERMS.assignmentsWrite);
  const [serverError, setServerError] = useState<string | null>(null);
  const city = initial?.city ?? null;

  const form = useForm<Values>({ resolver: zodResolver(schema), defaultValues: defaults(initial), mode: "onTouched" });
  const { register, handleSubmit, control, formState } = form;
  const errors = formState.errors;

  const categories = useQuery({ queryKey: ["categories"], queryFn: () => api<CategoryOut[]>("/categories"), staleTime: 300_000 });
  const settings = useQuery({
    queryKey: ["settings"],
    queryFn: () => api<{ values: SettingsMap; defaults: SettingsMap }>("/settings"),
    staleTime: 300_000,
  });
  const weights = useMemo(() => {
    const d = (settings.data?.defaults?.["matching.weights"] ?? {}) as Record<string, number>;
    const v = (settings.data?.values?.["matching.weights"] ?? {}) as Record<string, number>;
    return { ...DEFAULT_WEIGHTS, ...d, ...v };
  }, [settings.data]);
  const excludedMode = String(
    (settings.data?.values?.["matching.options"] as Record<string, unknown> | undefined)?.excluded_mode ??
      (settings.data?.defaults?.["matching.options"] as Record<string, unknown> | undefined)?.excluded_mode ??
      "reject",
  );

  const live = useWatch({ control }) as Partial<Values>;
  const preview = useMemo(() => previewBody(live, city), [live, city]);

  // Warn before leaving with unsaved edits.
  const saved = useRef(false);
  useEffect(() => {
    if (!formState.isDirty) return;
    const onBefore = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = "";
    };
    const guarded = (e: BeforeUnloadEvent) => {
      if (!saved.current) onBefore(e);
    };
    window.addEventListener("beforeunload", guarded);
    return () => window.removeEventListener("beforeunload", guarded);
  }, [formState.isDirty]);

  const save = useMutation({
    mutationFn: (body: AssignmentIn) =>
      initial
        ? api<AssignmentOut>(`/assignments/${initial.id}`, { method: "PUT", body, query: { rematch: true } })
        : api<AssignmentOut>("/assignments", { method: "POST", body, query: { backfill: true } }),
    onSuccess: (a) => {
      saved.current = true;
      toast.success(initial ? "Headline saved — recent reports re-scored" : "Headline created — recent reports scored");
      qc.invalidateQueries({ queryKey: ["assignments"] });
      qc.invalidateQueries({ queryKey: ["assignment", a.id] });
      qc.invalidateQueries({ queryKey: ["research", a.id] });
      qc.invalidateQueries({ queryKey: ["matches", a.id] });
      qc.invalidateQueries({ queryKey: ["overview"] });
      router.push(`/research/${a.id}`);
    },
    onError: (e) => {
      const msg = errorMessage(e);
      setServerError(msg);
      toast.error(msg);
    },
  });

  const onSubmit = (v: Values) => {
    setServerError(null);
    return save.mutateAsync(toBody(v, city)).catch(() => undefined);
  };

  const places = useWatch({ control, name: "places" });
  const geoMode = useWatch({ control, name: "geo_mode" });
  const restrict = useWatch({ control, name: "restrict_to_sources" });
  const sourcesPicked = useWatch({ control, name: "sources" });
  const archived = initial?.status === "archived";
  const busy = formState.isSubmitting || save.isPending;

  return (
    <form onSubmit={handleSubmit(onSubmit)} noValidate className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(320px,420px)]">
      <div className="flex min-w-0 flex-col gap-4">
        {!write ? <InlineAlert tone="info">You can view these rules, but your role cannot change headlines.</InlineAlert> : null}
        {archived ? (
          <InlineAlert tone="info" title="This headline is archived">
            Saving updates its rules but keeps it archived. Restore it from My Headlines to resume monitoring.
          </InlineAlert>
        ) : null}
        {serverError ? (
          <InlineAlert tone="danger" title="Could not save">
            {serverError}
          </InlineAlert>
        ) : null}

        <fieldset disabled={!write} className="flex min-w-0 flex-col gap-4">
          <Card>
            <CardHeader icon={Newspaper} title="Story" description="What you are following and how urgent it is." />
            <CardBody className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <Field label="Headline" htmlFor="a-headline" required error={errors.headline?.message} className="sm:col-span-2">
                <Input id="a-headline" autoFocus={!initial} placeholder="e.g. Garment worker wage protests in Bangladesh" {...register("headline")} />
              </Field>
              <Field label="Brief" htmlFor="a-description" hint="The angle, what to look for, who to call. Shown in the research workspace." className="sm:col-span-2" error={errors.description?.message}>
                <Textarea id="a-description" rows={3} {...register("description")} />
              </Field>
              <Field label="Category" htmlFor="a-category" hint="Reports filed under this category score a little higher.">
                <Select id="a-category" {...register("category_id")}>
                  <option value="">Any category</option>
                  {categories.data?.map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.name}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field label="Language" htmlFor="a-language" hint="Optional code (en, fr, bn); matching reports score higher." error={errors.language?.message}>
                <Input id="a-language" maxLength={16} placeholder="Any language" {...register("language")} />
              </Field>
              <Field label="Priority" htmlFor="a-priority">
                <Select id="a-priority" {...register("priority")}>
                  <option value="urgent">Urgent</option>
                  <option value="high">High</option>
                  <option value="normal">Normal</option>
                  <option value="low">Low</option>
                </Select>
              </Field>
              <Field label="Monitoring" htmlFor="a-status" hint={archived ? "Archived headlines stay archived when saved." : "Paused headlines keep their matches but stop collecting new ones."}>
                {archived ? (
                  <Input id="a-status" readOnly value="Archived" />
                ) : (
                  <Select id="a-status" {...register("status")}>
                    <option value="active">Active — match new reports</option>
                    <option value="paused">Paused</option>
                  </Select>
                )}
              </Field>
            </CardBody>
          </Card>

          <Card>
            <CardHeader
              icon={KeyRound}
              title="Keywords"
              description="Matching is accent- and case-insensitive and treats simple plurals as the same word (protest, protests). Click a term’s slider to change its points."
            />
            <CardBody>
              <Controller
                control={control}
                name="keywords"
                render={({ field }) => (
                  <KeywordEditor
                    value={field.value as KeywordValue[]}
                    onChange={(next) => field.onChange(next)}
                    weights={weights}
                    excludedMode={excludedMode}
                    disabled={!write}
                    error={(errors.keywords as { message?: string } | undefined)?.message ?? errors.keywords?.root?.message}
                  />
                )}
              />
            </CardBody>
          </Card>

          <Card>
            <CardHeader
              icon={MapPin}
              title="Places"
              description="Places detected in the report text — not where the publisher is based. A country includes its regions and cities."
            />
            <CardBody className="flex flex-col gap-3">
              <Controller
                control={control}
                name="places"
                render={({ field }) => (
                  <div className="flex flex-col gap-2">
                    {field.value.length ? (
                      <ul className="flex flex-wrap gap-1.5" aria-label="Selected places">
                        {field.value.map((p) => (
                          <li key={p.id}>
                            <PlaceChip place={p} onRemove={write ? () => field.onChange(field.value.filter((x) => x.id !== p.id)) : undefined} />
                          </li>
                        ))}
                      </ul>
                    ) : null}
                    <Field label="Add a place" htmlFor="a-place" error={(errors.places as { message?: string } | undefined)?.message}>
                      <PlacePicker
                        id="a-place"
                        disabled={!write}
                        exclude={field.value.map((p) => p.id)}
                        placeholder="Search countries, regions, cities or custom areas…"
                        onSelect={(p) => field.onChange([...field.value, p])}
                      />
                    </Field>
                  </div>
                )}
              />
              <fieldset className="flex flex-col gap-2">
                <legend className="mb-1 text-sm font-medium">How places affect matching</legend>
                {(
                  [
                    { value: "boost", title: "Boost", body: `Reports about these places score +${weights.geography ?? 20}; others can still match on keywords.` },
                    { value: "require", title: "Require", body: "Only reports about at least one of these places can match." },
                  ] as const
                ).map((o) => (
                  <label
                    key={o.value}
                    className={cn(
                      "flex cursor-pointer items-start gap-2 rounded border px-3 py-2 text-sm",
                      geoMode === o.value ? "border-accent bg-accent/5" : "border-border",
                    )}
                  >
                    <input type="radio" value={o.value} className="mt-0.5 accent-[rgb(var(--accent))]" {...register("geo_mode")} />
                    <span>
                      <span className="font-medium">{o.title}</span>
                      <span className="block text-xs text-muted">{o.body}</span>
                    </span>
                  </label>
                ))}
                {geoMode === "require" && !places?.length ? <p className="text-xs text-warning">Pick at least one place for “Require” to work.</p> : null}
              </fieldset>
            </CardBody>
          </Card>

          <Card>
            <CardHeader icon={Rss} title="Sources" description="Optional. Picked sources score higher and can be polled more often for this story." />
            <CardBody className="flex flex-col gap-3">
              <Controller
                control={control}
                name="sources"
                render={({ field }) => (
                  <Field label="Sources to favour" htmlFor="a-sources" error={(errors.sources as { message?: string } | undefined)?.message}>
                    <SourcePicker id="a-sources" value={field.value} onChange={field.onChange} disabled={!write} />
                  </Field>
                )}
              />
              <Checkbox
                label={<>Only match reports from these sources{restrict && !sourcesPicked?.length ? <span className="text-warning"> (pick at least one)</span> : null}</>}
                {...register("restrict_to_sources")}
              />
            </CardBody>
          </Card>

          <Card>
            <CardHeader icon={CalendarClock} title="Timing and threshold" />
            <CardBody className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <Field label="Start date" htmlFor="a-start" hint="Ignore reports published before this day.">
                <Input id="a-start" type="date" {...register("start_date")} />
              </Field>
              <Field label="End date" htmlFor="a-end" hint="Ignore reports published after this day." error={errors.end_date?.message}>
                <Input id="a-end" type="date" {...register("end_date")} />
              </Field>
              <Field
                label="Check sources at least every (minutes)"
                htmlFor="a-interval"
                error={errors.interval_minutes?.message}
                hint="While active, picked sources (or all sources, if none are picked) are fetched at least this often. Blank uses each source’s own schedule."
              >
                <Input id="a-interval" inputMode="numeric" placeholder="Source default" {...register("interval_minutes")} />
              </Field>
              <Field
                label="Minimum score"
                htmlFor="a-min"
                error={errors.min_score?.message}
                hint="Reports scoring below this are not matched. Use the preview’s near-miss count to tune it."
              >
                <Input id="a-min" type="number" min={0} max={1000} step={1} {...register("min_score", { valueAsNumber: true })} />
              </Field>
            </CardBody>
          </Card>
        </fieldset>

        <div className="sticky bottom-0 z-10 -mx-1 flex items-center justify-end gap-2 border-t border-border bg-bg/95 px-1 py-3 backdrop-blur">
          {formState.isDirty ? <span className="mr-auto text-xs text-muted">Unsaved changes</span> : null}
          <Button type="button" variant="ghost" onClick={() => router.back()}>
            Cancel
          </Button>
          {write ? (
            <Button type="submit" variant="primary" loading={busy}>
              <Save className="h-4 w-4" /> {initial ? "Save and re-score" : "Create headline"}
            </Button>
          ) : null}
        </div>
      </div>

      <aside className="min-w-0 lg:sticky lg:top-16 lg:self-start">
        <PreviewPanel body={preview.body} notReady={preview.reason} />
      </aside>
    </form>
  );
}
