"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { AuthCard } from "@/components/shell/auth-card";
import { Button } from "@/components/ui/button";
import { Field, Input } from "@/components/ui/input";
import { InlineAlert, Spinner } from "@/components/ui/states";
import { api, errorMessage, setCsrfToken } from "@/lib/api";
import type { SessionPayload } from "@/lib/types";

const schema = z
  .object({
    display_name: z.string().trim().min(1, "Enter your name").max(120),
    email: z.string().trim().email("Enter a valid email address"),
    password: z.string().min(10, "Use at least 10 characters").max(256),
    confirm: z.string(),
  })
  .refine((v) => v.password === v.confirm, { path: ["confirm"], message: "Passwords do not match" });
type Values = z.infer<typeof schema>;

export default function SetupPage() {
  const router = useRouter();
  const qc = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  const status = useQuery({ queryKey: ["setup-status"], queryFn: () => api<{ needs_setup: boolean }>("/auth/setup-status") });
  const { register, handleSubmit, formState } = useForm<Values>({ resolver: zodResolver(schema) });

  useEffect(() => {
    if (!done && status.data && !status.data.needs_setup) router.replace("/login");
  }, [status.data, router, done]);

  const onSubmit = async ({ confirm: _c, ...body }: Values) => {
    setError(null);
    try {
      const s = await api<SessionPayload>("/auth/setup", { body });
      setCsrfToken(s.csrf_token);
      setDone(true);
      qc.setQueryData(["me"], s);
      qc.setQueryData(["setup-status"], { needs_setup: false });
      router.replace("/sources?welcome=1");
    } catch (e) {
      setError(errorMessage(e));
    }
  };

  if (!status.data?.needs_setup || done) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <Spinner />
      </div>
    );
  }

  return (
    <AuthCard title="Set up Samyabadi" description="Create the first administrator account. You can invite editors and researchers afterwards.">
      <form onSubmit={handleSubmit(onSubmit)} className="flex flex-col gap-4" noValidate>
        {error ? <InlineAlert tone="danger">{error}</InlineAlert> : null}
        <Field label="Your name" htmlFor="display_name" error={formState.errors.display_name?.message}>
          <Input id="display_name" autoComplete="name" autoFocus {...register("display_name")} />
        </Field>
        <Field label="Email" htmlFor="email" error={formState.errors.email?.message}>
          <Input id="email" type="email" autoComplete="username" {...register("email")} />
        </Field>
        <Field label="Password" htmlFor="password" error={formState.errors.password?.message} hint="At least 10 characters; a passphrase works well.">
          <Input id="password" type="password" autoComplete="new-password" {...register("password")} />
        </Field>
        <Field label="Repeat password" htmlFor="confirm" error={formState.errors.confirm?.message}>
          <Input id="confirm" type="password" autoComplete="new-password" {...register("confirm")} />
        </Field>
        <Button type="submit" variant="primary" size="lg" loading={formState.isSubmitting}>
          Create administrator
        </Button>
      </form>
    </AuthCard>
  );
}
