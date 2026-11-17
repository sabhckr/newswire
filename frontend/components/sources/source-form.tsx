"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FlaskConical, Plus } from "lucide-react";
import { useState } from "react";
import { Controller, useForm } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";

import { Button } from "@/components/ui/button";
import { Checkbox, Field, Input, Select, Textarea } from "@/components/ui/input";
import { InlineAlert } from "@/components/ui/states";
import { Switch } from "@/components/ui/switch";
import { api, errorMessage } from "@/lib/api";
import type { CategoryOut, SourceOut } from "@/lib/types";

import { ProbeResultPanel, type ProbeResult } from "./probe-result";

export const INTERVALS: { value: number; label: string }[] = [
  { value: 60, label: "Every minute" },
  { value: 120, label: "Every 2 minutes" },
  { value: 300, label: "Every 5 minutes" },
  { value: 600, label: "Every 10 minutes" },
  { value: 900, label: "Every 15 minutes" },
  { value: 1800, label: "Every 30 minutes" },
  { value: 3600, label: "Hourly" },
  { value: 7200, label: "Every 2 hours" },
  { value: 21600, label: "Every 6 hours" },
  { value: 43200, label: "Every 12 hours" },
  { value: 86400, label: "Daily" },
];

/** Field names the JSON API parser maps (backend/app/collectors/parsers.py DEFAULT_API_FIELDS). */
const API_FIELDS: { key: string; label: string; def: string }[] = [
  { key: "title", label: "Title", def: "title" },
  { key: "url", label: "Link", def: "url" },
  { key: "guid", label: "Unique id", def: "id" },
  { key: "published_at", label: "Published at", def: "published_at" },
  { key: "summary", label: "Summary", def: "description" },
  { key: "content", label: "Content", def: "content" },
  { key: "author", label: "Author", def: "author" },
  { key: "image", label: "Image", def: "image" },
  { key: "language", label: "Language", def: "language" },
  { key: "category", label: "Category", def: "category" },
  { key: "location", label: "Location", def: "location" },
];

const optionalUrl = z
  .string()
  .trim()
  .max(2048)
  .refine((v) => !v || /^https?:\/\/\S+$/i.test(v), "Enter a full http(s) URL");

const schema = z.object({
  name: z.string().trim().min(1, "Give the source a name").max(200),
  publisher: z.string().trim().min(1, "Who publishes it?").max(200),
  source_type: z.enum(["rss", "atom", "json"]),
  feed_url: z.string().trim().min(8, "Enter the feed URL").max(2048).regex(/^https?:\/\/\S+$/i, "Enter a full http(s) URL"),
  website_url: optionalUrl,
  country_code: z
    .string()
    .trim()
    .refine((v) => !v || /^[A-Za-z]{2}$/.test(v), "Two-letter ISO code, e.g. GB"),
  region: z.string().trim().max(120),
  city: z.string().trim().max(120),
  language: z
    .string()
    .trim()
    .max(16)
    .refine((v) => !v || /^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})?$/.test(v), "Language code, e.g. en or pt-BR"),
  category_ids: z.array(z.string()),
  polling_interval_seconds: z.number().int().min(60, "At least 60 seconds").max(7 * 86400),
  priority: z.number().int().min(1).max(5),
  is_active: z.boolean(),
  respect_robots: z.boolean(),
  extract_page_metadata: z.boolean(),
  reliability_notes: z.string(),
  terms_notes: z.string(),
  items_path: z.string().trim().max(200),
  fields: z.record(z.string()),
});

type Values = z.infer<typeof schema>;

function defaults(s?: SourceOut | null): Values {
  const cfg = (s?.api_config ?? {}) as { items_path?: string; fields?: Record<string, string> };
  return {
    name: s?.name ?? "",
    publisher: s?.publisher ?? "",
    source_type: (s?.source_type as Values["source_type"]) ?? "rss",
    feed_url: s?.feed_url ?? "",
    website_url: s?.website_url ?? "",
    country_code: s?.country_code ?? "",
    region: s?.region ?? "",
    city: s?.city ?? "",
    language: s?.language ?? "",
    category_ids: s?.categories.map((c) => c.id) ?? [],
    polling_interval_seconds: s?.polling_interval_seconds ?? 300,
    priority: s?.priority ?? 3,
    is_active: s?.is_active ?? true,
    respect_robots: s?.respect_robots ?? true,
    extract_page_metadata: s?.extract_page_metadata ?? false,
    reliability_notes: s?.reliability_notes ?? "",
    terms_notes: s?.terms_notes ?? "",
    items_path: cfg.items_path ?? "",
    fields: cfg.fields ?? {},
  };
}

function apiConfig(v: Values): Record<string, unknown> | null {
  if (v.source_type !== "json") return null;
  const fields = Object.fromEntries(Object.entries(v.fields).filter(([, x]) => x && x.trim()).map(([k, x]) => [k, x.trim()]));
  const cfg: Record<string, unknown> = {};
  if (v.items_path) cfg.items_path = v.items_path;
  if (Object.keys(fields).length) cfg.fields = fields;
  return Object.keys(cfg).length ? cfg : null;
}

const nul = (s: string) => (s.trim() ? s.trim() : null);

export function SourceForm({
  source,
  onSaved,
  onCancel,
}: {
  source?: SourceOut | null;
  onSaved: (s: SourceOut) => void;
  onCancel: () => void;
}) {
  const qc = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  const [probe, setProbe] = useState<ProbeResult | null>(null);
  const [newCategory, setNewCategory] = useState("");
  const categories = useQuery({ queryKey: ["categories"], queryFn: () => api<CategoryOut[]>("/categories") });
  const { register, handleSubmit, control, watch, getValues, setValue, formState } = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: defaults(source),
  });
  const type = watch("source_type");
  const errors = formState.errors;

  const test = useMutation({
    mutationFn: () => {
      const v = getValues();
      return api<ProbeResult>("/sources/test", {
        body: { feed_url: v.feed_url.trim(), source_type: v.source_type, api_config: apiConfig(v), respect_robots: v.respect_robots },
      });
    },
    onMutate: () => setProbe(null),
    onSuccess: (r) => {
      setProbe(r);
      if (r.ok && !getValues("name") && r.feed_title) setValue("name", r.feed_title.slice(0, 200));
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  const addCategory = useMutation({
    mutationFn: (name: string) => api<CategoryOut>("/categories", { body: { name } }),
    onSuccess: (c) => {
      qc.invalidateQueries({ queryKey: ["categories"] });
      setValue("category_ids", [...getValues("category_ids"), c.id]);
      setNewCategory("");
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  const onSubmit = async (v: Values) => {
    setError(null);
    const body = {
      name: v.name.trim(),
      publisher: v.publisher.trim(),
      source_type: v.source_type,
      feed_url: v.feed_url.trim(),
      website_url: nul(v.website_url),
      country_code: nul(v.country_code)?.toUpperCase() ?? null,
      region: nul(v.region),
      city: nul(v.city),
      language: nul(v.language)?.toLowerCase() ?? null,
      category_ids: v.category_ids,
      polling_interval_seconds: v.polling_interval_seconds,
      priority: v.priority,
      is_active: v.is_active,
      respect_robots: v.respect_robots,
      extract_page_metadata: v.extract_page_metadata,
      reliability_notes: nul(v.reliability_notes),
      terms_notes: nul(v.terms_notes),
      api_config: apiConfig(v),
    };
    try {
      const saved = source
        ? await api<SourceOut>(`/sources/${source.id}`, { method: "PUT", body })
        : await api<SourceOut>("/sources", { body });
      qc.invalidateQueries({ queryKey: ["sources"] });
      qc.invalidateQueries({ queryKey: ["source", saved.id] });
      toast.success(source ? "Source updated" : "Source added. The worker will fetch it on its next cycle.");
      onSaved(saved);
    } catch (e) {
      setError(errorMessage(e));
    }
  };

  const intervalKnown = INTERVALS.some((i) => i.value === watch("polling_interval_seconds"));

  return (
    <form onSubmit={handleSubmit(onSubmit)} className="flex flex-col gap-5" noValidate>
      {error ? <InlineAlert tone="danger">{error}</InlineAlert> : null}

      <fieldset className="grid gap-3 sm:grid-cols-[1fr_140px]">
        <legend className="sr-only">Feed</legend>
        <Field label="Feed URL" htmlFor="sf-feed" error={errors.feed_url?.message} required hint="RSS, Atom, JSON Feed or a public JSON API endpoint">
          <Input id="sf-feed" inputMode="url" placeholder="https://example.com/rss.xml" {...register("feed_url")} aria-invalid={!!errors.feed_url} />
        </Field>
        <Field label="Format" htmlFor="sf-type">
          <Select id="sf-type" {...register("source_type")}>
            <option value="rss">RSS</option>
            <option value="atom">Atom</option>
            <option value="json">JSON Feed / API</option>
          </Select>
        </Field>
        <div className="flex flex-wrap items-center gap-2 sm:col-span-2">
          <Button size="sm" variant="outline" onClick={() => test.mutate()} loading={test.isPending} disabled={!watch("feed_url")}>
            <FlaskConical className="h-3.5 w-3.5" /> Test feed
          </Button>
          <span className="text-xs text-muted">Fetches once and shows what would be collected. Nothing is stored.</span>
        </div>
        {probe ? (
          <div className="sm:col-span-2">
            <ProbeResultPanel result={probe} />
          </div>
        ) : null}
      </fieldset>

      {type === "json" ? (
        <fieldset className="rounded-lg border border-border p-3">
          <legend className="px-1 text-xs font-medium">JSON field mapping</legend>
          <p className="mb-3 text-xs text-muted">
            Leave blank for JSON Feed. For other APIs, give the dotted path to the list of items and the field names inside each item. Blank fields use the default shown.
          </p>
          <Field label="Items path" htmlFor="sf-items" hint="e.g. data.articles; blank if the response is the list itself" className="mb-3">
            <Input id="sf-items" placeholder="data.items" {...register("items_path")} />
          </Field>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
            {API_FIELDS.map((f) => (
              <Field key={f.key} label={f.label} htmlFor={`sf-f-${f.key}`}>
                <Input id={`sf-f-${f.key}`} placeholder={f.def} {...register(`fields.${f.key}` as const)} />
              </Field>
            ))}
          </div>
        </fieldset>
      ) : null}

      <fieldset className="grid gap-3 sm:grid-cols-2">
        <legend className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">Publisher</legend>
        <Field label="Source name" htmlFor="sf-name" error={errors.name?.message} required>
          <Input id="sf-name" placeholder="World news" {...register("name")} aria-invalid={!!errors.name} />
        </Field>
        <Field label="Publisher" htmlFor="sf-pub" error={errors.publisher?.message} required>
          <Input id="sf-pub" placeholder="Example News" {...register("publisher")} aria-invalid={!!errors.publisher} />
        </Field>
        <Field label="Website" htmlFor="sf-web" error={errors.website_url?.message}>
          <Input id="sf-web" inputMode="url" placeholder="https://example.com" {...register("website_url")} />
        </Field>
        <Field label="Language" htmlFor="sf-lang" error={errors.language?.message} hint="Used when the feed does not declare one">
          <Input id="sf-lang" placeholder="en" {...register("language")} />
        </Field>
        <Field label="Publisher country" htmlFor="sf-cc" error={errors.country_code?.message} hint="Where the publisher is based; not where stories happen">
          <Input id="sf-cc" placeholder="GB" maxLength={2} className="uppercase" {...register("country_code")} />
        </Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Region" htmlFor="sf-region">
            <Input id="sf-region" {...register("region")} />
          </Field>
          <Field label="City" htmlFor="sf-city">
            <Input id="sf-city" {...register("city")} />
          </Field>
        </div>
      </fieldset>

      <fieldset className="grid gap-3 sm:grid-cols-2">
        <legend className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">Collection</legend>
        <Field label="Polling interval" htmlFor="sf-interval" error={errors.polling_interval_seconds?.message}>
          <Select id="sf-interval" {...register("polling_interval_seconds", { valueAsNumber: true })}>
            {!intervalKnown ? <option value={watch("polling_interval_seconds")}>{watch("polling_interval_seconds")} seconds</option> : null}
            {INTERVALS.map((i) => (
              <option key={i.value} value={i.value}>
                {i.label}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Priority" htmlFor="sf-prio" hint="1 is fetched first when many sources are due">
          <Select id="sf-prio" {...register("priority", { valueAsNumber: true })}>
            {[1, 2, 3, 4, 5].map((p) => (
              <option key={p} value={p}>
                {p}
                {p === 1 ? " (highest)" : p === 5 ? " (lowest)" : ""}
              </option>
            ))}
          </Select>
        </Field>
        <div className="flex flex-col gap-2 sm:col-span-2">
          <Controller
            control={control}
            name="is_active"
            render={({ field }) => <Switch id="sf-active" checked={field.value} onCheckedChange={field.onChange} label="Active: collect on schedule" />}
          />
          <Controller
            control={control}
            name="respect_robots"
            render={({ field }) => (
              <Switch id="sf-robots" checked={field.value} onCheckedChange={field.onChange} label="Respect robots.txt (recommended)" />
            )}
          />
          <Controller
            control={control}
            name="extract_page_metadata"
            render={({ field }) => (
              <Switch
                id="sf-meta"
                checked={field.value}
                onCheckedChange={field.onChange}
                label="Fetch each article page for canonical URL, image and description (slower; one extra request per new item)"
              />
            )}
          />
        </div>
      </fieldset>

      <fieldset>
        <legend className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">Categories</legend>
        <Controller
          control={control}
          name="category_ids"
          render={({ field }) => (
            <div className="flex flex-wrap gap-x-4 gap-y-2">
              {categories.data?.map((c) => (
                <Checkbox
                  key={c.id}
                  label={c.name}
                  checked={field.value.includes(c.id)}
                  onChange={(e) => field.onChange(e.target.checked ? [...field.value, c.id] : field.value.filter((x) => x !== c.id))}
                />
              ))}
              {categories.data && !categories.data.length ? <span className="text-xs text-muted">No categories yet.</span> : null}
            </div>
          )}
        />
        <div className="mt-2 flex max-w-sm items-center gap-2">
          <Input
            aria-label="New category name"
            placeholder="New category"
            value={newCategory}
            onChange={(e) => setNewCategory(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                if (newCategory.trim()) addCategory.mutate(newCategory.trim());
              }
            }}
          />
          <Button size="sm" variant="outline" onClick={() => addCategory.mutate(newCategory.trim())} disabled={!newCategory.trim()} loading={addCategory.isPending}>
            <Plus className="h-3.5 w-3.5" /> Add
          </Button>
        </div>
      </fieldset>

      <fieldset className="grid gap-3 sm:grid-cols-2">
        <legend className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">Notes</legend>
        <Field label="Reliability notes" htmlFor="sf-rel" hint="Editorial notes about accuracy, bias or corrections policy">
          <Textarea id="sf-rel" rows={3} {...register("reliability_notes")} />
        </Field>
        <Field label="Terms of use" htmlFor="sf-terms" hint="Record what the publisher allows (quoting, attribution, rate limits)">
          <Textarea id="sf-terms" rows={3} {...register("terms_notes")} />
        </Field>
      </fieldset>

      <div className="sticky -bottom-4 -mx-4 -mb-4 flex justify-end gap-2 border-t border-border bg-surface px-4 py-3">
        <Button variant="outline" onClick={onCancel}>
          Cancel
        </Button>
        <Button type="submit" variant="primary" loading={formState.isSubmitting}>
          {source ? "Save changes" : "Add source"}
        </Button>
      </div>
    </form>
  );
}
