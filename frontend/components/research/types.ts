import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback } from "react";

import type { AssignmentOut, ImageOut, LicenseWarning } from "@/lib/types";

export type WorkspaceImage = Omit<ImageOut, "warnings"> & { warnings?: LicenseWarning[] };

export interface ChecklistItem {
  label: string;
  done: boolean;
  url: string | null;
}

/** GET /research/{assignment_id} */
export interface Workspace {
  assignment: AssignmentOut;
  stats: {
    matched_items: number;
    distinct_publishers: number;
    first_report_at: string | null;
    latest_report_at: string | null;
    by_review_status: Record<string, number>;
    average_score: number | null;
  };
  publishers: { publisher: string; items: number; first_report_at: string | null; latest_report_at: string | null; top_score: number | null }[];
  geography: { country_code: string; roles: Record<string, number> }[];
  timeline: { granularity: "hour" | "day"; buckets: { bucket: string; items: number; publishers: number }[] };
  clusters: {
    id: string;
    title: string;
    title_source: string;
    item_count: number;
    publisher_count: number;
    first_seen_at: string | null;
    last_seen_at: string | null;
    matched_items: number;
  }[];
  images: WorkspaceImage[];
  articles: { id: string; headline: string; status: string; updated_at: string }[];
}

export function normalizeChecklist(raw: unknown[] | null | undefined): ChecklistItem[] {
  return (raw ?? []).flatMap((r) => {
    if (!r || typeof r !== "object") return [];
    const o = r as Record<string, unknown>;
    const label = typeof o.label === "string" ? o.label : "";
    if (!label) return [];
    return [{ label, done: !!o.done, url: typeof o.url === "string" && o.url ? o.url : null }];
  });
}

/** Read/patch URL search params without adding history entries. */
export function useUrlState() {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const get = useCallback((k: string) => params.get(k) ?? "", [params]);
  const update = useCallback(
    (patch: Record<string, string | null>) => {
      const next = new URLSearchParams(params.toString());
      for (const [k, v] of Object.entries(patch)) {
        if (v === null || v === "") next.delete(k);
        else next.set(k, v);
      }
      const s = next.toString();
      router.replace(s ? `${pathname}?${s}` : pathname, { scroll: false });
    },
    [params, pathname, router],
  );
  return { params, get, update };
}
