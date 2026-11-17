"use client";

import * as P from "@radix-ui/react-popover";

import { cn } from "@/lib/utils";

export const Popover = P.Root;
export const PopoverTrigger = P.Trigger;
export const PopoverClose = P.Close;

export function PopoverContent({ className, align = "end", ...props }: P.PopoverContentProps) {
  return (
    <P.Portal>
      <P.Content
        align={align}
        sideOffset={6}
        className={cn("z-50 rounded-lg border border-border bg-surface shadow-pop outline-none animate-fade-in", className)}
        {...props}
      />
    </P.Portal>
  );
}
