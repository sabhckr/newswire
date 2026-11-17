import Link from "next/link";

import { cn } from "@/lib/utils";

// components/ui/button has no asChild/link form; this mirrors its outline/primary/ghost look for navigation links.
const variants = {
  primary: "bg-accent text-accent-fg hover:bg-accent/90 border border-transparent",
  outline: "bg-transparent text-text hover:bg-surface-2 border border-border",
  ghost: "bg-transparent text-text hover:bg-surface-2 border border-transparent",
} as const;
const sizes = { sm: "h-7 px-2.5 text-xs gap-1.5", md: "h-8 px-3 text-sm gap-2" } as const;

export function LinkButton({
  href,
  variant = "outline",
  size = "md",
  className,
  children,
  ...props
}: React.ComponentProps<typeof Link> & { variant?: keyof typeof variants; size?: keyof typeof sizes }) {
  return (
    <Link
      href={href}
      className={cn("inline-flex shrink-0 select-none items-center whitespace-nowrap rounded font-medium transition-colors", variants[variant], sizes[size], className)}
      {...props}
    >
      {children}
    </Link>
  );
}
