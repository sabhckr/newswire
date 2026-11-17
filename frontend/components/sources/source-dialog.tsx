"use client";

import { Dialog, DialogContent } from "@/components/ui/dialog";
import type { SourceOut } from "@/lib/types";

import { SourceForm } from "./source-form";

export function SourceDialog({
  open,
  onOpenChange,
  source,
  onSaved,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  source?: SourceOut | null;
  onSaved?: (s: SourceOut) => void;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      {open ? (
        <DialogContent
          wide
          title={source ? `Edit ${source.name}` : "Add a source"}
          description={
            source
              ? "Changing the feed URL clears cached validators and schedules an immediate fetch."
              : "Any public RSS, Atom or JSON feed. Test it first to see what will be collected."
          }
        >
          <SourceForm
            source={source}
            onCancel={() => onOpenChange(false)}
            onSaved={(s) => {
              onOpenChange(false);
              onSaved?.(s);
            }}
          />
        </DialogContent>
      ) : null}
    </Dialog>
  );
}
