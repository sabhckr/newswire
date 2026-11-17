"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { KeyRound, Wand2 } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { Field, Input } from "@/components/ui/input";
import { InlineAlert } from "@/components/ui/states";
import { Switch } from "@/components/ui/switch";
import { api, ApiError, errorMessage } from "@/lib/api";
import type { DestinationIn, DestinationOut, DestinationUpdateIn } from "@/lib/types";

type Kind = "wordpress" | "webhook";
type Form = {
  kind: Kind;
  name: string;
  base_url: string;
  username: string;
  secret: string;
  is_active: boolean;
  is_commercial: boolean;
  auto_publish: boolean;
};

function initial(d: DestinationOut | null): Form {
  return {
    kind: (d?.kind === "webhook" ? "webhook" : "wordpress") as Kind,
    name: d?.name ?? "",
    base_url: d?.base_url ?? "",
    username: d?.username ?? "",
    secret: "",
    is_active: d?.is_active ?? true,
    is_commercial: d?.is_commercial ?? true,
    auto_publish: d?.auto_publish ?? false,
  };
}

function randomSecret(): string {
  const bytes = new Uint8Array(32);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

/** Create or edit a destination. Secrets are write-only: the API only reports whether one is stored. */
export function DestinationDialog({
  open,
  onOpenChange,
  destination,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  destination: DestinationOut | null;
}) {
  const qc = useQueryClient();
  const [f, setF] = useState<Form>(() => initial(destination));
  const [errors, setErrors] = useState<Partial<Record<keyof Form, string>>>({});
  useEffect(() => {
    if (open) {
      setF(initial(destination));
      setErrors({});
    }
  }, [open, destination]);

  const editing = !!destination;
  const builtin = destination?.kind === "builtin";
  const kind: string = destination?.kind ?? f.kind;
  const set = <K extends keyof Form>(k: K, v: Form[K]) => setF((x) => ({ ...x, [k]: v }));

  const save = useMutation({
    mutationFn: async () => {
      if (!destination) {
        const body: DestinationIn = {
          name: f.name.trim(),
          kind: f.kind,
          base_url: f.base_url.trim(),
          username: f.kind === "wordpress" ? f.username.trim() || null : null,
          secret: f.secret.trim() || null,
          is_active: f.is_active,
          is_commercial: f.is_commercial,
          auto_publish: f.auto_publish,
        };
        return api<DestinationOut>("/publishing/destinations", { body });
      }
      const body: DestinationUpdateIn = {};
      if (f.name.trim() !== destination.name) body.name = f.name.trim();
      if (f.is_active !== destination.is_active) body.is_active = f.is_active;
      if (f.is_commercial !== destination.is_commercial) body.is_commercial = f.is_commercial;
      if (f.auto_publish !== destination.auto_publish) body.auto_publish = f.auto_publish;
      if (!builtin) {
        if (f.base_url.trim() !== (destination.base_url ?? "")) body.base_url = f.base_url.trim();
        if (kind === "wordpress" && f.username.trim() !== (destination.username ?? "")) body.username = f.username.trim() || null;
        if (f.secret.trim()) body.secret = f.secret.trim();
      }
      return api<DestinationOut>(`/publishing/destinations/${destination.id}`, { method: "PUT", body });
    },
    onSuccess: (d) => {
      toast.success(editing ? `Saved ${d.name}` : `Added ${d.name}. Test the connection before publishing.`);
      qc.invalidateQueries({ queryKey: ["publishing-destinations"] });
      onOpenChange(false);
    },
    onError: (e) => {
      if (e instanceof ApiError && e.code === "unsafe_url") setErrors({ base_url: e.message });
      else toast.error(errorMessage(e));
    },
  });

  const submit = () => {
    const errs: Partial<Record<keyof Form, string>> = {};
    if (!f.name.trim()) errs.name = "Give the destination a name";
    if (!builtin) {
      let ok = false;
      try {
        const u = new URL(f.base_url.trim());
        ok = u.protocol === "https:" || u.protocol === "http:";
      } catch {
        ok = false;
      }
      if (!ok) errs.base_url = "Enter a full URL starting with https://";
      if (kind === "wordpress" && !f.username.trim()) errs.username = "WordPress needs the username that owns the application password";
      if (kind === "wordpress" && !editing && !f.secret.trim()) errs.secret = "Paste a WordPress application password";
    }
    setErrors(errs);
    if (!Object.keys(errs).length) save.mutate();
  };

  const secretLabel = kind === "wordpress" ? "Application password" : "Signing secret";
  const secretHint = editing && destination?.has_secret
    ? "A credential is stored and never shown again. Leave blank to keep it, or enter a new one to replace it."
    : kind === "wordpress"
      ? "Create one in WordPress under Users → Profile → Application Passwords. It is stored encrypted."
      : "Used to sign every request (HMAC-SHA256). Copy it to your receiving site now: it cannot be shown again.";

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        title={editing ? `Edit ${destination?.name}` : "Add a publishing destination"}
        description={builtin ? "The built-in site serves the public API and RSS feed. Only its name and flags can change." : undefined}
        footer={
          <>
            <Button variant="ghost" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button variant="primary" loading={save.isPending} onClick={submit}>
              {editing ? "Save" : "Add destination"}
            </Button>
          </>
        }
      >
        <form
          className="flex flex-col gap-3"
          onSubmit={(e) => {
            e.preventDefault();
            submit();
          }}
        >
          {!editing ? (
            <fieldset className="flex flex-col gap-1.5">
              <legend className="mb-1 text-xs font-medium">Type</legend>
              {(
                [
                  ["wordpress", "WordPress site", "Creates posts through the WordPress REST API with an application password."],
                  ["webhook", "Webhook (custom website)", "POSTs signed JSON for each publish, update and unpublish."],
                ] as const
              ).map(([k, label, help]) => (
                <label key={k} className="flex cursor-pointer items-start gap-2 rounded border border-border p-2 text-sm has-[:checked]:border-accent">
                  <input type="radio" name="dest-kind" value={k} checked={f.kind === k} onChange={() => set("kind", k)} className="mt-1 accent-[rgb(var(--accent))]" />
                  <span>
                    <span className="font-medium">{label}</span>
                    <span className="block text-xs text-muted">{help}</span>
                  </span>
                </label>
              ))}
            </fieldset>
          ) : null}

          <Field label="Name" htmlFor="dest-name" required error={errors.name}>
            <Input id="dest-name" value={f.name} onChange={(e) => set("name", e.target.value)} placeholder="e.g. Main WordPress site" aria-invalid={!!errors.name} />
          </Field>

          {!builtin ? (
            <>
              <Field
                label={kind === "wordpress" ? "Site address" : "Endpoint URL"}
                htmlFor="dest-url"
                required
                error={errors.base_url}
                hint={kind === "wordpress" ? "The site root, e.g. https://news.example.com. NEWSWIRE calls /wp-json/wp/v2/posts." : "NEWSWIRE POSTs JSON here. Private network addresses are refused."}
              >
                <Input id="dest-url" type="url" value={f.base_url} onChange={(e) => set("base_url", e.target.value)} placeholder="https://" aria-invalid={!!errors.base_url} />
              </Field>
              {kind === "wordpress" ? (
                <Field label="WordPress username" htmlFor="dest-user" required error={errors.username}>
                  <Input id="dest-user" value={f.username} onChange={(e) => set("username", e.target.value)} autoComplete="off" aria-invalid={!!errors.username} />
                </Field>
              ) : null}
              <Field label={secretLabel} htmlFor="dest-secret" required={kind === "wordpress" && !editing} error={errors.secret} hint={secretHint}>
                <div className="flex gap-2">
                  <Input
                    id="dest-secret"
                    type={kind === "wordpress" ? "password" : "text"}
                    value={f.secret}
                    onChange={(e) => set("secret", e.target.value)}
                    autoComplete="new-password"
                    spellCheck={false}
                    className="font-mono text-xs"
                    placeholder={editing && destination?.has_secret ? "•••••••• (unchanged)" : ""}
                    aria-invalid={!!errors.secret}
                  />
                  {kind === "webhook" ? (
                    <Button type="button" variant="outline" onClick={() => set("secret", randomSecret())}>
                      <Wand2 className="h-4 w-4" /> Generate
                    </Button>
                  ) : null}
                </div>
              </Field>
              {editing && destination?.has_secret ? (
                <p className="-mt-1 inline-flex items-center gap-1.5 text-xs text-muted">
                  <KeyRound className="h-3.5 w-3.5" aria-hidden /> Credential stored
                </p>
              ) : null}
            </>
          ) : null}

          <div className="flex flex-col gap-3 rounded-lg border border-border p-3">
            <div>
              <Switch id="dest-active" label="Active" checked={f.is_active} onCheckedChange={(v) => set("is_active", v)} />
              <p className="ml-10 text-xs text-muted">Inactive destinations are hidden from the publish panel.</p>
            </div>
            <div>
              <Switch id="dest-commercial" label="Commercial publication" checked={f.is_commercial} onCheckedChange={(v) => set("is_commercial", v)} />
              <p className="ml-10 text-xs text-muted">
                Leave on if the site carries ads, paywalls or sponsorship. Images under a NonCommercial (NC) licence are then blocked here.
                Turn off only for a genuinely non-commercial site.
              </p>
            </div>
            <div>
              <Switch id="dest-auto" label="Allow auto-publish" checked={f.auto_publish} onCheckedChange={(v) => set("auto_publish", v)} />
              <p className="ml-10 text-xs text-muted">
                Approved articles go out here without a manual publish, but only while the global auto-publish policy is also on.
              </p>
            </div>
            {f.auto_publish ? (
              <InlineAlert tone="warning" title="Articles may go live as soon as they are approved">
                If the global policy is on, approving an article publishes it here automatically, provided it passes every blocking check.
                Keep this off unless approvals are always final.
              </InlineAlert>
            ) : null}
          </div>
          <button type="submit" hidden aria-hidden tabIndex={-1} />
        </form>
      </DialogContent>
    </Dialog>
  );
}
