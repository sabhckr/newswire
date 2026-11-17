"use client";

import * as D from "@radix-ui/react-dialog";
import { X } from "lucide-react";

import { cn } from "@/lib/utils";

export const Dialog = D.Root;
export const DialogTrigger = D.Trigger;
export const DialogClose = D.Close;

export function DialogContent({
  title,
  description,
  children,
  className,
  footer,
  wide,
}: {
  title: React.ReactNode;
  description?: React.ReactNode;
  children?: React.ReactNode;
  className?: string;
  footer?: React.ReactNode;
  wide?: boolean;
}) {
  return (
    <D.Portal>
      <D.Overlay className="fixed inset-0 z-50 bg-black/50 animate-fade-in" />
      <D.Content
        className={cn(
          "fixed left-1/2 top-[8vh] z-50 flex max-h-[84vh] w-[calc(100vw-2rem)] -translate-x-1/2 flex-col rounded-lg border border-border bg-surface shadow-pop animate-slide-in",
          wide ? "max-w-3xl" : "max-w-lg",
          className,
        )}
      >
        <div className="flex items-start justify-between gap-4 border-b border-border px-4 py-3">
          <div className="min-w-0">
            <D.Title className="text-sm font-semibold">{title}</D.Title>
            {description ? (
              <D.Description className="mt-0.5 text-xs text-muted">{description}</D.Description>
            ) : (
              <D.Description className="sr-only">{typeof title === "string" ? title : "Dialog"}</D.Description>
            )}
          </div>
          <D.Close className="rounded p-1 text-muted hover:bg-surface-2 hover:text-text" aria-label="Close">
            <X className="h-4 w-4" />
          </D.Close>
        </div>
        <div className="scroll-thin min-h-0 flex-1 overflow-y-auto px-4 py-4">{children}</div>
        {footer ? <div className="flex justify-end gap-2 border-t border-border px-4 py-3">{footer}</div> : null}
      </D.Content>
    </D.Portal>
  );
}
