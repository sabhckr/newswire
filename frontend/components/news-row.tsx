"use client";

import { Copy, ExternalLink, Layers, Target } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Time } from "@/components/ui/time";
import { Tip } from "@/components/ui/tooltip";
import type { NewsItemOut } from "@/lib/types";
import { cn, flag, hostOf } from "@/lib/utils";

/** Compact one-row rendering of a collected news item, used across pages. */
export function NewsRow({
  item,
  onOpen,
  active,
  extra,
}: {
  item: NewsItemOut;
  onOpen?: (item: NewsItemOut) => void;
  active?: boolean;
  extra?: React.ReactNode;
}) {
  const when = item.published_at ?? item.retrieved_at;
  return (
    <div
      className={cn(
        "group flex gap-3 border-b border-border/70 px-3 py-2.5 last:border-0 hover:bg-surface-2/60",
        active && "bg-accent/5",
        item.is_duplicate && "opacity-70",
      )}
    >
      <div className="min-w-0 flex-1">
        <button
          type="button"
          onClick={() => onOpen?.(item)}
          className="block w-full text-left text-sm font-medium leading-snug hover:text-accent"
        >
          {item.title}
        </button>
        <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted">
          <span className="font-medium text-text/80">{item.publisher ?? hostOf(item.url)}</span>
          <span aria-hidden>·</span>
          <Time value={when} />
          {!item.published_at ? (
            <Tip content="The feed did not give a publication time; this is when NEWSWIRE retrieved it.">
              <span className="cursor-help underline decoration-dotted">retrieved</span>
            </Tip>
          ) : null}
          {item.countries?.length ? (
            <span title={item.countries.join(", ")}>
              {item.countries.slice(0, 4).map((c) => `${flag(c)} ${c}`).join("  ")}
              {item.countries.length > 4 ? ` +${item.countries.length - 4}` : ""}
            </span>
          ) : null}
          {item.language ? <span className="uppercase">{item.language}</span> : null}
          {item.match_count ? (
            <Badge tone="info" icon={Target}>
              {item.match_count} match{item.match_count === 1 ? "" : "es"}
            </Badge>
          ) : null}
          {item.cluster_id ? <Layers className="h-3 w-3" aria-label="In a story cluster" /> : null}
          {item.is_duplicate ? (
            <Badge tone="warning" icon={Copy}>
              Duplicate
            </Badge>
          ) : null}
          {extra}
        </div>
      </div>
      <a
        href={item.url}
        target="_blank"
        rel="noopener noreferrer nofollow"
        className="mt-0.5 shrink-0 self-start rounded p-1 text-muted opacity-60 hover:bg-surface-2 hover:text-text group-hover:opacity-100"
        aria-label={`Open original article at ${hostOf(item.url)}`}
      >
        <ExternalLink className="h-3.5 w-3.5" />
      </a>
    </div>
  );
}
