import { cn } from "@/lib/utils";

const tones = {
  neutral: "bg-surface-2 text-muted border-border",
  success: "bg-success/10 text-success border-success/25",
  warning: "bg-warning/10 text-warning border-warning/30",
  danger: "bg-danger/10 text-danger border-danger/30",
  info: "bg-info/10 text-info border-info/25",
  accent: "bg-accent/10 text-accent border-accent/25",
} as const;

export type Tone = keyof typeof tones;

export function Badge({
  tone = "neutral",
  icon: Icon,
  children,
  className,
  title,
}: {
  tone?: Tone;
  icon?: React.ComponentType<{ className?: string; "aria-hidden"?: boolean }>;
  children: React.ReactNode;
  className?: string;
  title?: string;
}) {
  return (
    <span
      title={title}
      className={cn(
        "inline-flex max-w-full items-center gap-1 whitespace-nowrap rounded border px-1.5 py-px text-2xs font-medium",
        tones[tone],
        className,
      )}
    >
      {Icon ? <Icon className="h-3 w-3 shrink-0" aria-hidden /> : null}
      <span className="truncate">{children}</span>
    </span>
  );
}
