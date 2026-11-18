"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, ExternalLink } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Field, Input } from "@/components/ui/input";
import { ErrorState, InlineAlert, SkeletonRows } from "@/components/ui/states";
import { api, errorMessage } from "@/lib/api";
import type { MetaAppIn, MetaAppOut } from "@/lib/types";

import { CopyField } from "./destination-dialog";
import { FacebookIcon, InstagramIcon } from "./kinds";

export function useMetaApp(enabled = true) {
  return useQuery({
    queryKey: ["meta-app"],
    queryFn: () => api<MetaAppOut>("/publishing/meta-app"),
    enabled,
  });
}

/** Settings → Integrations: the one Meta app that Facebook Pages and Instagram accounts sign in with. */
export function MetaAppSettings({ canManage }: { canManage: boolean }) {
  const qc = useQueryClient();
  const meta = useMetaApp();
  const [appId, setAppId] = useState("");
  const [secret, setSecret] = useState("");
  const [fbConfig, setFbConfig] = useState("");
  const [igConfig, setIgConfig] = useState("");
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (meta.data) {
      setAppId(meta.data.app_id ?? "");
      setFbConfig(meta.data.login_config_id ?? "");
      setIgConfig(meta.data.instagram_login_config_id ?? "");
    }
  }, [meta.data]);

  const save = useMutation({
    mutationFn: () => {
      const body: MetaAppIn = {
        app_id: appId.trim(),
        secret: secret.trim() || null,
        login_config_id: fbConfig.trim() || null,
        instagram_login_config_id: igConfig.trim() || null,
      };
      return api<MetaAppOut>("/publishing/meta-app", { method: "PUT", body });
    },
    onSuccess: (r) => {
      qc.setQueryData(["meta-app"], r);
      qc.invalidateQueries({ queryKey: ["publishing-destinations"] });
      setSecret("");
      toast.success("Meta app saved. Add a Facebook Page or Instagram account under Publishing → Destinations.");
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  const submit = () => {
    if (!/^\d{3,}$/.test(appId.trim())) return setError("The App ID is a number, e.g. 1234567890123456");
    if (!meta.data?.has_secret && !secret.trim()) return setError("Paste the App secret");
    setError(null);
    save.mutate();
  };

  if (meta.isLoading) return <SkeletonRows rows={3} />;
  if (meta.isError) return <ErrorState error={meta.error} onRetry={() => meta.refetch()} />;
  const configured = !!meta.data?.configured;

  return (
    <Card>
      <CardHeader
        title={
          <span className="inline-flex items-center gap-2">
            <FacebookIcon className="h-4 w-4" />
            <InstagramIcon className="h-4 w-4" />
            Facebook &amp; Instagram (Meta app)
          </span>
        }
        description="Set up once. After that, anyone with publishing rights adds a Facebook Page or Instagram account with Connect → sign in → choose."
        actions={
          configured ? (
            <Badge tone="success" icon={CheckCircle2}>
              Set up
            </Badge>
          ) : (
            <Badge tone="warning">Not set up</Badge>
          )
        }
      />
      <CardBody className="flex flex-col gap-4">
        <details className="rounded-lg border border-border p-3 text-sm" open={!configured}>
          <summary className="cursor-pointer font-medium">How to get the App ID and secret (one time, about 10 minutes)</summary>
          <ol className="mt-2 list-decimal space-y-1.5 pl-5 text-xs text-muted">
            <li>
              Open{" "}
              <a
                href="https://developers.facebook.com/apps/"
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-0.5 text-accent underline"
              >
                developers.facebook.com/apps <ExternalLink className="h-3 w-3" />
              </a>{" "}
              with the Facebook account that manages your Page, and click <b>Create app</b>.
            </li>
            <li>
              Choose the use case <b>Manage everything on your Page</b>. For Instagram also add <b>Manage messaging &amp; content on Instagram</b>{" "}
              (Instagram API with Facebook Login).
            </li>
            <li>
              In <b>App settings → Basic</b>, copy the <b>App ID</b> and <b>App secret</b> into the boxes below.
            </li>
            <li>
              In <b>Facebook Login → Settings</b>, add this address to <b>Valid OAuth Redirect URIs</b> and save:
              <div className="mt-1">
                {meta.data?.redirect_uri ? <CopyField value={meta.data.redirect_uri} label="Redirect address for Meta" /> : null}
              </div>
            </li>
            <li>
              Until Meta reviews the app, it works for people who have a role on it (admin, developer or tester), which is enough for your own
              newsroom. Add colleagues under <b>App roles</b>.
            </li>
            <li>
              Instagram must be a <b>Professional</b> account (Business or Creator) linked to your Facebook Page: in the Instagram app, Settings →
              Account type, then Accounts Center → link the Page.
            </li>
          </ol>
        </details>

        {!canManage ? (
          <InlineAlert tone="info">Only an administrator can change the Meta app.</InlineAlert>
        ) : (
          <form
            className="grid gap-3 sm:grid-cols-2"
            onSubmit={(e) => {
              e.preventDefault();
              submit();
            }}
          >
            <Field label="App ID" htmlFor="meta-app-id" required error={error && !secret ? error : undefined}>
              <Input
                id="meta-app-id"
                value={appId}
                onChange={(e) => setAppId(e.target.value)}
                inputMode="numeric"
                className="font-mono text-xs"
                autoComplete="off"
              />
            </Field>
            <Field
              label="App secret"
              htmlFor="meta-app-secret"
              required={!meta.data?.has_secret}
              hint={meta.data?.has_secret ? "Stored encrypted and never shown. Leave blank to keep it." : "Stored encrypted on this NEWSWIRE."}
            >
              <Input
                id="meta-app-secret"
                type="password"
                value={secret}
                onChange={(e) => setSecret(e.target.value)}
                placeholder={meta.data?.has_secret ? "•••••••• (unchanged)" : ""}
                autoComplete="new-password"
                className="font-mono text-xs"
              />
            </Field>
            <details className="text-xs text-muted sm:col-span-2">
              <summary className="cursor-pointer">Advanced: Facebook Login for Business configuration IDs</summary>
              <div className="mt-2 grid gap-3 sm:grid-cols-2">
                <Field label="Facebook configuration ID" htmlFor="meta-fb-config" hint="Only if your app type requires it.">
                  <Input id="meta-fb-config" value={fbConfig} onChange={(e) => setFbConfig(e.target.value)} className="font-mono text-xs" />
                </Field>
                <Field label="Instagram configuration ID" htmlFor="meta-ig-config" hint="Only if your app type requires it.">
                  <Input id="meta-ig-config" value={igConfig} onChange={(e) => setIgConfig(e.target.value)} className="font-mono text-xs" />
                </Field>
              </div>
            </details>
            {error ? (
              <InlineAlert tone="danger" className="sm:col-span-2">
                {error}
              </InlineAlert>
            ) : null}
            <div className="sm:col-span-2">
              <Button type="submit" variant="primary" loading={save.isPending}>
                Save Meta app
              </Button>
            </div>
          </form>
        )}
      </CardBody>
    </Card>
  );
}
