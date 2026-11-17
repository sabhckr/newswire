import { ChevronLeft, ChevronRight } from "lucide-react";

import { cn, formatNumber } from "@/lib/utils";

import { Button } from "./button";

export function PageHeader({
  title,
  description,
  actions,
  className,
  children,
}: {
  title: React.ReactNode;
  description?: React.ReactNode;
  actions?: React.ReactNode;
  className?: string;
  children?: React.ReactNode;
}) {
  return (
    <div className={cn("mb-4 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between", className)}>
      <div className="min-w-0">
        <h1 className="truncate text-lg font-semibold tracking-tight">{title}</h1>
        {description ? <p className="mt-0.5 text-sm text-muted">{description}</p> : null}
        {children}
      </div>
      {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
    </div>
  );
}

export function Pagination({
  page,
  pageSize,
  total,
  onPage,
  className,
}: {
  page: number;
  pageSize: number;
  total: number;
  onPage: (p: number) => void;
  className?: string;
}) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  const from = total === 0 ? 0 : (page - 1) * pageSize + 1;
  const to = Math.min(total, page * pageSize);
  return (
    <div className={cn("flex items-center justify-between gap-3 border-t border-border px-3 py-2 text-xs text-muted", className)}>
      <span className="tabular">
        {formatNumber(from)}–{formatNumber(to)} of {formatNumber(total)}
      </span>
      <div className="flex items-center gap-1">
        <Button size="icon-sm" variant="ghost" disabled={page <= 1} onClick={() => onPage(page - 1)} aria-label="Previous page">
          <ChevronLeft className="h-4 w-4" />
        </Button>
        <span className="tabular px-1">
          {page} / {pages}
        </span>
        <Button size="icon-sm" variant="ghost" disabled={page >= pages} onClick={() => onPage(page + 1)} aria-label="Next page">
          <ChevronRight className="h-4 w-4" />
        </Button>
      </div>
    </div>
  );
}

export function Kbd({ children }: { children: React.ReactNode }) {
  return <kbd className="rounded border border-border bg-surface-2 px-1 font-mono text-2xs text-muted">{children}</kbd>;
}

export function Stat({
  label,
  value,
  hint,
  icon: Icon,
  tone,
  href,
}: {
  label: string;
  value: React.ReactNode;
  hint?: React.ReactNode;
  icon?: React.ComponentType<{ className?: string }>;
  tone?: "danger" | "warning" | "success";
  href?: string;
}) {
  const body = (
    <div className="flex h-full flex-col justify-between gap-2 rounded-lg border border-border bg-surface p-3 transition-colors hover:border-accent/40">
      <div className="flex items-center justify-between gap-2 text-xs text-muted">
        <span className="truncate">{label}</span>
        {Icon ? <Icon className="h-4 w-4 shrink-0" /> : null}
      </div>
      <div
        className={cn(
          "tabular text-2xl font-semibold leading-none",
          tone === "danger" && "text-danger",
          tone === "warning" && "text-warning",
          tone === "success" && "text-success",
        )}
      >
        {value}
      </div>
      {hint ? <div className="truncate text-xs text-muted">{hint}</div> : null}
    </div>
  );
  return href ? (
    <a href={href} className="block rounded-lg">
      {body}
    </a>
  ) : (
    body
  );
}

export function Toolbar({ children, className }: { children: React.ReactNode; className?: string }) {
  return <div className={cn("flex flex-wrap items-center gap-2 border-b border-border px-3 py-2", className)}>{children}</div>;
}
