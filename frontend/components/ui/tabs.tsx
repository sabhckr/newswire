"use client";

import * as T from "@radix-ui/react-tabs";

import { cn } from "@/lib/utils";

export const Tabs = T.Root;

export function TabsList({ className, ...props }: T.TabsListProps) {
  return <T.List className={cn("flex items-center gap-1 overflow-x-auto border-b border-border scroll-thin", className)} {...props} />;
}

export function TabsTrigger({ className, ...props }: T.TabsTriggerProps) {
  return (
    <T.Trigger
      className={cn(
        "-mb-px whitespace-nowrap border-b-2 border-transparent px-3 py-2 text-sm text-muted hover:text-text data-[state=active]:border-accent data-[state=active]:text-text",
        className,
      )}
      {...props}
    />
  );
}

export const TabsContent = T.Content;
