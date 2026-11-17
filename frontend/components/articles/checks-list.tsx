import { AlertTriangle, CheckCircle2, Info, XCircle } from "lucide-react";

import type { LicenseWarning, PublicationCheck } from "@/lib/types";
import { cn } from "@/lib/utils";

const LEVEL = {
  error: { icon: XCircle, cls: "text-danger", label: "Blocks publishing" },
  warning: { icon: AlertTriangle, cls: "text-warning", label: "Warning" },
  info: { icon: Info, cls: "text-info", label: "Note" },
} as const;

/** Pre-publication checks or licence warnings. Level is shown with an icon and text, not colour alone. */
export function ChecksList({
  checks,
  emptyText = "All checks pass.",
  compact,
}: {
  checks: (PublicationCheck | LicenseWarning)[];
  emptyText?: string;
  compact?: boolean;
}) {
  if (!checks.length) {
    return (
      <p className="flex items-center gap-2 text-sm text-success">
        <CheckCircle2 className="h-4 w-4" aria-hidden /> {emptyText}
      </p>
    );
  }
  const order = { error: 0, warning: 1, info: 2 } as const;
  const sorted = [...checks].sort((a, b) => order[a.level] - order[b.level]);
  return (
    <ul className={cn("flex flex-col", compact ? "gap-1" : "gap-1.5")}>
      {sorted.map((c, i) => {
        const spec = LEVEL[c.level] ?? LEVEL.info;
        return (
          <li key={`${c.code}-${i}`} className="flex gap-2 text-sm">
            <spec.icon className={cn("mt-0.5 h-4 w-4 shrink-0", spec.cls)} aria-hidden />
            <span>
              <span className="sr-only">{spec.label}: </span>
              {c.message}
            </span>
          </li>
        );
      })}
    </ul>
  );
}

export function asChecks(v: unknown): PublicationCheck[] {
  return Array.isArray(v) ? (v as PublicationCheck[]).filter((c) => c && typeof c.message === "string") : [];
}
