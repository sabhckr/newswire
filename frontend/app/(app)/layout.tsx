"use client";

import { useQuery } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { Suspense, useEffect } from "react";

import { AppShell } from "@/components/shell/app-shell";
import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/states";
import { api, errorMessage } from "@/lib/api";
import { SessionProvider, useSessionQuery } from "@/lib/session";

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const me = useSessionQuery();
  const unauthorized = me.error?.status === 401;
  const setup = useQuery({
    queryKey: ["setup-status"],
    queryFn: () => api<{ needs_setup: boolean }>("/auth/setup-status"),
    enabled: unauthorized,
  });

  useEffect(() => {
    if (!unauthorized || !setup.data) return;
    if (setup.data.needs_setup) router.replace("/setup");
    else router.replace(`/login?next=${encodeURIComponent(window.location.pathname + window.location.search)}`);
  }, [unauthorized, setup.data, router]);

  if (me.data) {
    return (
      <SessionProvider session={me.data}>
        <AppShell>
          <Suspense fallback={<Spinner className="p-6" />}>{children}</Suspense>
        </AppShell>
      </SessionProvider>
    );
  }
  if (me.error && !unauthorized) {
    return (
      <div className="flex min-h-screen flex-col items-center justify-center gap-3 p-6 text-center">
        <p className="text-sm font-medium">Samyabadi cannot reach its API</p>
        <p className="max-w-md text-sm text-muted">{errorMessage(me.error)}</p>
        <Button variant="outline" onClick={() => me.refetch()}>
          Try again
        </Button>
      </div>
    );
  }
  return (
    <div className="flex min-h-screen items-center justify-center">
      <Spinner label="Loading Samyabadi" />
    </div>
  );
}
