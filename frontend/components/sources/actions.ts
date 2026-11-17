"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { api, errorMessage } from "@/lib/api";
import type { FetchLogOut, SourceOut } from "@/lib/types";

/** Shared mutations for the sources list and the source detail page. */
export function useSourceActions() {
  const qc = useQueryClient();
  const invalidate = (id?: string) => {
    qc.invalidateQueries({ queryKey: ["sources"] });
    if (id) {
      qc.invalidateQueries({ queryKey: ["source", id] });
      qc.invalidateQueries({ queryKey: ["source-health", id] });
      qc.invalidateQueries({ queryKey: ["source-logs", id] });
    }
    qc.invalidateQueries({ queryKey: ["overview"] });
  };

  const refresh = useMutation({
    mutationFn: (id: string) => api<{ log: FetchLogOut; source: SourceOut }>(`/sources/${id}/refresh`, { method: "POST" }),
    onSuccess: (r) => {
      invalidate(r.source.id);
      qc.invalidateQueries({ queryKey: ["news"] });
      const l = r.log;
      if (l.status === "success") toast.success(`${r.source.name}: ${l.items_new} new, ${l.items_duplicate} duplicates of ${l.items_found} entries`);
      else if (l.status === "not_modified") toast.info(`${r.source.name}: not modified since the last fetch`);
      else toast.error(`${r.source.name}: ${l.status}${l.http_status ? ` (HTTP ${l.http_status})` : ""}${l.error_message ? `: ${l.error_message}` : ""}`);
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  const setActive = useMutation({
    mutationFn: ({ id, active }: { id: string; active: boolean }) =>
      api<SourceOut>(`/sources/${id}/${active ? "enable" : "disable"}`, { method: "POST" }),
    onSuccess: (s) => {
      invalidate(s.id);
      toast.success(`${s.name} ${s.is_active ? "enabled" : "disabled"}`);
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  const remove = useMutation({
    mutationFn: (id: string) => api(`/sources/${id}`, { method: "DELETE" }),
    onSuccess: (_r, id) => {
      invalidate(id);
      toast.success("Source deleted. Stories already collected are kept.");
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  const bulk = useMutation({
    mutationFn: ({ ids, action }: { ids: string[]; action: "enable" | "disable" | "delete" | "refresh_soon" }) =>
      api<{ updated: number }>("/sources/bulk", { body: { ids, action } }),
    onSuccess: (r, v) => {
      invalidate();
      const verb = { enable: "enabled", disable: "disabled", delete: "deleted", refresh_soon: "queued for the next cycle" }[v.action];
      toast.success(`${r.updated} source${r.updated === 1 ? "" : "s"} ${verb}`);
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  return { refresh, setActive, remove, bulk };
}
