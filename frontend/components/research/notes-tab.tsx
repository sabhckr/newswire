"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ClipboardList, ExternalLink, NotebookPen, Plus, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import { normalizeChecklist, type ChecklistItem, type Workspace } from "@/components/research/types";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Input, Textarea } from "@/components/ui/input";
import { api, errorMessage } from "@/lib/api";
import { PERMS, useSession } from "@/lib/session";
import { cn } from "@/lib/utils";

type PatchBody = { editorial_notes?: string; source_checklist?: ChecklistItem[]; research_status?: string };
type PatchOut = { ok: boolean; editorial_notes: string | null; source_checklist: unknown[] | null; research_status: string };

export function useResearchPatch(assignmentId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: PatchBody) => api<PatchOut>(`/research/${assignmentId}`, { method: "PATCH", body }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["research", assignmentId] });
      qc.invalidateQueries({ queryKey: ["assignments"] });
    },
  });
}

function isUrl(v: string) {
  try {
    const u = new URL(v);
    return u.protocol === "http:" || u.protocol === "https:";
  } catch {
    return false;
  }
}

export function NotesTab({ ws }: { ws: Workspace }) {
  const a = ws.assignment;
  const { can } = useSession();
  const write = can(PERMS.researchWrite);
  const patch = useResearchPatch(a.id);

  const saved = a.editorial_notes ?? "";
  const [notes, setNotes] = useState(saved);
  const [baseline, setBaseline] = useState(saved);
  // Adopt server changes only when the local copy is not being edited.
  useEffect(() => {
    if (notes === baseline) setNotes(saved);
    setBaseline(saved);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [saved]);
  const dirty = notes !== saved;

  const [list, setList] = useState<ChecklistItem[]>(() => normalizeChecklist(a.source_checklist));
  useEffect(() => setList(normalizeChecklist(a.source_checklist)), [a.source_checklist]);
  const [label, setLabel] = useState("");
  const [url, setUrl] = useState("");
  const urlBad = !!url.trim() && !isUrl(url.trim());

  const saveNotes = () =>
    patch.mutate(
      { editorial_notes: notes },
      { onSuccess: () => toast.success("Notes saved"), onError: (e) => toast.error(errorMessage(e)) },
    );

  const saveList = (next: ChecklistItem[]) => {
    const prev = list;
    setList(next);
    patch.mutate(
      { source_checklist: next },
      {
        onError: (e) => {
          setList(prev);
          toast.error(errorMessage(e));
        },
      },
    );
  };

  const addItem = (e: React.FormEvent) => {
    e.preventDefault();
    const l = label.trim();
    if (!l || urlBad) return;
    if (list.length >= 100) {
      toast.error("The checklist holds up to 100 items");
      return;
    }
    saveList([...list, { label: l.slice(0, 300), done: false, url: url.trim() || null }]);
    setLabel("");
    setUrl("");
  };

  const done = list.filter((i) => i.done).length;

  return (
    <div className="grid grid-cols-1 gap-4 p-3 lg:grid-cols-2">
      <Card>
        <CardHeader
          icon={NotebookPen}
          title="Editorial notes"
          description="Shared with everyone working this story. Plain text."
          actions={
            write ? (
              <Button size="sm" variant="primary" disabled={!dirty} loading={patch.isPending && patch.variables?.editorial_notes !== undefined} onClick={saveNotes}>
                Save notes
              </Button>
            ) : null
          }
        />
        <CardBody>
          <label htmlFor="ws-notes" className="sr-only">
            Editorial notes
          </label>
          <Textarea
            id="ws-notes"
            rows={14}
            value={notes}
            disabled={!write}
            onChange={(e) => setNotes(e.target.value)}
            onKeyDown={(e) => {
              if ((e.metaKey || e.ctrlKey) && e.key === "s") {
                e.preventDefault();
                if (dirty && write) saveNotes();
              }
            }}
            placeholder="Angle, open questions, contacts, what has been confirmed and by whom…"
          />
          <p className="mt-1 text-xs text-muted">{dirty ? "Unsaved changes · ⌘/Ctrl+S to save" : saved ? "Saved" : "No notes yet"}</p>
        </CardBody>
      </Card>

      <Card>
        <CardHeader
          icon={ClipboardList}
          title="Source checklist"
          description={list.length ? `${done} of ${list.length} done` : "People to call, documents to obtain, claims to verify."}
        />
        <CardBody className="flex flex-col gap-3">
          {list.length ? (
            <ul className="flex flex-col divide-y divide-border rounded border border-border">
              {list.map((item, i) => (
                <li key={`${i}:${item.label}`} className="flex items-center gap-2 px-2 py-1.5">
                  <input
                    type="checkbox"
                    id={`chk-${i}`}
                    checked={item.done}
                    disabled={!write}
                    onChange={() => saveList(list.map((x, j) => (j === i ? { ...x, done: !x.done } : x)))}
                    className="h-3.5 w-3.5 accent-[rgb(var(--accent))]"
                  />
                  <label htmlFor={`chk-${i}`} className={cn("min-w-0 flex-1 text-sm", item.done && "text-muted line-through")}>
                    {item.label}
                  </label>
                  {item.url ? (
                    <a href={item.url} target="_blank" rel="noopener noreferrer" className="rounded p-1 text-muted hover:text-text" aria-label={`Open link for ${item.label}`}>
                      <ExternalLink className="h-3.5 w-3.5" />
                    </a>
                  ) : null}
                  {write ? (
                    <button
                      type="button"
                      onClick={() => saveList(list.filter((_, j) => j !== i))}
                      className="rounded p-1 text-muted hover:text-danger"
                      aria-label={`Remove ${item.label}`}
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  ) : null}
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-muted">Nothing on the checklist yet.</p>
          )}
          {write ? (
            <form onSubmit={addItem} className="flex flex-col gap-2 sm:flex-row sm:items-start">
              <div className="flex-1">
                <label htmlFor="chk-label" className="sr-only">
                  Checklist item
                </label>
                <Input id="chk-label" value={label} onChange={(e) => setLabel(e.target.value)} placeholder="e.g. Call the labour ministry spokesperson" maxLength={300} />
              </div>
              <div className="sm:w-56">
                <label htmlFor="chk-url" className="sr-only">
                  Link (optional)
                </label>
                <Input id="chk-url" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="Link (optional)" aria-invalid={urlBad} />
                {urlBad ? <p className="mt-0.5 text-xs text-danger">Use a full http(s) link</p> : null}
              </div>
              <Button type="submit" variant="outline" disabled={!label.trim() || urlBad}>
                <Plus className="h-4 w-4" /> Add
              </Button>
            </form>
          ) : null}
        </CardBody>
      </Card>
    </div>
  );
}
