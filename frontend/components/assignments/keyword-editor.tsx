"use client";

import { Ban, Lock, Plus, SlidersHorizontal, X } from "lucide-react";
import { useId, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input, Label } from "@/components/ui/input";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { cn } from "@/lib/utils";

export type KeywordKind = "primary" | "synonym" | "required" | "secondary" | "excluded";
export interface KeywordValue {
  term: string;
  kind: KeywordKind;
  weight: number | null;
}

/** Backend defaults (services/settings.py) used until /settings loads. */
export const DEFAULT_WEIGHTS: Record<string, number> = {
  required_phrase: 30,
  primary: 15,
  synonym: 12,
  secondary: 7,
  excluded_penalty: -50,
};

const WEIGHT_KEY: Record<KeywordKind, string> = {
  required: "required_phrase",
  primary: "primary",
  synonym: "synonym",
  secondary: "secondary",
  excluded: "excluded_penalty",
};

const SECTIONS: { kind: KeywordKind; label: string; hint: string; placeholder: string }[] = [
  {
    kind: "primary",
    label: "Primary keywords",
    hint: "The core terms of the story. Headline hits score more than summary hits; close spellings and plurals count.",
    placeholder: "e.g. garment factory",
  },
  {
    kind: "synonym",
    label: "Synonyms and alternate spellings",
    hint: "Other names for the same thing — transliterations, abbreviations, local names.",
    placeholder: "e.g. RMG sector",
  },
  {
    kind: "required",
    label: "Required phrases",
    hint: "Every required phrase must appear (exact or inflected), or the report is not matched.",
    placeholder: "e.g. Bangladesh",
  },
  {
    kind: "secondary",
    label: "Secondary keywords",
    hint: "Supporting context. These add points but never match a report on their own.",
    placeholder: "e.g. wages",
  },
  {
    kind: "excluded",
    label: "Excluded keywords",
    hint: "",
    placeholder: "e.g. cricket",
  },
];

function splitTerms(raw: string): string[] {
  return raw
    .split(/[,\n;]/)
    .map((t) => t.trim().replace(/\s+/g, " "))
    .filter(Boolean)
    .map((t) => t.slice(0, 200));
}

export function KeywordEditor({
  value,
  onChange,
  weights,
  excludedMode,
  disabled,
  error,
}: {
  value: KeywordValue[];
  onChange: (next: KeywordValue[]) => void;
  weights: Record<string, number>;
  excludedMode: string;
  disabled?: boolean;
  error?: string;
}) {
  const add = (kind: KeywordKind, raw: string): string | null => {
    const terms = splitTerms(raw);
    if (!terms.length) return null;
    const have = new Set(value.map((k) => `${k.kind}:${k.term.toLowerCase()}`));
    const next = [...value];
    const dupes: string[] = [];
    for (const term of terms) {
      const key = `${kind}:${term.toLowerCase()}`;
      if (have.has(key)) dupes.push(term);
      else {
        have.add(key);
        next.push({ term, kind, weight: null });
      }
    }
    if (next.length !== value.length) onChange(next);
    return dupes.length ? `Already added: ${dupes.join(", ")}` : null;
  };
  const removeAt = (idx: number) => onChange(value.filter((_, i) => i !== idx));
  const setWeight = (idx: number, weight: number | null) => onChange(value.map((k, i) => (i === idx ? { ...k, weight } : k)));

  return (
    <div className="flex flex-col gap-4">
      {SECTIONS.map((s) => (
        <KeywordSection
          key={s.kind}
          section={{
            ...s,
            hint:
              s.kind === "excluded"
                ? excludedMode === "reject"
                  ? "A report containing any of these is rejected outright (Settings → Matching can switch this to a points penalty)."
                  : "Each one found subtracts points from the score."
                : s.hint,
          }}
          items={value.map((k, i) => ({ k, i })).filter(({ k }) => k.kind === s.kind)}
          defaultWeight={weights[WEIGHT_KEY[s.kind]] ?? DEFAULT_WEIGHTS[WEIGHT_KEY[s.kind]]}
          weightApplies={s.kind !== "excluded" || excludedMode !== "reject"}
          onAdd={(raw) => add(s.kind, raw)}
          onRemove={removeAt}
          onWeight={setWeight}
          disabled={disabled}
        />
      ))}
      {error ? (
        <p className="text-xs text-danger" role="alert">
          {error}
        </p>
      ) : null}
    </div>
  );
}

function KeywordSection({
  section,
  items,
  defaultWeight,
  weightApplies,
  onAdd,
  onRemove,
  onWeight,
  disabled,
}: {
  section: { kind: KeywordKind; label: string; hint: string; placeholder: string };
  items: { k: KeywordValue; i: number }[];
  defaultWeight: number;
  weightApplies: boolean;
  onAdd: (raw: string) => string | null;
  onRemove: (idx: number) => void;
  onWeight: (idx: number, w: number | null) => void;
  disabled?: boolean;
}) {
  const inputId = useId();
  const hintId = useId();
  const [draft, setDraft] = useState("");
  const [note, setNote] = useState<string | null>(null);
  const commit = () => {
    if (!draft.trim()) return;
    setNote(onAdd(draft));
    setDraft("");
  };
  const Icon = section.kind === "required" ? Lock : section.kind === "excluded" ? Ban : null;

  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-baseline justify-between gap-2">
        <Label htmlFor={inputId}>
          {section.label}
          {items.length ? <span className="tabular ml-1.5 text-xs font-normal text-muted">{items.length}</span> : null}
        </Label>
        {weightApplies ? (
          <span className="tabular text-2xs text-muted">
            {defaultWeight > 0 ? "+" : ""}
            {defaultWeight} pts each
          </span>
        ) : (
          <span className="text-2xs text-muted">rejects</span>
        )}
      </div>
      <div
        className={cn(
          "flex min-h-[2.25rem] flex-wrap items-center gap-1.5 rounded border border-border bg-surface px-1.5 py-1 focus-within:ring-2 focus-within:ring-accent/40",
          disabled && "opacity-60",
        )}
      >
        {items.map(({ k, i }) => (
          <span
            key={`${k.kind}:${k.term}`}
            className={cn(
              "inline-flex items-center gap-1 rounded border py-0.5 pl-1.5 pr-0.5 text-xs",
              section.kind === "required" && "border-accent/40 bg-accent/10",
              section.kind === "excluded" && "border-danger/40 bg-danger/10",
              section.kind === "secondary" && "border-border bg-surface",
              (section.kind === "primary" || section.kind === "synonym") && "border-border bg-surface-2",
            )}
          >
            {Icon ? <Icon className="h-3 w-3 text-muted" aria-hidden /> : null}
            <span>{k.term}</span>
            {weightApplies ? (
              <WeightPopover term={k.term} weight={k.weight} defaultWeight={defaultWeight} onChange={(w) => onWeight(i, w)} disabled={disabled} />
            ) : null}
            <button
              type="button"
              disabled={disabled}
              onClick={() => onRemove(i)}
              className="rounded p-0.5 text-muted hover:bg-border hover:text-text"
              aria-label={`Remove ${k.term}`}
            >
              <X className="h-3 w-3" />
            </button>
          </span>
        ))}
        <input
          id={inputId}
          value={draft}
          disabled={disabled}
          aria-describedby={hintId}
          onChange={(e) => {
            const v = e.target.value;
            if (/[,;\n]/.test(v)) {
              setNote(onAdd(v));
              setDraft("");
            } else {
              setDraft(v);
              if (note) setNote(null);
            }
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              commit();
            } else if (e.key === "Backspace" && !draft && items.length) {
              onRemove(items[items.length - 1].i);
            }
          }}
          onBlur={commit}
          placeholder={items.length ? "Add another…" : section.placeholder}
          className="h-6 min-w-[8rem] flex-1 bg-transparent px-1 text-sm outline-none placeholder:text-muted"
          autoComplete="off"
        />
        {draft.trim() ? (
          <Button type="button" variant="ghost" size="icon-sm" onMouseDown={(e) => e.preventDefault()} onClick={commit} aria-label={`Add ${draft.trim()}`}>
            <Plus className="h-3.5 w-3.5" />
          </Button>
        ) : null}
      </div>
      <p id={hintId} className={cn("text-xs", note ? "text-warning" : "text-muted")} role={note ? "status" : undefined}>
        {note ?? section.hint} {!note ? <span className="text-muted/80">Enter or comma adds a term.</span> : null}
      </p>
    </div>
  );
}

function WeightPopover({
  term,
  weight,
  defaultWeight,
  onChange,
  disabled,
}: {
  term: string;
  weight: number | null;
  defaultWeight: number;
  onChange: (w: number | null) => void;
  disabled?: boolean;
}) {
  const id = useId();
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState("");
  const parsed = draft.trim() === "" ? null : Number(draft);
  const invalid = parsed !== null && (!Number.isFinite(parsed) || parsed < -200 || parsed > 200);
  const apply = () => {
    if (invalid) return;
    onChange(parsed);
    setOpen(false);
  };
  return (
    <Popover
      open={open}
      onOpenChange={(o) => {
        if (o) setDraft(weight === null ? "" : String(weight));
        setOpen(o);
      }}
    >
      <PopoverTrigger asChild>
        <button
          type="button"
          disabled={disabled}
          className={cn(
            "tabular inline-flex items-center gap-0.5 rounded px-1 text-2xs hover:bg-border",
            weight === null ? "text-muted" : "font-semibold text-accent",
          )}
          aria-label={`Weight for ${term}: ${weight === null ? `default ${defaultWeight}` : weight}. Change weight`}
        >
          {weight === null ? <SlidersHorizontal className="h-2.5 w-2.5" aria-hidden /> : weight}
        </button>
      </PopoverTrigger>
      <PopoverContent align="start" className="flex w-60 flex-col gap-2 p-3">
        <Label htmlFor={id}>Points for “{term}”</Label>
        <Input
          id={id}
          type="number"
          step="any"
          min={-200}
          max={200}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              apply();
            }
          }}
          placeholder={`Default ${defaultWeight}`}
          autoFocus
        />
        <p className={cn("text-xs", invalid ? "text-danger" : "text-muted")}>
          {invalid ? "Use a number between −200 and 200." : "Leave blank to use the default from Settings → Matching."}
        </p>
        <div className="flex justify-end gap-2">
          {weight !== null ? (
            <Button
              type="button"
              size="sm"
              variant="ghost"
              onClick={() => {
                onChange(null);
                setOpen(false);
              }}
            >
              Use default
            </Button>
          ) : null}
          <Button type="button" size="sm" variant="primary" disabled={invalid} onClick={apply}>
            Apply
          </Button>
        </div>
      </PopoverContent>
    </Popover>
  );
}
