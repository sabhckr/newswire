import { Loader2 } from "lucide-react";
import { forwardRef } from "react";

import { cn } from "@/lib/utils";

const variants = {
  primary: "bg-accent text-accent-fg hover:bg-accent/90 border border-transparent",
  secondary: "bg-surface-2 text-text hover:bg-border/60 border border-border",
  outline: "bg-transparent text-text hover:bg-surface-2 border border-border",
  ghost: "bg-transparent text-text hover:bg-surface-2 border border-transparent",
  danger: "bg-danger text-white hover:bg-danger/90 border border-transparent",
  "danger-outline": "bg-transparent text-danger hover:bg-danger/10 border border-danger/40",
} as const;

const sizes = {
  sm: "h-7 px-2.5 text-xs gap-1.5",
  md: "h-8 px-3 text-sm gap-2",
  lg: "h-10 px-4 text-sm gap-2",
  icon: "h-8 w-8 justify-center",
  "icon-sm": "h-7 w-7 justify-center",
} as const;

export type ButtonProps = React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: keyof typeof variants;
  size?: keyof typeof sizes;
  loading?: boolean;
};

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { className, variant = "secondary", size = "md", loading, disabled, children, type = "button", ...props },
  ref,
) {
  return (
    <button
      ref={ref}
      type={type}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      className={cn(
        "inline-flex shrink-0 select-none items-center whitespace-nowrap rounded font-medium transition-colors disabled:pointer-events-none disabled:opacity-50",
        variants[variant],
        sizes[size],
        className,
      )}
      {...props}
    >
      {loading ? <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden /> : null}
      {children}
    </button>
  );
});
