"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { AuthCard } from "@/components/shell/auth-card";
import { Button } from "@/components/ui/button";
import { Field, Input } from "@/components/ui/input";
import { InlineAlert } from "@/components/ui/states";
import { api, errorMessage, setCsrfToken } from "@/lib/api";
import type { SessionPayload } from "@/lib/types";

const schema = z.object({
  email: z.string().trim().min(3, "Enter your email"),
  password: z.string().min(1, "Enter your password"),
});
type Values = z.infer<typeof schema>;

function safeNext(next: string | null): string {
  return next && next.startsWith("/") && !next.startsWith("//") ? next : "/";
}

function LoginForm() {
  const router = useRouter();
  const params = useSearchParams();
  const qc = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  const setup = useQuery({ queryKey: ["setup-status"], queryFn: () => api<{ needs_setup: boolean }>("/auth/setup-status") });
  const { register, handleSubmit, formState } = useForm<Values>({ resolver: zodResolver(schema) });

  useEffect(() => {
    if (setup.data?.needs_setup) router.replace("/setup");
  }, [setup.data, router]);

  const onSubmit = async (v: Values) => {
    setError(null);
    try {
      const s = await api<SessionPayload>("/auth/login", { body: v });
      setCsrfToken(s.csrf_token);
      qc.setQueryData(["me"], s);
      router.replace(safeNext(params.get("next")));
    } catch (e) {
      setError(errorMessage(e));
    }
  };

  return (
    <AuthCard title="Sign in" description="Use the account your administrator created for you.">
      <form onSubmit={handleSubmit(onSubmit)} className="flex flex-col gap-4" noValidate>
        {error ? <InlineAlert tone="danger">{error}</InlineAlert> : null}
        <Field label="Email" htmlFor="email" error={formState.errors.email?.message}>
          <Input id="email" type="email" autoComplete="username" autoFocus {...register("email")} aria-invalid={!!formState.errors.email} />
        </Field>
        <Field label="Password" htmlFor="password" error={formState.errors.password?.message}>
          <Input id="password" type="password" autoComplete="current-password" {...register("password")} aria-invalid={!!formState.errors.password} />
        </Field>
        <Button type="submit" variant="primary" size="lg" loading={formState.isSubmitting}>
          Sign in
        </Button>
        <p className="text-xs text-muted">Forgotten password? An administrator can reset it, or run <code className="font-mono">python -m app.cli reset-password</code> on the server.</p>
      </form>
    </AuthCard>
  );
}

export default function LoginPage() {
  return (
    <Suspense>
      <LoginForm />
    </Suspense>
  );
}
