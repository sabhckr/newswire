"use client";

import * as M from "@radix-ui/react-dropdown-menu";

import { cn } from "@/lib/utils";

export const Dropdown = M.Root;
export const DropdownTrigger = M.Trigger;

export function DropdownContent({ className, align = "end", ...props }: M.DropdownMenuContentProps) {
  return (
    <M.Portal>
      <M.Content
        align={align}
        sideOffset={6}
        className={cn("z-50 min-w-[180px] rounded-lg border border-border bg-surface p-1 shadow-pop animate-fade-in", className)}
        {...props}
      />
    </M.Portal>
  );
}

export function DropdownItem({
  className,
  icon: Icon,
  children,
  danger,
  ...props
}: M.DropdownMenuItemProps & { icon?: React.ComponentType<{ className?: string }>; danger?: boolean }) {
  return (
    <M.Item
      className={cn(
        "flex cursor-pointer select-none items-center gap-2 rounded px-2 py-1.5 text-sm outline-none data-[disabled]:pointer-events-none data-[highlighted]:bg-surface-2 data-[disabled]:opacity-50",
        danger && "text-danger",
        className,
      )}
      {...props}
    >
      {Icon ? <Icon className="h-4 w-4 text-muted" /> : null}
      {children}
    </M.Item>
  );
}

export function DropdownLabel({ className, ...props }: M.DropdownMenuLabelProps) {
  return <M.Label className={cn("px-2 py-1.5 text-xs text-muted", className)} {...props} />;
}

export function DropdownSeparator() {
  return <M.Separator className="my-1 h-px bg-border" />;
}
