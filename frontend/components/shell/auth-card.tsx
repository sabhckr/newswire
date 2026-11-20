import { APP_NAME, APP_SHORT_NAME, APP_TAGLINE, LOGO_WIDE } from "@/lib/brand";

export function AuthCard({ title, description, children }: { title: string; description?: string; children: React.ReactNode }) {
  return (
    <main className="flex min-h-screen items-center justify-center bg-bg px-4 py-10">
      <div className="w-full max-w-sm">
        <div className="mb-6 flex flex-col items-center gap-2" aria-label={APP_NAME}>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={LOGO_WIDE} alt={APP_SHORT_NAME} width={256} height={120} className="h-auto w-64 max-w-full rounded-lg" />
          <span className="text-sm font-semibold uppercase tracking-[0.18em] text-muted">{APP_TAGLINE}</span>
        </div>
        <div className="rounded-lg border border-border bg-surface p-6">
          <h1 className="text-base font-semibold">{title}</h1>
          {description ? <p className="mt-1 text-sm text-muted">{description}</p> : null}
          <div className="mt-5">{children}</div>
        </div>
        <p className="mt-4 text-center text-xs text-muted">{APP_NAME} · no AI, no paid services</p>
      </div>
    </main>
  );
}
