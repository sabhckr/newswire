import { forwardRef } from "react";

import { cn } from "@/lib/utils";

const base =
  "w-full rounded border border-border bg-surface px-2.5 text-sm text-text placeholder:text-muted/70 disabled:cursor-not-allowed disabled:opacity-60 aria-[invalid=true]:border-danger";

export const Input = forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(function Input(
  { className, ...props },
  ref,
) {
  return <input ref={ref} className={cn(base, "h-8", className)} {...props} />;
});

export const Textarea = forwardRef<HTMLTextAreaElement, React.TextareaHTMLAttributes<HTMLTextAreaElement>>(function Textarea(
  { className, ...props },
  ref,
) {
  return <textarea ref={ref} className={cn(base, "min-h-[80px] py-2 leading-5", className)} {...props} />;
});

export const Select = forwardRef<HTMLSelectElement, React.SelectHTMLAttributes<HTMLSelectElement>>(function Select(
  { className, children, ...props },
  ref,
) {
  return (
    <select ref={ref} className={cn(base, "h-8 pr-7", className)} {...props}>
      {children}
    </select>
  );
});

export function Label({ className, ...props }: React.LabelHTMLAttributes<HTMLLabelElement>) {
  return <label className={cn("text-xs font-medium text-text", className)} {...props} />;
}

export function Field({
  label,
  htmlFor,
  hint,
  error,
  children,
  className,
  required,
}: {
  label: React.ReactNode;
  htmlFor?: string;
  hint?: React.ReactNode;
  error?: string;
  children: React.ReactNode;
  className?: string;
  required?: boolean;
}) {
  return (
    <div className={cn("flex flex-col gap-1", className)}>
      <Label htmlFor={htmlFor}>
        {label}
        {required ? <span className="text-danger"> *</span> : null}
      </Label>
      {children}
      {error ? (
        <p className="text-xs text-danger" role="alert">
          {error}
        </p>
      ) : hint ? (
        <p className="text-xs text-muted">{hint}</p>
      ) : null}
    </div>
  );
}

export function Checkbox({ label, className, ...props }: React.InputHTMLAttributes<HTMLInputElement> & { label?: React.ReactNode }) {
  const box = <input type="checkbox" className={cn("h-3.5 w-3.5 rounded border-border accent-[rgb(var(--accent))]", className)} {...props} />;
  if (!label) return box;
  return (
    <label className="inline-flex cursor-pointer items-center gap-2 text-sm">
      {box}
      <span>{label}</span>
    </label>
  );
}
