"use client";

import { useQuery } from "@tanstack/react-query";
import { Loader2, MapPin, Search, X } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { api } from "@/lib/api";
import type { GeoEntityOut } from "@/lib/types";
import { cn, flag } from "@/lib/utils";

export type PlaceKind = "continent" | "country" | "admin1" | "admin2" | "city" | "custom";

const KIND_LABEL: Record<string, string> = {
  continent: "Continent",
  country: "Country",
  admin1: "Region",
  admin2: "District",
  city: "City",
  custom: "Custom area",
};

export function kindLabel(kind: string): string {
  return KIND_LABEL[kind] ?? kind;
}

function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

/** Searchable gazetteer picker (countries, regions, cities, custom areas). Keyboard: ↑ ↓ Enter Esc. */
export function PlacePicker({
  onSelect,
  exclude = [],
  kinds,
  placeholder = "Search places…",
  id,
  disabled,
  className,
}: {
  onSelect: (place: GeoEntityOut) => void;
  exclude?: string[];
  kinds?: PlaceKind[];
  placeholder?: string;
  id?: string;
  disabled?: boolean;
  className?: string;
}) {
  const listId = useId();
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const wrap = useRef<HTMLDivElement>(null);
  const term = useDebounced(q.trim(), 200);
  const { data, isFetching } = useQuery({
    queryKey: ["geo-search", term, kinds?.join(",") ?? ""],
    queryFn: ({ signal }) => api<GeoEntityOut[]>("/geography/entities", { query: { q: term, kind: kinds, limit: 12 }, signal }),
    enabled: open && term.length >= 2,
    staleTime: 300_000,
  });
  const results = (data ?? []).filter((p) => !exclude.includes(p.id));

  useEffect(() => {
    const onDoc = (e: MouseEvent) => {
      if (wrap.current && !wrap.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, []);

  useEffect(() => setActive(0), [term]);

  const choose = (p: GeoEntityOut) => {
    onSelect(p);
    setQ("");
    setOpen(false);
  };

  const showList = open && term.length >= 2;

  return (
    <div ref={wrap} className={cn("relative", className)}>
      <Search className="pointer-events-none absolute left-2.5 top-2 h-4 w-4 text-muted" aria-hidden />
      <Input
        id={id}
        value={q}
        disabled={disabled}
        onChange={(e) => {
          setQ(e.target.value);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown") {
            e.preventDefault();
            setActive((a) => Math.min(a + 1, Math.max(results.length - 1, 0)));
          } else if (e.key === "ArrowUp") {
            e.preventDefault();
            setActive((a) => Math.max(a - 1, 0));
          } else if (e.key === "Enter") {
            if (showList && results[active]) {
              e.preventDefault();
              choose(results[active]);
            }
          } else if (e.key === "Escape") {
            setOpen(false);
          }
        }}
        placeholder={placeholder}
        className="pl-8"
        role="combobox"
        aria-expanded={showList}
        aria-controls={listId}
        aria-autocomplete="list"
        autoComplete="off"
      />
      {isFetching ? <Loader2 className="absolute right-2.5 top-2 h-4 w-4 animate-spin text-muted" aria-hidden /> : null}
      {showList ? (
        <ul
          id={listId}
          role="listbox"
          className="scroll-thin absolute z-40 mt-1 max-h-64 w-full overflow-y-auto rounded-lg border border-border bg-surface p-1 shadow-pop"
        >
          {!isFetching && results.length === 0 ? (
            <li className="px-2 py-2 text-sm text-muted">No places match “{term}”.</li>
          ) : null}
          {results.map((p, i) => (
            <li
              key={p.id}
              role="option"
              aria-selected={i === active}
              onMouseDown={(e) => {
                e.preventDefault();
                choose(p);
              }}
              onMouseEnter={() => setActive(i)}
              className={cn("flex cursor-pointer items-center gap-2 rounded px-2 py-1.5 text-sm", i === active && "bg-surface-2")}
            >
              <MapPin className="h-3.5 w-3.5 shrink-0 text-muted" aria-hidden />
              <span className="min-w-0 flex-1 truncate">
                {p.country_code && p.kind !== "continent" ? `${flag(p.country_code)} ` : ""}
                {p.name}
              </span>
              <span className="shrink-0 text-2xs text-muted">
                {kindLabel(p.kind)}
                {p.country_code && p.kind !== "country" ? ` · ${p.country_code}` : ""}
              </span>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

export function PlaceChip({ place, onRemove }: { place: GeoEntityOut; onRemove?: () => void }) {
  return (
    <span className="inline-flex items-center gap-1 rounded border border-border bg-surface-2 py-0.5 pl-1.5 pr-0.5 text-xs">
      <MapPin className="h-3 w-3 text-muted" aria-hidden />
      <span>
        {place.country_code && place.kind !== "continent" ? `${flag(place.country_code)} ` : ""}
        {place.name}
      </span>
      <span className="text-2xs text-muted">{kindLabel(place.kind)}</span>
      {onRemove ? (
        <button
          type="button"
          onClick={onRemove}
          className="rounded p-0.5 text-muted hover:bg-border hover:text-text"
          aria-label={`Remove ${place.name}`}
        >
          <X className="h-3 w-3" />
        </button>
      ) : null}
    </span>
  );
}

export function PlaceBadge({ name, kind }: { name: string; kind?: string }) {
  return (
    <Badge tone="neutral" icon={MapPin}>
      {name}
      {kind ? ` · ${kindLabel(kind)}` : ""}
    </Badge>
  );
}
