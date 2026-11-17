"use client";

import { X } from "lucide-react";
import { useId, useState } from "react";

import { cn } from "@/lib/utils";

/** Free-text tag chips: Enter or comma adds, Backspace on an empty field removes the last one. */
export function TagInput({
  id,
  value,
  onChange,
  suggestions = [],
  disabled,
}: {
  id?: string;
  value: string[];
  onChange: (tags: string[]) => void;
  suggestions?: string[];
  disabled?: boolean;
}) {
  const [text, setText] = useState("");
  const listId = useId();

  const add = (raw: string) => {
    const parts = raw
      .split(",")
      .map((t) => t.trim().slice(0, 80))
      .filter(Boolean);
    if (!parts.length) return;
    const lower = new Set(value.map((t) => t.toLowerCase()));
    const next = [...value];
    for (const p of parts) {
      if (!lower.has(p.toLowerCase())) {
        next.push(p);
        lower.add(p.toLowerCase());
      }
    }
    onChange(next);
    setText("");
  };

  return (
    <div
      className={cn(
        "flex min-h-8 flex-wrap items-center gap-1 rounded border border-border bg-surface px-1.5 py-1 focus-within:ring-2 focus-within:ring-accent/70",
        disabled && "opacity-60",
      )}
    >
      {value.map((t) => (
        <span key={t} className="inline-flex items-center gap-1 rounded bg-surface-2 px-1.5 py-0.5 text-xs">
          {t}
          {!disabled ? (
            <button
              type="button"
              onClick={() => onChange(value.filter((x) => x !== t))}
              className="rounded text-muted hover:text-text"
              aria-label={`Remove tag ${t}`}
            >
              <X className="h-3 w-3" />
            </button>
          ) : null}
        </span>
      ))}
      <input
        id={id}
        list={listId}
        value={text}
        disabled={disabled}
        onChange={(e) => {
          const v = e.target.value;
          if (v.endsWith(",")) add(v);
          else setText(v);
        }}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            add(text);
          } else if (e.key === "Backspace" && !text && value.length) {
            onChange(value.slice(0, -1));
          }
        }}
        onBlur={() => add(text)}
        placeholder={value.length ? "" : "Add tags…"}
        className="h-6 min-w-[8rem] flex-1 bg-transparent px-1 text-sm outline-none placeholder:text-muted/70"
        aria-label="Add a tag"
      />
      <datalist id={listId}>
        {suggestions
          .filter((s) => !value.includes(s))
          .slice(0, 50)
          .map((s) => (
            <option key={s} value={s} />
          ))}
      </datalist>
    </div>
  );
}
