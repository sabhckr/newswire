"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Archive, Copy, Microscope, MoreHorizontal, Pause, Pencil, Play, RotateCcw, Trash2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { useConfirm } from "@/components/ui/confirm";
import { Dropdown, DropdownContent, DropdownItem, DropdownSeparator, DropdownTrigger } from "@/components/ui/dropdown";
import { api, errorMessage } from "@/lib/api";
import { PERMS, useSession } from "@/lib/session";
import type { AssignmentOut } from "@/lib/types";

/** Mutations shared by the headline list and the research workspace header. */
export function useAssignmentActions() {
  const qc = useQueryClient();
  const router = useRouter();
  const confirm = useConfirm();
  const refresh = (id?: string) => {
    qc.invalidateQueries({ queryKey: ["assignments"] });
    qc.invalidateQueries({ queryKey: ["overview"] });
    if (id) {
      qc.invalidateQueries({ queryKey: ["assignment", id] });
      qc.invalidateQueries({ queryKey: ["research", id] });
    }
  };
  const status = useMutation({
    mutationFn: ({ a, action }: { a: AssignmentOut; action: "pause" | "resume" | "archive" }) =>
      api<AssignmentOut>(`/assignments/${a.id}/${action}`, { method: "POST" }),
    onSuccess: (a, { action }) => {
      toast.success(`“${a.headline}” ${action === "pause" ? "paused" : action === "resume" ? "resumed" : "archived"}`);
      refresh(a.id);
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const duplicate = useMutation({
    mutationFn: (a: AssignmentOut) => api<AssignmentOut>(`/assignments/${a.id}/duplicate`, { method: "POST" }),
    onSuccess: (copy) => {
      toast.success("Duplicated as a paused copy", {
        action: { label: "Edit", onClick: () => router.push(`/headlines/${copy.id}/edit`) },
      });
      refresh();
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const remove = useMutation({
    mutationFn: (a: AssignmentOut) => api(`/assignments/${a.id}`, { method: "DELETE" }),
    onSuccess: () => {
      toast.success("Headline deleted");
      refresh();
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const rematch = useMutation({
    mutationFn: (a: AssignmentOut) =>
      api<{ scanned: number; created: number; removed: number }>(`/assignments/${a.id}/rematch`, { method: "POST", query: { days: 7 } }),
    onSuccess: (s, a) => {
      toast.success(`Re-scored ${s.scanned} reports: ${s.created} new matches, ${s.removed} removed`);
      refresh(a.id);
      qc.invalidateQueries({ queryKey: ["matches", a.id] });
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  const askDelete = async (a: AssignmentOut) => {
    const ok = await confirm({
      title: "Delete this headline?",
      body: (
        <>
          “{a.headline}” and its {a.match_count} stored matches will be deleted. Collected news reports are kept. Archive it instead if you may
          need it again.
        </>
      ),
      confirmLabel: "Delete headline",
      destructive: true,
    });
    if (ok) remove.mutate(a);
  };

  return { status, duplicate, remove, rematch, askDelete };
}

export function AssignmentRowMenu({ a, showResearch = true }: { a: AssignmentOut; showResearch?: boolean }) {
  const router = useRouter();
  const { can } = useSession();
  const { status, duplicate, rematch, askDelete } = useAssignmentActions();
  const write = can(PERMS.assignmentsWrite);
  return (
    <Dropdown>
      <DropdownTrigger asChild>
        <Button variant="ghost" size="icon-sm" aria-label={`Actions for ${a.headline}`}>
          <MoreHorizontal className="h-4 w-4" />
        </Button>
      </DropdownTrigger>
      <DropdownContent>
        {showResearch ? (
          <DropdownItem icon={Microscope} onSelect={() => router.push(`/research/${a.id}`)}>
            Open research
          </DropdownItem>
        ) : null}
        {write ? (
          <>
            <DropdownItem icon={Pencil} onSelect={() => router.push(`/headlines/${a.id}/edit`)}>
              Edit rules
            </DropdownItem>
            <DropdownItem icon={Copy} onSelect={() => duplicate.mutate(a)}>
              Duplicate
            </DropdownItem>
            <DropdownItem icon={RotateCcw} onSelect={() => rematch.mutate(a)}>
              Re-score last 7 days
            </DropdownItem>
            <DropdownSeparator />
            {a.status === "active" ? (
              <DropdownItem icon={Pause} onSelect={() => status.mutate({ a, action: "pause" })}>
                Pause monitoring
              </DropdownItem>
            ) : (
              <DropdownItem icon={Play} onSelect={() => status.mutate({ a, action: "resume" })}>
                {a.status === "archived" ? "Restore and resume" : "Resume monitoring"}
              </DropdownItem>
            )}
            {a.status !== "archived" ? (
              <DropdownItem icon={Archive} onSelect={() => status.mutate({ a, action: "archive" })}>
                Archive
              </DropdownItem>
            ) : null}
            <DropdownItem icon={Trash2} danger onSelect={() => askDelete(a)}>
              Delete…
            </DropdownItem>
          </>
        ) : null}
      </DropdownContent>
    </Dropdown>
  );
}
