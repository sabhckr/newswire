"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useMemo } from "react";

import { api, ApiError, setCsrfToken } from "./api";
import type { SessionPayload } from "./types";

export const PERMS = {
  read: "read",
  sourcesWrite: "sources:write",
  assignmentsWrite: "assignments:write",
  researchWrite: "research:write",
  clustersWrite: "clusters:write",
  imagesWrite: "images:write",
  imagesApprove: "images:approve",
  articlesWrite: "articles:write",
  articlesApprove: "articles:approve",
  articlesPublish: "articles:publish",
  publishingManage: "publishing:manage",
  settingsWrite: "settings:write",
  usersManage: "users:manage",
  systemManage: "system:manage",
} as const;

type SessionCtx = {
  session: SessionPayload;
  can: (perm: string) => boolean;
  refresh: () => Promise<unknown>;
};

const Ctx = createContext<SessionCtx | null>(null);

export function useSessionQuery() {
  return useQuery<SessionPayload, ApiError>({
    queryKey: ["me"],
    queryFn: async () => {
      const s = await api<SessionPayload>("/auth/me");
      setCsrfToken(s.csrf_token);
      return s;
    },
    retry: (count, err) => err.status !== 401 && count < 2,
    staleTime: 60_000,
  });
}

export function SessionProvider({ session, children }: { session: SessionPayload; children: React.ReactNode }) {
  const qc = useQueryClient();
  const router = useRouter();
  const perms = useMemo(() => new Set(session.permissions), [session.permissions]);
  const can = useCallback((p: string) => perms.has(p), [perms]);
  const refresh = useCallback(() => qc.invalidateQueries({ queryKey: ["me"] }), [qc]);

  useEffect(() => {
    const onUnauthorized = () => {
      qc.removeQueries({ queryKey: ["me"] });
      router.replace(`/login?next=${encodeURIComponent(window.location.pathname + window.location.search)}`);
    };
    window.addEventListener("newswire:unauthorized", onUnauthorized);
    return () => window.removeEventListener("newswire:unauthorized", onUnauthorized);
  }, [qc, router]);

  const value = useMemo(() => ({ session, can, refresh }), [session, can, refresh]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useSession(): SessionCtx {
  const v = useContext(Ctx);
  if (!v) throw new Error("useSession must be used inside SessionProvider");
  return v;
}

export function useCan(perm: string): boolean {
  return useSession().can(perm);
}
