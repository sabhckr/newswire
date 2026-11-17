"use client";

import { ExternalLink, Link as LinkIcon, Plus, Trash2 } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Field, Input } from "@/components/ui/input";
import { hostOf } from "@/lib/utils";

export type SourceDraft = { news_item_id: string | null; url: string; title: string; publisher: string; note: string };

/** Reference list for the article ("Sources" section in the published HTML). */
export function SourcesEditor({
  value,
  onChange,
  disabled,
}: {
  value: SourceDraft[];
  onChange: (v: SourceDraft[]) => void;
  disabled?: boolean;
}) {
  const [url, setUrl] = useState("");
  const [title, setTitle] = useState("");
  const [publisher, setPublisher] = useState("");
  const [error, setError] = useState<string | null>(null);

  const add = () => {
    const u = url.trim();
    let parsed: URL | null = null;
    try {
      parsed = new URL(/^https?:\/\//i.test(u) ? u : `https://${u}`);
    } catch {
      parsed = null;
    }
    if (!u || !parsed || !parsed.hostname.includes(".")) {
      setError("Enter the full address of the source, e.g. https://example.com/story");
      return;
    }
    if (value.some((s) => s.url === parsed!.toString())) {
      setError("That source is already listed");
      return;
    }
    onChange([...value, { news_item_id: null, url: parsed.toString(), title: title.trim(), publisher: publisher.trim(), note: "" }]);
    setUrl("");
    setTitle("");
    setPublisher("");
    setError(null);
  };

  return (
    <div className="flex flex-col gap-3">
      {value.length ? (
        <ol className="flex flex-col divide-y divide-border/70 rounded-lg border border-border">
          {value.map((s, i) => (
            <li key={`${s.url}-${i}`} className="flex items-start gap-3 px-3 py-2">
              <span className="tabular mt-0.5 w-5 shrink-0 text-xs text-muted">{i + 1}.</span>
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium">{s.title || hostOf(s.url)}</p>
                <p className="flex items-center gap-1 text-xs text-muted">
                  {s.publisher ? <span className="truncate">{s.publisher} ·</span> : null}
                  <a href={s.url} target="_blank" rel="noopener noreferrer nofollow" className="inline-flex min-w-0 items-center gap-1 hover:text-accent">
                    <span className="truncate">{hostOf(s.url)}</span>
                    <ExternalLink className="h-3 w-3 shrink-0" aria-hidden />
                  </a>
                  {s.news_item_id ? <span className="shrink-0">· from collected news</span> : null}
                </p>
              </div>
              {!disabled ? (
                <Button
                  size="icon-sm"
                  variant="ghost"
                  onClick={() => onChange(value.filter((_, j) => j !== i))}
                  aria-label={`Remove source ${s.title || s.url}`}
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </Button>
              ) : null}
            </li>
          ))}
        </ol>
      ) : (
        <p className="flex items-center gap-2 text-sm text-muted">
          <LinkIcon className="h-4 w-4" aria-hidden /> No sources yet. Verified matches are added automatically when you start an article from research.
        </p>
      )}
      {!disabled ? (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            add();
          }}
          className="grid gap-2 rounded-lg border border-dashed border-border p-3 sm:grid-cols-[2fr_2fr_1.3fr_auto] sm:items-end"
        >
          <Field label="Source URL" htmlFor="src-url" error={error ?? undefined}>
            <Input id="src-url" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://…" aria-invalid={!!error} />
          </Field>
          <Field label="Title" htmlFor="src-title">
            <Input id="src-title" value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Optional" />
          </Field>
          <Field label="Publisher" htmlFor="src-pub">
            <Input id="src-pub" value={publisher} onChange={(e) => setPublisher(e.target.value)} placeholder="Optional" />
          </Field>
          <Button type="submit" variant="secondary" className={error ? "sm:mb-5" : undefined}>
            <Plus className="h-4 w-4" /> Add
          </Button>
        </form>
      ) : null}
    </div>
  );
}
