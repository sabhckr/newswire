"use client";

import * as S from "@radix-ui/react-switch";

import { cn } from "@/lib/utils";

export function Switch({ className, label, id, ...props }: S.SwitchProps & { label?: React.ReactNode }) {
  const control = (
    <S.Root
      id={id}
      className={cn(
        "relative inline-flex h-[18px] w-8 shrink-0 cursor-pointer items-center rounded-full border border-border bg-surface-2 transition-colors data-[state=checked]:border-accent data-[state=checked]:bg-accent disabled:cursor-not-allowed disabled:opacity-50",
        className,
      )}
      {...props}
    >
      <S.Thumb className="block h-3.5 w-3.5 translate-x-px rounded-full bg-white shadow transition-transform data-[state=checked]:translate-x-[14px]" />
    </S.Root>
  );
  if (!label) return control;
  return (
    <label htmlFor={id} className="inline-flex cursor-pointer items-center gap-2 text-sm">
      {control}
      <span>{label}</span>
    </label>
  );
}
