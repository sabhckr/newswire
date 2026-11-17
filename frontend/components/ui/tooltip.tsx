"use client";

import * as T from "@radix-ui/react-tooltip";

export const TooltipProvider = T.Provider;

export function Tip({ content, children, side = "top" }: { content: React.ReactNode; children: React.ReactNode; side?: T.TooltipContentProps["side"] }) {
  if (!content) return <>{children}</>;
  return (
    <T.Root>
      <T.Trigger asChild>{children}</T.Trigger>
      <T.Portal>
        <T.Content
          side={side}
          sideOffset={6}
          className="z-[60] max-w-xs rounded border border-border bg-surface px-2 py-1 text-xs text-text shadow-pop animate-fade-in"
        >
          {content}
        </T.Content>
      </T.Portal>
    </T.Root>
  );
}
