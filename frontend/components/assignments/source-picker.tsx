"use client";

import { useQuery } from "@tanstack/react-query";
import { Loader2, Rss, Search, X } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";

import { Input } from "@/components/ui/input";
import { api } from "@/lib/api";
import type { Page, SourceBrief, SourceOut } from "@/lib/types";
import { cn } from "@/lib/utils";

function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

/** Multi-select of configured sources. Keyboard: ↑ ↓ Enter Esc; Backspace on an empty box removes the last pick. */
export function SourcePicker({
  value,
  onChange,
  id,
  disabled,
}: {
  value: SourceBrief[];
  onChange: (next: SourceBrief[]) => void;
  id?: string;
  disabled?: boolean;
}) {
  const listId = useId();
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const wrap = useRef<HTMLDivElement>(null);
  const term = useDebounced(q.trim(), 200);
  const { data, isFetching, isError } = useQuery({
    queryKey: ["sources", "picker", term],
    queryFn: ({ signal }) => api<Page<SourceOut>>("/sources", { query: { q: term || undefined, page_size: 25 }, signal }),
    enabled: open,
    staleTime: 60_000,
  });
  const chosen = new Set(value.map((s) => s.id));
  const results = (data?.items ?? []).filter((s) => !chosen.has(s.id));

  useEffect(() => {
    const onDoc = (e: MouseEvent) => {
      if (wrap.current && !wrap.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, []);
  useEffect(() => setActive(0), [term]);

  const choose = (s: SourceOut) => {
    onChange([...value, { id: s.id, name: s.name, publisher: s.publisher }]);
    setQ("");
  };

  return (
    <div className="flex flex-col gap-2">
      {value.length ? (
        <ul className="flex flex-wrap gap-1.5" aria-label="Selected sources">
          {value.map((s) => (
            <li key={s.id} className="inline-flex items-center gap-1 rounded border border-border bg-surface-2 py-0.5 pl-1.5 pr-0.5 text-xs">
              <Rss className="h-3 w-3 text-muted" aria-hidden />
              <span>{s.name}</span>
              {s.publisher && s.publisher !== s.name ? <span className="text-2xs text-muted">{s.publisher}</span> : null}
              <button
                type="button"
                disabled={disabled}
                onClick={() => onChange(value.filter((x) => x.id !== s.id))}
                className="rounded p-0.5 text-muted hover:bg-border hover:text-text"
                aria-label={`Remove ${s.name}`}
              >
                <X className="h-3 w-3" />
              </button>
            </li>
          ))}
        </ul>
      ) : null}
      <div ref={wrap} className="relative">
        <Search className="pointer-events-none absolute left-2.5 top-2 h-4 w-4 text-muted" aria-hidden />
        <Input
          id={id}
          value={q}
          disabled={disabled}
          placeholder="Search your sources…"
          className="pl-8"
          role="combobox"
          aria-expanded={open}
          aria-controls={listId}
          aria-autocomplete="list"
          autoComplete="off"
          onFocus={() => setOpen(true)}
          onChange={(e) => {
            setQ(e.target.value);
            setOpen(true);
          }}
          onKeyDown={(e) => {
            if (e.key === "ArrowDown") {
              e.preventDefault();
              setActive((a) => Math.min(a + 1, Math.max(results.length - 1, 0)));
            } else if (e.key === "ArrowUp") {
              e.preventDefault();
              setActive((a) => Math.max(a - 1, 0));
            } else if (e.key === "Enter") {
              if (open && results[active]) {
                e.preventDefault();
                choose(results[active]);
              }
            } else if (e.key === "Escape") {
              setOpen(false);
            } else if (e.key === "Backspace" && !q && value.length) {
              onChange(value.slice(0, -1));
            }
          }}
        />
        {isFetching ? <Loader2 className="absolute right-2.5 top-2 h-4 w-4 animate-spin text-muted" aria-hidden /> : null}
        {open ? (
          <ul id={listId} role="listbox" className="scroll-thin absolute z-40 mt-1 max-h-64 w-full overflow-y-auto rounded-lg border border-border bg-surface p-1 shadow-pop">
            {isError ? <li className="px-2 py-2 text-sm text-danger">Could not load sources.</li> : null}
            {!isFetching && !isError && results.length === 0 ? (
              <li className="px-2 py-2 text-sm text-muted">{term ? `No sources match “${term}”.` : data?.items.length ? "All sources are selected." : "No sources configured yet."}</li>
            ) : null}
            {results.map((s, i) => (
              <li
                key={s.id}
                role="option"
                aria-selected={i === active}
                onMouseDown={(e) => {
                  e.preventDefault();
                  choose(s);
                }}
                onMouseEnter={() => setActive(i)}
                className={cn("flex cursor-pointer items-center gap-2 rounded px-2 py-1.5 text-sm", i === active && "bg-surface-2")}
              >
                <Rss className="h-3.5 w-3.5 shrink-0 text-muted" aria-hidden />
                <span className="min-w-0 flex-1 truncate">{s.name}</span>
                <span className="shrink-0 text-2xs text-muted">
                  {s.publisher}
                  {s.is_active ? "" : " · disabled"}
                </span>
              </li>
            ))}
          </ul>
        ) : null}
      </div>
    </div>
  );
}
