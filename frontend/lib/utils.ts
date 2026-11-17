import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

const rtf = typeof Intl !== "undefined" ? new Intl.RelativeTimeFormat("en", { numeric: "auto", style: "short" }) : null;

/** "3 min ago", "in 2 hr". Always pair with the absolute time in a title attribute. */
export function relativeTime(value: string | number | Date | null | undefined, now: number = Date.now()): string {
  if (value === null || value === undefined || value === "") return "—";
  const t = new Date(value).getTime();
  if (Number.isNaN(t)) return "—";
  const diff = (t - now) / 1000;
  const abs = Math.abs(diff);
  if (abs < 45) return diff < 0 ? "just now" : "in a moment";
  const units: [Intl.RelativeTimeFormatUnit, number][] = [
    ["minute", 60],
    ["hour", 3600],
    ["day", 86400],
    ["week", 604800],
    ["month", 2629800],
    ["year", 31557600],
  ];
  let unit: Intl.RelativeTimeFormatUnit = "minute";
  let size = 60;
  for (const [u, s] of units) {
    if (abs >= s) {
      unit = u;
      size = s;
    }
  }
  const n = Math.round(diff / size);
  return rtf ? rtf.format(n, unit) : `${n} ${unit}`;
}

export function formatDateTime(value: string | number | Date | null | undefined, opts?: Intl.DateTimeFormatOptions): string {
  if (!value) return "—";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString(undefined, opts ?? { dateStyle: "medium", timeStyle: "short" });
}

export function formatDate(value: string | number | Date | null | undefined): string {
  return formatDateTime(value, { dateStyle: "medium" });
}

export function formatNumber(n: number | null | undefined, digits = 0): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  return n.toLocaleString(undefined, { maximumFractionDigits: digits, minimumFractionDigits: digits ? 0 : undefined });
}

export function formatBytes(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  let v = n;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(v >= 10 || i === 0 ? 0 : 1)} ${units[i]}`;
}

export function formatDuration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return "—";
  if (ms < 1000) return `${ms} ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(1)} s`;
  return `${Math.round(ms / 60000)} min`;
}

export function formatInterval(seconds: number | null | undefined): string {
  if (!seconds) return "—";
  if (seconds < 60) return `${seconds} s`;
  if (seconds < 3600) return `${+(seconds / 60).toFixed(1)} min`;
  if (seconds < 86400) return `${+(seconds / 3600).toFixed(1)} h`;
  return `${+(seconds / 86400).toFixed(1)} d`;
}

export function hostOf(url: string | null | undefined): string {
  if (!url) return "";
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

/** Regional-indicator flag for an ISO 3166 alpha-2 code; text fallback is the code itself. */
export function flag(cc: string | null | undefined): string {
  if (!cc || cc.length !== 2) return "";
  const A = 0x1f1e6;
  return String.fromCodePoint(...cc.toUpperCase().split("").map((c) => A + c.charCodeAt(0) - 65));
}

export function pluralize(n: number, one: string, many?: string): string {
  return `${formatNumber(n)} ${n === 1 ? one : many ?? `${one}s`}`;
}

export function toLocalInput(value: string | null | undefined): string {
  if (!value) return "";
  const d = new Date(value);
  const pad = (x: number) => String(x).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export function fromLocalInput(value: string): string | null {
  if (!value) return null;
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? null : d.toISOString();
}

export const browserTimeZone = (): string => {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone;
  } catch {
    return "UTC";
  }
};
