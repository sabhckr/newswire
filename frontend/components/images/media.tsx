"use client";

import { AlertTriangle, ImageOff, Info, XCircle } from "lucide-react";
import { useState } from "react";

import { Badge, type Tone } from "@/components/ui/badge";
import type { ImageOut, LicenseWarning } from "@/lib/types";
import { cn } from "@/lib/utils";

/**
 * Stored media URLs are absolute (public_base_url + /media/…). The Next server proxies /media to the
 * backend, so strip the origin and load them from this origin; that keeps them working whatever
 * public_base_url is set to.
 */
export function mediaSrc(url: string | null | undefined): string | null {
  if (!url) return null;
  return url.replace(/^https?:\/\/[^/]+(?=\/media\/)/, "");
}

export function previewSrc(img: Pick<ImageOut, "local_thumbnail_url" | "thumbnail_url" | "local_url">, full = false): string | null {
  if (full) return mediaSrc(img.local_url) ?? mediaSrc(img.local_thumbnail_url) ?? img.thumbnail_url ?? null;
  return mediaSrc(img.local_thumbnail_url) ?? img.thumbnail_url ?? mediaSrc(img.local_url) ?? null;
}

export function imageWarnings(img: { warnings?: unknown[] }): LicenseWarning[] {
  return (img.warnings ?? []) as LicenseWarning[];
}

const LICENCE_TONE: Record<string, Tone> = {
  cc0: "success",
  pdm: "success",
  own: "success",
  by: "info",
  "by-sa": "info",
  "by-nd": "warning",
  "by-nc": "warning",
  "by-nc-sa": "warning",
  "by-nc-nd": "warning",
  other: "danger",
  unknown: "danger",
};

export function LicenceBadge({ code, name, className }: { code: string | null | undefined; name?: string | null; className?: string }) {
  const c = code ?? "unknown";
  const tone = LICENCE_TONE[c] ?? "danger";
  return (
    <Badge tone={tone} icon={tone === "danger" ? AlertTriangle : undefined} className={className}>
      {name || (c === "unknown" ? "Unknown licence" : c.toUpperCase())}
    </Badge>
  );
}

const LEVEL = {
  error: { icon: XCircle, cls: "text-danger", label: "Blocks use" },
  warning: { icon: AlertTriangle, cls: "text-warning", label: "Warning" },
  info: { icon: Info, cls: "text-info", label: "Note" },
} as const;

export function WarningList({ warnings, className }: { warnings: LicenseWarning[]; className?: string }) {
  if (!warnings.length) return null;
  return (
    <ul className={cn("flex flex-col gap-1", className)}>
      {warnings.map((w) => {
        const l = LEVEL[w.level] ?? LEVEL.info;
        const Icon = l.icon;
        return (
          <li key={w.code} className="flex items-start gap-1.5 text-xs">
            <Icon className={cn("mt-px h-3.5 w-3.5 shrink-0", l.cls)} aria-hidden />
            <span>
              <span className={cn("font-medium", l.cls)}>{l.label}:</span> {w.message}
            </span>
          </li>
        );
      })}
    </ul>
  );
}

/** Thumbnail with a neutral fallback when the remote or local file cannot be loaded. */
export function Thumb({ src, alt, className, imgClassName }: { src: string | null; alt: string; className?: string; imgClassName?: string }) {
  const [failed, setFailed] = useState(false);
  return (
    <div className={cn("flex items-center justify-center overflow-hidden bg-surface-2", className)}>
      {src && !failed ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={src}
          alt={alt}
          loading="lazy"
          decoding="async"
          referrerPolicy="no-referrer"
          onError={() => setFailed(true)}
          className={cn("h-full w-full object-cover", imgClassName)}
        />
      ) : (
        <span className="flex flex-col items-center gap-1 text-2xs text-muted">
          <ImageOff className="h-5 w-5" aria-hidden />
          No preview
        </span>
      )}
    </div>
  );
}
