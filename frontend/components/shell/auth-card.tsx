import { Radio } from "lucide-react";

export function AuthCard({ title, description, children }: { title: string; description?: string; children: React.ReactNode }) {
  return (
    <main className="flex min-h-screen items-center justify-center bg-bg px-4 py-10">
      <div className="w-full max-w-sm">
        <div className="mb-6 flex items-center justify-center gap-2">
          <span className="flex h-8 w-8 items-center justify-center rounded bg-accent text-accent-fg">
            <Radio className="h-4 w-4" aria-hidden />
          </span>
          <span className="text-lg font-semibold tracking-tight">NEWSWIRE</span>
        </div>
        <div className="rounded-lg border border-border bg-surface p-6">
          <h1 className="text-base font-semibold">{title}</h1>
          {description ? <p className="mt-1 text-sm text-muted">{description}</p> : null}
          <div className="mt-5">{children}</div>
        </div>
        <p className="mt-4 text-center text-xs text-muted">Self-hosted news intelligence · no AI, no paid services</p>
      </div>
    </main>
  );
}
